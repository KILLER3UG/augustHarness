"""Session messages domain (hot path for chat open / pagination).

Backed by the per-session transcript files under ``<dataDir>/transcripts/``
(one JSONL line per message row — same row dict the ``messages`` table kept),
read and written through the primitives in ``sessions.py``. The dedupe and
enrichment semantics (migrations 047/048) are unchanged; only the engine
underneath moved from SQLite transactions to load-modify-atomic-replace.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from typing import cast

from app.json_narrowing import as_int, as_str
from app.services.filestore import TRANSCRIPTS_DIR, home
from app.services.memory_store import sessions as _sessions
from app.services.memory_store.transcript_blocks import (
    MAX_ENRICHMENT_BYTES,
    decode_blocks,
    encode_blocks,
    fit_enrichment,
    sanitize_enrichment,
)
from app.services.memory_store.wire import _json, _row_as_wire
from app.type_aliases import JsonValue, MessageDict

# Longest client identity accepted. The desktop mints `m<epoch>` / `a<epoch>`;
# the bound only exists so a hostile body cannot stuff a megabyte into an
# indexed column.
MAX_CLIENT_MESSAGE_ID_CHARS = 200

# Search cache: per-session parsed rows keyed by the transcript file's stat,
# so repeated searches over a cold store do not re-read every file per hit.
_SEARCH_CACHE: dict[str, tuple[tuple[int, int], float, list[dict[str, object]]]] = {}
_SEARCH_CACHE_LOCK = threading.Lock()
_SEARCH_CACHE_TTL_S = 5.0


def normalize_client_message_id(value: object) -> str | None:
    """Trim + bound a client message id. None when absent/unusable."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed[:MAX_CLIENT_MESSAGE_ID_CHARS]


def _scrub_persisted(value: object) -> object:
    """Remove this install's credential values before they reach the file.

    Tool arguments are persisted even when the tool call was DENIED, and the
    outbound scrubber only covers provider traffic. A key that rides into a
    tool-call argument would otherwise be durable in the transcript and
    re-read into later turns. Matched by value, never pattern, so ordinary
    prose is untouched.
    """
    try:
        from app.services.hooks.secret_guard import scrubOutboundBody

        return scrubOutboundBody(value)
    except Exception:  # noqa: BLE001 — a scrub failure must not block a message write
        return value


def _structured_source(
    role: str, content: JsonValue, blocks: JsonValue | None
) -> dict[str, object]:
    source: dict[str, object] = {'role': role, 'content': content}
    if isinstance(blocks, list):
        source['blocks'] = blocks
    elif isinstance(blocks, dict):
        for key, value in blocks.items():
            source[key] = value
    scrubbed_source = _scrub_persisted(source)
    if isinstance(scrubbed_source, dict):
        return scrubbed_source
    return source


def save_message(
    sessionId: str,
    role: str,
    content: JsonValue,
    blocks: JsonValue | None = None,
    client_message_id: str | None = None,
) -> int:
    """Append a message to a session's transcript file.

    ``blocks`` is the optional structured transcript payload (migration 047):
    a block list, or an object of structured fields. Stored as JSON in the
    row's ``blocks_json``; ``content`` stays the searchable text.

    ``client_message_id`` (migration 048) is the desktop's own message id.
    Prefer ``upsert_client_message`` when the id is set — a plain append can
    produce a second row for a replayed client message.

    Returns the new message id (the AUTOINCREMENT stand-in).
    """
    row: dict[str, object] = {
        'session_id': sessionId,
        'role': role,
        'content': _scrub_persisted(_json(content)),
        'blocks_json': encode_blocks(_structured_source(role, content, blocks)),
        'client_message_id': normalize_client_message_id(client_message_id),
        'source': None,
    }
    return _sessions.append_message_row(row)


def upsert_client_message(
    session_id: str,
    client_message_id: str,
    role: str,
    content: JsonValue,
    blocks: JsonValue | None = None,
) -> dict[str, object]:
    """Idempotently write a client-authored message (migration 048).

    Three-step reconciliation, in order, so a replayed POST converges on ONE
    row instead of appending a duplicate bubble to the transcript:

    1. the row already carries this ``client_message_id`` -> update it
       (same id is returned, so the client keeps its identity);
    2. an unclaimed row with the same role + content exists -> ADOPT it by
       stamping the client id on it. This is the case that matters: the
       workbench loop already wrote the user/assistant turn, and inserting
       again would double every bubble in the restored chat;
    3. otherwise append a new row.

    Returns ``{id, status}`` with status in ``created`` / ``updated`` /
    ``adopted``.
    """
    cid = normalize_client_message_id(client_message_id)
    if not cid:
        raise ValueError('client_message_id required')
    content_text = _scrub_persisted(_json(content))
    encoded = encode_blocks(_structured_source(role, content, blocks))

    with _sessions._LOCK:
        rows = _sessions.read_message_rows(session_id)
        for row in rows:
            if row.get('client_message_id') == cid:
                mid = as_int(row.get('id'), 0)
                row['role'] = role
                row['content'] = content_text
                if encoded is not None:
                    row['blocks_json'] = encoded
                _sessions.write_message_rows(session_id, rows)
                return {'id': mid, 'status': 'updated'}

        for row in rows:
            if (
                not row.get('client_message_id')
                and row.get('role') == role
                and row.get('content') == content_text
            ):
                mid = as_int(row.get('id'), 0)
                row['client_message_id'] = cid
                if encoded is not None:
                    row['blocks_json'] = encoded
                _sessions.write_message_rows(session_id, rows)
                return {'id': mid, 'status': 'adopted'}

    new_row: dict[str, object] = {
        'session_id': session_id,
        'role': role,
        'content': content_text,
        'blocks_json': encoded,
        'client_message_id': cid,
        'source': None,
    }
    mid = _sessions.append_message_row(new_row)
    return {'id': mid, 'status': 'created'}


def enrich_client_message(
    session_id: str,
    client_message_id: str,
    blocks: object = None,
    structured: object = None,
) -> dict[str, object]:
    """Write the client-authored structured payload onto an existing row.

    Idempotent by construction: an identical payload is a NO-OP
    (``unchanged: True``) rather than a redundant write, so a debounced client
    can re-send freely. Only ``blocks_json`` is written — ``content`` is never
    touched, which is what keeps the row's searchable text intact.

    Returns ``{ok, id, status, reason?}``. ``ok: False`` carries a ``reason``
    the route maps onto a status code:
      * ``not_found``  — no row for this client id in this session
      * ``empty``      — nothing allow-listed in the payload
      * ``too_large``  — even the reduced payload exceeds the byte budget
    """
    cid = normalize_client_message_id(client_message_id)
    if not cid:
        return {'ok': False, 'reason': 'not_found'}
    fields = sanitize_enrichment(blocks, structured)
    if not fields:
        return {'ok': False, 'reason': 'empty'}
    scrubbed_fields = _scrub_persisted(fields)
    if isinstance(scrubbed_fields, dict):
        fields = scrubbed_fields
    fitted = fit_enrichment(fields)
    if fitted is None:
        return {'ok': False, 'reason': 'too_large'}
    encoded = json.dumps(fitted, ensure_ascii=False, default=str)

    with _sessions._LOCK:
        rows = _sessions.read_message_rows(session_id)
        for row in rows:
            if row.get('client_message_id') != cid:
                continue
            mid = as_int(row.get('id'), 0)
            current = row.get('blocks_json')
            # Compare decoded payloads, not the raw string: key order or a
            # re-serialization of the same timeline must not re-write the row.
            if decode_blocks(current) == decode_blocks(encoded):
                return {'ok': True, 'id': mid, 'status': 'ok', 'unchanged': True}
            row['blocks_json'] = encoded
            _sessions.write_message_rows(session_id, rows)
            return {'ok': True, 'id': mid, 'status': 'ok', 'unchanged': False}
    return {'ok': False, 'reason': 'not_found'}


def enrichment_byte_budget() -> int:
    """Exposed for the route/tests — the per-message byte cap (048)."""
    return MAX_ENRICHMENT_BYTES


def get_messages(
    sessionId: str,
    limit: int | None = None,
    offset: int = 0,
    before_id: int | None = None,
) -> list[MessageDict]:
    """Get messages for a session, with optional pagination.

    Default (no limit) returns all messages — historical API contract.
    Pass ``limit`` / ``offset`` / ``before_id`` for paged loads.
    """
    rows = _sessions.read_message_rows(sessionId)
    if before_id is not None:
        older = [r for r in rows if as_int(r.get('id'), 0) < before_id]
        window = older[-int(limit):] if limit is not None and limit > 0 else older
    else:
        start = int(offset) if offset and offset > 0 else 0
        if limit is not None and limit > 0:
            window = rows[start : start + int(limit)]
        else:
            window = rows[start:]
    results: list[MessageDict] = []
    for r in window:
        msg = cast(MessageDict, _row_as_wire(r))
        try:
            msg['content'] = json.loads(msg['content']) if isinstance(msg['content'], str) else msg['content']
        except (json.JSONDecodeError, TypeError):
            pass
        # 047: merge the structured transcript payload (blocks + thinking /
        # tools / attachments / todos …) onto the wire message. A missing
        # blocks_json is a legacy text-only row and stays exactly as it was.
        wire = cast('dict[str, object]', msg)
        structured = decode_blocks(wire.pop('blocksJson', None))
        if structured:
            wire.update(structured)
        results.append(msg)
    return results


def count_messages(sessionId: str) -> int:
    """Count messages for a session (pagination helpers)."""
    return len(_sessions.read_message_rows(sessionId))


async def get_messages_async(
    sessionId: str,
    limit: int | None = None,
    offset: int = 0,
    before_id: int | None = None,
) -> list[MessageDict]:
    """Async wrapper: run the file read on a worker thread.

    Avoids blocking the asyncio event loop during message list loads.
    """

    return await asyncio.to_thread(
        get_messages,
        sessionId,
        limit=limit,
        offset=offset,
        before_id=before_id,
    )


def delete_session_messages(sessionId: str) -> int:
    """Delete all messages for a session."""
    with _sessions._LOCK:
        rows = _sessions.read_message_rows(sessionId)
        _sessions.write_message_rows(sessionId, [])
        return len(rows)


# ── Transcript search (the messages_fts replacement) ────────────────────────


def _cached_rows(session_id: str) -> list[dict[str, object]]:
    """Parsed rows for one session, cached briefly against the file's stat.

    The search route touches every transcript; without this each keystroke
    of a search re-reads every file. Keyed on (size, mtime) so an edit by
    this process or another invalidates immediately; the TTL only bounds
    stat() traffic.
    """
    path = _sessions._session_file(session_id)
    try:
        stat = path.stat()
        stamp = (stat.st_size, int(stat.st_mtime * 1000))
    except OSError:
        stamp = (0, 0)
    now = time.monotonic()
    with _SEARCH_CACHE_LOCK:
        hit = _SEARCH_CACHE.get(session_id)
        if hit and hit[0] == stamp and now - hit[1] < _SEARCH_CACHE_TTL_S:
            return hit[2]
    rows = _sessions.read_message_rows(session_id)
    with _SEARCH_CACHE_LOCK:
        # Keep the cache small: a store with thousands of sessions must not
        # pin every transcript in memory.
        if len(_SEARCH_CACHE) > 64:
            _SEARCH_CACHE.clear()
        _SEARCH_CACHE[session_id] = (stamp, now, rows)
    return rows


def _searchable_text(content: object) -> str:
    """The text a search should match: the decoded message content."""
    if isinstance(content, str):
        stripped = content.strip()
        if len(stripped) > 1 and stripped[0] == '"' and stripped[-1] == '"':
            try:
                decoded = json.loads(stripped)
            except (json.JSONDecodeError, TypeError, ValueError):
                decoded = None
            if isinstance(decoded, str):
                return decoded
        return content
    if isinstance(content, dict):
        text = content.get('content')
        if isinstance(text, str):
            return text
        return json.dumps(content, ensure_ascii=False, default=str)
    if content is None:
        return ''
    return str(content)


def _make_snippet(text: str, terms: list[str], width: int = 240) -> str:
    """A snippet with each matched term wrapped in [brackets].

    The FTS5 ``snippet()`` marker shape the old route returned ('[' / ']' /
    '…'), cut on a word boundary near the first hit.
    """
    lowered = text.lower()
    first = len(text)
    for term in terms:
        found = lowered.find(term)
        if 0 <= found < first:
            first = found
    start = max(0, first - width // 3)
    if start > 0:
        # Start on a whitespace boundary so a word is not cut mid-letter.
        space = text.find(' ', start)
        if 0 <= space < first:
            start = space + 1
    window = text[start : start + width * 2]
    uniq = sorted({t for t in terms if t}, key=len, reverse=True)
    if uniq:
        pattern = re.compile('|'.join(re.escape(t) for t in uniq), re.IGNORECASE)
        window = pattern.sub(lambda m: f'[{m.group(0)}]', window)
    snippet = window.strip()
    if len(snippet) > width:
        snippet = snippet[:width].rstrip() + '…'
    elif start > 0:
        snippet = '…' + snippet
    return snippet or text[:width]


def search_messages(
    query: str,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Search every transcript for messages matching ``query``.

    The messages_fts index died with SQLite; this is the plain-Python
    replacement: case-insensitive substring per term, scored by how many
    query terms are present, ties broken newest-first. One best hit per
    session, in the same ``{sessionId, title, role, snippet, messageId}``
    shape the old route returned.
    """
    terms = [t.lower() for t in query.split() if t.strip()]
    if not terms:
        return []
    sessions_meta = _sessions._load_session_rows()
    try:
        transcript_dir = home(TRANSCRIPTS_DIR)
        session_ids = sorted(
            p.stem for p in transcript_dir.glob('*.jsonl')
        )
    except OSError:
        session_ids = []

    hits: list[tuple[int, int, dict[str, object]]] = []
    for sid in session_ids:
        original_sid = sid
        # A sanitised file name may map back to a different session id; the
        # digest suffix marks that case. Session metadata stays keyed by the
        # original id, so prefer the recorded rows' session_id when present.
        rows = _cached_rows(original_sid)
        if not rows:
            continue
        stored_sid = as_str(rows[0].get('session_id'), '') or original_sid
        best_score = 0
        best: dict[str, object] | None = None
        best_id = 0
        # Newest first: walk the file from the end.
        for row in reversed(rows):
            text = _searchable_text(row.get('content'))
            if not text:
                continue
            lowered = text.lower()
            score = sum(1 for t in terms if t in lowered)
            if score == 0:
                continue
            if best is None or score >= best_score:
                best = row
                best_score = score
                best_id = as_int(row.get('id'), 0)
        if best is None:
            continue
        title = as_str((sessions_meta.get(stored_sid) or {}).get('title'), '')
        hits.append(
            (
                best_score,
                best_id,
                {
                    'sessionId': stored_sid,
                    'title': title or 'Untitled conversation',
                    'role': as_str(best.get('role'), ''),
                    'snippet': _make_snippet(
                        _searchable_text(best.get('content')), terms
                    ),
                    'messageId': best_id,
                },
            )
        )
    # Most terms matched first, then the newest message.
    hits.sort(key=lambda h: (-h[0], -h[1]))
    return [h[2] for h in hits[: max(1, min(limit, 100))]]
