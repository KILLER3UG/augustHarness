"""Sessions domain — workbench sessions and messages in transcript files.

The SQLite ``sessions`` / ``messages`` tables became two file homes under
``dataDir()``:

* ``<dataDir>/transcripts/<sessionId>.jsonl`` — one JSON line per message,
  carrying the same per-message dict the ``messages`` table row held (same
  column-name spellings, ``blocks_json`` included), so the wire shapes the
  desktop reads never moved.
* ``<dataDir>/transcripts/sessions.json`` — a dict keyed by sessionId with
  the same fields the ``sessions`` row carried (snake_case; the wire
  camelCase conversion happens at read time via ``_row_as_wire``).

The delete-and-reinsert-in-a-transaction pattern became
load-modify-atomic-replace: ``save_workbench_session_sot`` loads the current
JSONL, applies the message list and rewrites the file through
``filestore.rewrite_jsonl`` (temp file + ``os.replace``). That is the same
guarantee the ``BEGIN IMMEDIATE`` transaction gave — a crash mid-write
leaves the previous transcript intact — without a database.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import cast

from app.json_narrowing import as_int, as_str
from app.services.filestore import (
    STATE_DIR,
    TRANSCRIPTS_DIR,
    append_jsonl,
    home,
    load_jsonl,
    read_json,
    rewrite_jsonl,
    write_json,
)
from app.services.memory_store.transcript_blocks import encode_blocks
from app.services.memory_store.wire import _row_as_wire, _session_field
from app.type_aliases import SessionRecord

# One lock serialises read-modify-write cycles on the transcript files.
# Writers are this backend's own threads; the atomic-replace rule makes each
# whole-file write all-or-nothing and the lock makes the read side of a
# read-modify-write see a complete file.
_LOCK = threading.RLock()

_SESSIONS_FILE = 'sessions.json'
# Monotonic message-id counter (the ``messages.id`` AUTOINCREMENT stand-in).
# Ids only have to be unique and non-decreasing within a session, but a global
# counter keeps every property the old column had, cheaply.
_SEQ_FILE = 'transcript-seq.json'
_SEQ_KEY = 'next'

_UNSAFE_NAME_CHARS = re.compile(r'[^A-Za-z0-9._-]')


def _session_file(session_id: str) -> Path:
    """The JSONL file for one session's transcript (created on demand)."""
    safe = _UNSAFE_NAME_CHARS.sub('_', session_id) or '_'
    if safe != session_id:
        # Characters were replaced: add a short digest so two hostile ids can
        # never sanitise onto the same file.
        digest = hashlib.sha1(session_id.encode('utf-8')).hexdigest()[:8]
        safe = f'{safe[:80]}__{digest}'
    return home(TRANSCRIPTS_DIR) / f'{safe}.jsonl'


def _sessions_path() -> Path:
    return home(TRANSCRIPTS_DIR) / _SESSIONS_FILE


def _load_session_rows() -> dict[str, dict[str, object]]:
    raw = read_json(_sessions_path(), default={})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, object]] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, dict):
            out[key] = value
    return out


def _save_session_rows(rows: dict[str, dict[str, object]]) -> None:
    write_json(_sessions_path(), rows)


def _now_stamp() -> str:
    """The ``datetime('now')`` shape the old rows carried (UTC, seconds)."""
    return time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())


def _load_rows(session_id: str) -> list[dict[str, object]]:
    rows = load_jsonl(_session_file(session_id))
    return [r for r in rows if isinstance(r, dict)]


def _write_rows(session_id: str, rows: list[dict[str, object]]) -> None:
    rewrite_jsonl(_session_file(session_id), cast('list[object]', rows))


def _max_row_id(rows: list[dict[str, object]]) -> int:
    best = 0
    for row in rows:
        value = as_int(row.get('id'), 0)
        if value > best:
            best = value
    return best


def _allocate_ids(count: int, floor: int = 0) -> list[int]:
    """``count`` fresh AUTOINCREMENT-style ids, never below ``floor`` + 1.

    The counter file keeps the value across restarts so a rewritten
    transcript never hands a new message an id an older row already used.
    """
    with _LOCK:
        counter = read_json(home(STATE_DIR) / _SEQ_FILE, default={})
        nxt = as_int(counter.get(_SEQ_KEY) if isinstance(counter, dict) else None, 1)
        nxt = max(nxt, floor + 1, 1)
        ids = list(range(nxt, nxt + max(count, 0)))
        if ids:
            write_json(home(STATE_DIR) / _SEQ_FILE, {_SEQ_KEY: ids[-1] + 1})
        return ids


def save_session(session: SessionRecord) -> None:
    """Persist a session record. Accepts camelCase wire keys (or snake_case)."""
    # Dual-read: wire camelCase or snake_case
    sid = as_str(session.get('id'), '')
    if not sid:
        return
    title = _session_field(session, 'title', '')
    started_at = _session_field(session, 'startedAt')
    message_count = _session_field(session, 'messageCount', 0)
    provider = _session_field(session, 'provider', '')
    model = _session_field(session, 'model', '')
    folder_id = _session_field(session, 'folderId')
    is_archived = _session_field(session, 'isArchived')
    workspace_path = _session_field(session, 'workspacePath')
    blob = session.get('workbenchBlob') or session.get('workbench_blob')
    updated_at = _session_field(session, 'updatedAt') or started_at
    with _LOCK:
        rows = _load_session_rows()
        stored = rows.get(sid)
        # Preserve the stored blob / archive flag when the caller only updates
        # metadata: an unrelated metadata write used to reset the archive flag
        # to 0, silently un-archiving a session the user parked.
        if blob is None and stored is not None:
            blob = stored.get('workbench_blob')
        if is_archived is None:
            if stored is not None:
                is_archived = bool(as_int(stored.get('is_archived'), 0))
            else:
                is_archived = False
        rows[sid] = {
            'id': sid,
            'title': title or '',
            'started_at': started_at,
            'message_count': message_count if message_count is not None else 0,
            'provider': provider or '',
            'model': model or '',
            'folder_id': folder_id,
            'is_archived': 1 if is_archived else 0,
            'workspace_path': workspace_path,
            'workbench_blob': (
                blob
                if isinstance(blob, str)
                else (json.dumps(blob) if blob is not None else None)
            ),
            'updated_at': updated_at,
        }
        _save_session_rows(rows)


# Client-identity match window for the snapshot rewrite (migration 048). The
# workbench transcript and the desktop transcript are the same conversation
# seen from two sides, so the same message can differ in its TAIL: the desktop
# sends the typed text while the backend row carries the appended @git snapshot
# / bot-mention note, and a mid-turn save catches a partially streamed
# assistant reply. Both differences append, so the LEADING window identifies
# the message while a full-text comparison does not. 160 chars is long enough
# that an unrelated message must open identically to be adopted.
_CLIENT_MATCH_PREFIX = 160


def _normalize_text(content: object) -> str:
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    # The two writers spell a text message differently: the POST path stores
    # ``_json(content)`` (a JSON string, quotes included) while the snapshot
    # writer stores the bare string. Unwrap one so the pair can be compared.
    stripped = text.strip()
    if len(stripped) > 1 and stripped[0] == '"' and stripped[-1] == '"':
        try:
            decoded = json.loads(stripped)
        except (json.JSONDecodeError, TypeError, ValueError):
            decoded = None
        if isinstance(decoded, str):
            text = decoded
    return ' '.join(text.split())


def _same_message(a: object, b: object) -> bool:
    """True when two views of one message agree over the leading window.

    Either side may be the longer one: the desktop's text is a prefix of the
    backend's row (injected @git / bot notes), and a streamed assistant reply
    is a prefix of the finalized one.
    """
    left = _normalize_text(a)
    right = _normalize_text(b)
    if not left or not right:
        return False
    if left[:_CLIENT_MATCH_PREFIX] == right[:_CLIENT_MATCH_PREFIX]:
        return True
    if len(left) <= _CLIENT_MATCH_PREFIX and right.startswith(left):
        return True
    return len(right) <= _CLIENT_MATCH_PREFIX and left.startswith(right)


def _claim_client_rows(
    rows: list[dict[str, object]],
    sid: str,
) -> dict[str, list[dict[str, object]]]:
    """Existing client-authored rows of this session, grouped by role, in order.

    Only rows the desktop has claimed (``client_message_id`` set) take part:
    a server-derived row must never be adopted, or an enrichment PATCH could
    land on a message the client never authored.
    """
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        if row.get('session_id') != sid:
            continue
        client_id = row.get('client_message_id')
        if not client_id:
            continue
        entry = {
            'id': row.get('id'),
            'role': row.get('role'),
            'content': row.get('content'),
            'clientMessageId': client_id,
            'blocksJson': row.get('blocks_json'),
            'createdAt': row.get('created_at'),
        }
        grouped.setdefault(as_str(entry['role'], 'user'), []).append(entry)
    return grouped


def _claim_client_row(
    bucket: dict[str, list[dict[str, object]]],
    role: str,
    content: object,
) -> dict[str, object] | None:
    """Take the earliest unclaimed client row that is the same message."""
    candidates = bucket.get(role)
    if not candidates:
        return None
    for index, entry in enumerate(candidates):
        if _same_message(entry['content'], content):
            return candidates.pop(index)
    return None


def save_workbench_session_sot(
    session_dict: dict[str, object],
    messages: list[dict[str, object]] | None = None,
) -> None:
    """Write session metadata, full blob, and messages as one atomic file write.

    This is the primary workbench save path, called three times per tool round
    through the durability barriers in ``workbench/durability.py``. Optional
    JSON file export happens outside this function.

    Migration 048 semantics survive the engine swap: client-authored identity
    and ``blocks_json`` are carried across the rewrite, so the rich transcript
    a user sees survives the next durability barrier (model dispatch, tool
    step boundary, autosave). A row the snapshot no longer contains IS
    deleted — the backend transcript stays authoritative for removals (Undo,
    clear, regeneration) — and the client re-posts it on its next sync.
    """

    sid = as_str(session_dict.get('id'), '')
    if not sid:
        raise ValueError('session id required')
    title = as_str(session_dict.get('title'), 'Workbench session')
    started = as_str(session_dict.get('startedAt') or session_dict.get('createdAt'), '')
    updated = as_str(session_dict.get('updatedAt'), started)
    msgs = messages if messages is not None else cast(
        list[dict[str, object]], session_dict.get('messages') or []
    )
    if not isinstance(msgs, list):
        msgs = []
    blob = json.dumps(session_dict, ensure_ascii=False, default=str)
    # The workbench session object carries no archive flag — the UI owns it —
    # so writing the dict back used to reset `is_archived` to 0 on the next
    # autosave. Keep the stored value unless this caller states it explicitly.
    explicit_archived = 'isArchived' in session_dict or 'is_archived' in session_dict
    with _LOCK:
        session_rows = _load_session_rows()
        stored_row = session_rows.get(sid)
        if explicit_archived:
            archived = 1 if (
                session_dict.get('isArchived') or session_dict.get('is_archived')
            ) else 0
        else:
            archived = as_int((stored_row or {}).get('is_archived'), 0)
        session_rows[sid] = {
            'id': sid,
            'title': title,
            'started_at': started,
            'message_count': len(msgs) or as_int(session_dict.get('messageCount'), 0),
            'provider': as_str(session_dict.get('provider'), ''),
            'model': as_str(session_dict.get('model'), ''),
            'folder_id': session_dict.get('folderId'),
            'is_archived': archived,
            'workspace_path': as_str(session_dict.get('workspacePath'), ''),
            'workbench_blob': blob,
            'updated_at': updated,
        }
        current_rows = _load_rows(sid)
        client_rows = _claim_client_rows(current_rows, sid)
        ids: list[int] = [
            as_int(row.get('id'), 0)
            for row in current_rows
            if as_int(row.get('id'), 0) > 0
        ]
        new_rows: list[dict[str, object]] = []
        # A duplicate id inside one batch would silently fold two messages
        # into one row; the first claim wins instead.
        seen_client_ids: set[str] = set()
        for msg in msgs:
            if not isinstance(msg, dict):
                continue
            role = as_str(msg.get('role'), 'user')
            content = msg.get('content', '')
            if msg.get('tool_calls') is not None or msg.get('tool_use_id') is not None:
                payload_dict: dict[str, object] = {'content': content}
                for k in ('tool_calls', 'tool_use_id', 'name'):
                    if k in msg:
                        payload_dict[k] = msg[k]
                payload: object = payload_dict
            else:
                payload = content
            content_str = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
            # 049 (audit C3/D7): a tail-patched user message persists as the
            # user's text alone. The per-turn <memory>/<relevant_skills>/
            # <session_state> tail is re-injected fresh every turn, so a
            # persisted copy is stale context the model should not trust (and
            # mining noise). Trim at the recorded boundary — the in-memory
            # surface the current turn still talks on is NOT mutated, so the
            # provider prefix cache stays stable. The marker keys themselves
            # never persist (the upstream dumps strip them per message too).
            if msg.get('_tailPatched') and isinstance(content_str, str):
                tailFrom = as_int(msg.get('_tailFrom'), -1)
                if 0 <= tailFrom < len(content_str):
                    content_str = content_str[:tailFrom].rstrip()
            # Tool arguments ride in `tool_calls` and are persisted even when
            # the call was denied. Scrub this install's credential VALUES here,
            # at the durability boundary, so the transcript file never becomes
            # the durable copy the transport scrub was meant to prevent.
            try:
                from app.services.hooks.secret_guard import scrubOutboundText

                content_str = scrubOutboundText(content_str)
            except Exception:
                pass
            # 047: the structured timeline (blocks / thinking / tools /
            # attachments / todos …) rides beside `content`, which stays the
            # searchable text. None for a message with nothing structured.
            blocks_json = encode_blocks(msg)
            # 048: re-attach the desktop's identity for this message, and keep
            # the timeline it synced when the snapshot itself carries none
            # (the backend's own transcript cannot derive tool cards the
            # desktop rendered).
            client_id = msg.get('clientMessageId') or msg.get('client_message_id')
            claimed: dict[str, object] | None = None
            if isinstance(client_id, str) and client_id.strip():
                client_id = client_id.strip()
                # The snapshot names the identity itself: adopt the existing
                # row's id (and created_at) so the rewrite is an UPDATE, not
                # an identity churn.
                for bucket_row in client_rows.get(role, []):
                    if as_str(bucket_row.get('clientMessageId'), '') == client_id:
                        client_rows.get(role, []).remove(bucket_row)
                        claimed = bucket_row
                        break
            else:
                client_id = None
                claimed = _claim_client_row(client_rows, role, content_str)
                if claimed is not None:
                    client_id = as_str(claimed.get('clientMessageId'), '') or None
            if blocks_json is None and claimed is not None:
                preserved = claimed.get('blocksJson')
                blocks_json = preserved if isinstance(preserved, str) else None
            if client_id is not None and client_id in seen_client_ids:
                client_id = None
            if client_id is not None:
                seen_client_ids.add(client_id)
            # 049: provenance written by the loop's machine-injection sites
            # (harness nudges, queued composites) — see message_sources.py.
            # Untagged rows persist as None (not recorded), never ''.
            source = as_str(msg.get('source'), '') or None
            # A claimed row keeps its existing id (and created_at): the
            # desktop reconciles by clientMessageId, and a stable id makes
            # `before_id` pagination work across saves instead of churning
            # every row on every autosave. Unclaimed rows get fresh ids.
            row_id = as_int(claimed.get('id'), 0) if claimed is not None else 0
            if claimed is not None:
                created_at = as_str(claimed.get('createdAt'), '') or _now_stamp()
            else:
                created_at = _now_stamp()
            new_rows.append({
                'id': row_id,
                'session_id': sid,
                'role': role,
                'content': content_str,
                'blocks_json': blocks_json,
                'client_message_id': client_id,
                'source': source,
                'created_at': created_at,
            })
        # Allocate ids for the rows that were not claimed, in file order.
        needs_id = [r for r in new_rows if not as_int(r.get('id'), 0)]
        fresh = _allocate_ids(len(needs_id), floor=max(ids) if ids else 0)
        for row, row_id in zip(needs_id, fresh):
            row['id'] = row_id
        _write_rows(sid, new_rows)
        _save_session_rows(session_rows)
    # Topic index (deterministic, no LLM): derive a session topic from the
    # title so workflow detection + topic-based cross-session recall have
    # data to work with (previously session_topics was never written).
    try:
        from app.services.memory_store.rest import index_session_topic

        topic = (title or '').strip()[:48]
        if topic:
            index_session_topic(sid, topic)
    except Exception:
        pass


def get_workbench_blob(session_id: str) -> dict[str, object] | None:
    """Load ONE workbench session blob by id, or None.

    Used when a session was pruned from the in-memory map (the snapshot
    keeps only the top-50 by recency) — replying to a pruned session must
    resume the ORIGINAL conversation, not silently create a new one.
    """
    if not session_id:
        return None
    with _LOCK:
        row = _load_session_rows().get(session_id)
    if not row:
        return None
    raw = row.get('workbench_blob')
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) and data.get('id') else None


def list_workbench_blobs(limit: int = 200) -> list[dict[str, object]]:
    """Load workbench session blobs (newest first)."""
    with _LOCK:
        rows = list(_load_session_rows().values())
    rows.sort(
        key=lambda r: (
            as_str(r.get('updated_at'), '') or as_str(r.get('started_at'), '')
        ),
        reverse=True,
    )
    out: list[dict[str, object]] = []
    for row in rows[: max(1, min(limit, 500))]:
        raw = row.get('workbench_blob')
        if not raw:
            continue
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get('id'):
            out.append(cast(dict[str, object], data))
    return out


def session_archive_flags() -> dict[str, bool]:
    """Ids of archived sessions — the cheap read the sidebar needs.

    Deliberately NOT part of ``list_workbench_blobs``: that load drags the
    whole transcript blob for up to 500 sessions, and the only thing the caller
    wants here is a boolean. Only archived rows are returned, so the payload
    stays tiny and an absent id means "not archived" rather than "unknown".
    """
    with _LOCK:
        rows = _load_session_rows()
    return {
        sid: True
        for sid, row in rows.items()
        if as_int(row.get('is_archived'), 0) == 1
    }


def list_sessions() -> list[SessionRecord]:
    """List all sessions, most recent first."""
    with _LOCK:
        rows = list(_load_session_rows().values())
    rows.sort(key=lambda r: as_str(r.get('started_at'), ''), reverse=True)
    return [cast(SessionRecord, _row_as_wire(r)) for r in rows]


def set_session_archived(sessionId: str, archived: bool) -> SessionRecord | None:
    """The single authority for parking a session; None when it does not exist.

    Both the REST surface (``PATCH /api/sessions/{id}``) and the legacy
    ``/api/august/sessions/manage`` action route call this, so the flag cannot
    be written two different ways.
    """
    session = get_session(sessionId)
    if not session:
        return None
    updated = dict(session)
    updated['isArchived'] = bool(archived)
    save_session(cast(SessionRecord, updated))
    return get_session(sessionId)


def get_session(sessionId: str) -> SessionRecord | None:
    """Get a single session by ID."""
    with _LOCK:
        row = _load_session_rows().get(sessionId)
    return cast(SessionRecord, _row_as_wire(row)) if row else None


def list_workspace_paths() -> list[str]:
    """DISTINCT non-empty workspace paths across sessions (cheap read).

    Reader for the skill-promotion judge's "known projects" enumeration —
    the old raw ``SELECT DISTINCT workspace_path FROM sessions``.
    """
    paths: list[str] = []
    seen: set[str] = set()
    for row in _load_session_rows().values():
        ws = str(row.get('workspace_path') or '').strip()
        if ws and ws not in seen:
            seen.add(ws)
            paths.append(ws)
    return paths


# Per-session stores swept by a session delete. The table names survive as
# the ``children`` keys of the delete result; each maps onto the shared file
# home ``memory_store/tables.py`` names (the content domain owns those files'
# writers; this sweep only removes the deleted session's rows).
# ``messages`` is the session's own JSONL; JSONL ledgers are pruned by
# rewriting each file without the session's rows; list-shaped JSON stores are
# filtered the same way. Stores owned by other domains (episodes, proposals,
# blackboard, refine entries, pending skills) are left to their owners' own
# purge paths.
_SESSION_CHILD_FILES: tuple[tuple[str, str, str], ...] = (
    # (result key, store home, file name)
    ('session_topics', 'telemetry', 'session-topics.json'),
    ('lifecycle', 'telemetry', 'lifecycle.jsonl'),
    ('usage_events', 'telemetry', 'usage-events.jsonl'),
    ('tool_guardrail_log', 'telemetry', 'tool-guardrail-log.jsonl'),
    ('turn_outcomes', 'telemetry', 'turn-outcomes.jsonl'),
)

_JSONL_CHILD_FILES = {'lifecycle', 'usage_events', 'tool_guardrail_log', 'turn_outcomes'}


def delete_session_cascade(
    sessionId: str, *, notify: bool = True
) -> dict[str, object]:
    """Delete a session and every per-session row in its files.

    Always sweeps the per-session child stores even when the session record
    is already gone (orphans from prior partial deletes). Returns:
      ``{ok, sessionId, messages, children: {store: n}}``
    ``ok`` is True when a session record or any child row was removed.

    When ``notify`` is True (default), fans out a real-time session-deleted
    event so the desktop sidebar can drop the row immediately.
    """
    sid = as_str(sessionId, '')
    if not sid:
        return {'ok': False, 'sessionId': sid, 'messages': 0, 'children': {}}

    children: dict[str, int] = {}
    with _LOCK:
        # Messages first — the session's own transcript file.
        rows = _load_rows(sid)
        if rows:
            children['messages'] = len(rows)
            _write_rows(sid, [])
        for key, kind, name in _SESSION_CHILD_FILES:
            path = home(kind) / name
            if key in _JSONL_CHILD_FILES:
                entries = load_jsonl(path)
                kept = [
                    e
                    for e in entries
                    if not (
                        isinstance(e, dict) and as_str(e.get('session_id'), '') == sid
                    )
                ]
                removed = len(entries) - len(kept)
                if removed:
                    children[key] = removed
                    rewrite_jsonl(path, kept)
            else:
                # List-shaped JSON store keyed by a session_id field
                # (session-topics, written by rest.index_session_topic).
                raw = read_json(path, default=[])
                if isinstance(raw, list):
                    kept = [
                        e
                        for e in raw
                        if not (
                            isinstance(e, dict)
                            and as_str(e.get('session_id'), '') == sid
                        )
                    ]
                    removed = len(raw) - len(kept)
                    if removed:
                        children[key] = removed
                        write_json(path, kept)
        session_rows = _load_session_rows()
        parent_deleted = sid in session_rows
        if parent_deleted:
            session_rows.pop(sid, None)
            _save_session_rows(session_rows)
        any_child = bool(children)
        ok = parent_deleted or any_child
        if ok and notify:
            try:
                from app.services.workbench.sessions import notify_session_deleted

                notify_session_deleted(sid)
            except Exception:
                pass
        return {
            'ok': ok,
            'sessionId': sid,
            'messages': children.get('messages', 0),
            'children': children,
        }


def delete_session_record(sessionId: str) -> bool:
    """Delete a session and all dependent rows (messages, timeline, …).

    Prefer this over calling ``delete_session_messages`` then this function
    separately — order bugs caused FK failures historically.
    """
    result = delete_session_cascade(sessionId)
    return bool(result.get('ok'))


# ── Message-row primitives shared with the search path ──────────────────────


def append_message_row(row: dict[str, object]) -> int:
    """Append one message row to its session file and return its id.

    The id comes from the global counter, floored above every id already in
    the file so a restored / rewritten transcript can never collide.
    """
    sid = as_str(row.get('session_id'), '')
    if not sid:
        raise ValueError('session id required')
    with _LOCK:
        row_id = as_int(row.get('id'), 0)
        if row_id <= 0:
            row_id = _allocate_ids(1, floor=_max_row_id(_load_rows(sid)))[0]
            row['id'] = row_id
        row.setdefault('created_at', _now_stamp())
        append_jsonl(_session_file(sid), row)
        return row_id


def read_message_rows(session_id: str) -> list[dict[str, object]]:
    """All message rows of one session, in file (conversation) order."""
    with _LOCK:
        return _load_rows(session_id)


def write_message_rows(session_id: str, rows: list[dict[str, object]]) -> None:
    """Atomically replace one session's message rows."""
    with _LOCK:
        _write_rows(session_id, rows)
