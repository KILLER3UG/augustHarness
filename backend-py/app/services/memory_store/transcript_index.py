"""Derived full-text index over the transcripts (a cache, never a record).

The transcripts under ``<dataDir>/transcripts/`` are the record. This module
is a REBUILDABLE index over them, in the same spirit as ``MEMORY.md`` being a
rebuildable index over ``<dataDir>/memory/``: it holds no fact that is not
derivable from the JSONL, so deleting it loses nothing and a corrupt one is
dropped and rebuilt rather than repaired.

Why it exists at all: the SQL engine's ``messages_fts`` died with SQLite, and
the plain-Python replacement scans every transcript per query. That is fine
at fourteen files and stops being fine at the "weeks of sessions" scale the
session-search docs describe, because every keystroke in the search box pays
for a full re-read of the whole corpus. An FTS5 index turns that into an
indexed lookup.

Three rules keep this from becoming the second store the project forbids:

1. **Derived, never authoritative.** The JSONL is the only source of truth.
   Every row here can be regenerated from it.
2. **Rebuildable at any time.** ``rebuild()`` throws the file away and reads
   the transcripts again. ``sync()`` brings it up to date incrementally.
3. **Disappearable without loss.** If ``sqlite3`` is unloadable (Windows Smart
   App Control blocks ``_sqlite3.pyd`` — see ``scripts/dev-app.mjs``), or the
   file is corrupt, or anything at all goes wrong, the caller falls back to
   the plain-Python scan and the product behaves exactly as it did before.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path

from app.json_narrowing import as_int, as_str
from app.services.filestore import STATE_DIR, TRANSCRIPTS_DIR, home

logger = logging.getLogger('august.memory_store.transcript_index')

_INDEX_NAME = 'transcript-index.sqlite'
# Bump when the row shape changes: an older file is rebuilt rather than read
# with a schema it does not understand. v3 is the TRIGRAM tokenizer: the
# scan's semantics are substring, and unicode61 tokenizes on word boundaries,
# so "ploy" did not match "deployment" in the index while it did in the
# scan — the two paths disagreed on the same query.
_SCHEMA_VERSION = 3
# One FTS row per message. Bounded so a single absurd line cannot bloat the
# index; the scan path has no such cap, so this only affects recall of very
# long messages.
_MAX_MESSAGE_CHARS = 20_000
# The trigram tokenizer matches 3-character windows, so a term shorter than
# that cannot be found by it at all. The scan matches any length, so a query
# with a 1- or 2-character term must go to the scan.
_MIN_TRIGRAM_TERM = 3

_LOCK = threading.Lock()
# None = not yet probed; False = this process cannot use the index.
_state: dict[str, object] = {'conn': None, 'available': None}


def _conn() -> sqlite3.Connection | None:
    """The open connection, narrowed for the type checker."""
    conn = _state['conn']
    return conn if isinstance(conn, sqlite3.Connection) else None


def _is_corrupt(exc: BaseException) -> bool:
    """Whether a sqlite failure means "this file is not a usable database"."""
    text = str(exc).lower()
    return 'not a database' in text or 'file is encrypted' in text


def _discard_corrupt(path: Path) -> bool:
    """Delete an unusable index file (plus its WAL sidecars). True if gone.

    The transcripts are the record, so this is a cache eviction, not data loss.
    """
    try:
        for suffix in ('', '-wal', '-shm'):
            Path(str(path) + suffix).unlink(missing_ok=True)
    except OSError:
        return False
    return True


def index_path() -> Path:
    """The derived index file. Lives beside the other derived state."""
    return home(STATE_DIR) / _INDEX_NAME


def available() -> bool:
    """Whether an FTS5 index can be used in this process.

    Probed once. A blocked ``_sqlite3.pyd`` is a per-machine property, not a
    per-call one, so a failure here is remembered rather than retried on every
    keystroke.
    """
    if _state['available'] is not None:
        return bool(_state['available'])
    with _LOCK:
        if _state['available'] is not None:
            return bool(_state['available'])
        ok = _try_open()[0] is not None
        _state['available'] = ok
        if not ok:
            logger.info(
                'transcript index unavailable (sqlite3/FTS5 blocked?) — '
                'falling back to the plain scan',
            )
        return ok


def _connect() -> sqlite3.Connection | None:
    """Open (creating if needed) the index, or None when it cannot be used.

    A file written by an older ``_SCHEMA_VERSION`` is DROPPED rather than read
    with columns it does not have. That is safe precisely because the index is
    derived: the transcripts are the record, so a version bump costs one re-read
    and never a memory.
    """
    conn = _conn()
    if conn is not None:
        return conn
    try:
        path = index_path()
        conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
        conn.execute('pragma journal_mode=wal')
        conn.execute('pragma synchronous=normal')
        stale = False
        try:
            row = conn.execute(
                "select value from meta where key = 'schema_version'"
            ).fetchone()
            if row is not None and as_str(row[0]) != str(_SCHEMA_VERSION):
                stale = True
        except sqlite3.Error:
            # No meta table yet: either a fresh file, or one written before the
            # version stamp existed. The columns decide, because a file with no
            # stamp is exactly the case that would otherwise slip through.
            stale = False
        if not stale:
            try:
                cols = {
                    as_str(r[1])
                    for r in conn.execute('pragma table_info(messages)')
                }
            except sqlite3.Error:
                cols = set()
            if cols and not {'message_id', 'content'} <= cols:
                stale = True
        if stale:
            logger.info(
                'transcript index schema changed — rebuilding from the transcripts',
            )
            conn.execute('drop table if exists messages')
            conn.execute('drop table if exists files')
            conn.execute('drop table if exists meta')
        conn.execute(
            'create virtual table if not exists messages using fts5('
            'session_key, session_id, role, message_id, content, '
            'tokenize="trigram"'
            ')',
        )
        conn.execute(
            'create table if not exists meta ('
            'key text primary key, value text not null'
            ')',
        )
        conn.execute(
            'create table if not exists files ('
            'session_key text primary key, stamp text not null'
            ')',
        )
        conn.execute(
            "insert into meta (key, value) values ('schema_version', ?) "
            "on conflict(key) do update set value = excluded.value",
            (str(_SCHEMA_VERSION),),
        )
        conn.commit()
    except (sqlite3.Error, OSError, ImportError) as exc:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        # ImportError is the blocked-_sqlite3 case (Windows Smart App Control),
        # which is a property of the machine, not of the file: do not retry.
        if isinstance(exc, ImportError):
            return None
        # A file that is not a database (a torn write, a truncated download, a
        # restored backup gone wrong) is discarded rather than disabling the
        # index for the whole process: it is derived, so deleting it costs a
        # re-read and nothing else. Only a genuine "cannot use sqlite at all"
        # is remembered as unavailable.
        if _is_corrupt(exc) and _discard_corrupt(path):
            _state['conn'] = None
            return _connect()
        return None
    _state['conn'] = conn
    return conn


def _try_open() -> tuple[sqlite3.Connection | None, bool]:
    """Open the index and report whether its schema version is current."""
    conn = _connect()
    if conn is None:
        return None, False
    try:
        row = conn.execute(
            "select value from meta where key = 'schema_version'"
        ).fetchone()
    except sqlite3.Error:
        return conn, False
    if row is None:
        return conn, True  # freshly created
    return conn, as_str(row[0]) == str(_SCHEMA_VERSION)


def _stamp(path: Path) -> str:
    """The identity of a transcript file's current contents."""
    try:
        st = path.stat()
    except OSError:
        return ''
    return f'{st.st_size}:{st.st_mtime_ns}'


def searchable_text(content: object) -> str:
    """The text a search should match: the decoded message content.

    Lives here rather than in ``messages.py`` so the indexer and the search
    path cannot drift about what a message "is". A JSON-encoded string is
    decoded once, at index time, so the index matches what the reader shows.
    """
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


def _rows_of(path: Path) -> list[dict[str, object]]:
    """Parsed message rows of one transcript, tolerating a torn last line."""
    from app.services.filestore import load_jsonl

    rows: list[dict[str, object]] = []
    for raw in load_jsonl(path):
        if isinstance(raw, dict):
            rows.append(raw)
    return rows


def _index_session(conn: sqlite3.Connection, path: Path, session_key: str) -> int:
    """(Re)index one transcript file. Returns the row count indexed."""
    stamp = _stamp(path)
    rows = _rows_of(path)
    stored_sid = ''
    for row in rows:
        stored_sid = as_str(row.get('session_id'), '')
        if stored_sid:
            break
    payload: list[tuple[str, str, str, int, str]] = []
    for row in rows:
        text = searchable_text(row.get('content'))
        if not text.strip():
            continue
        payload.append(
            (
                session_key,
                stored_sid or session_key,
                as_str(row.get('role'), ''),
                as_int(row.get('id'), 0),
                text[:_MAX_MESSAGE_CHARS],
            )
        )
    conn.execute('delete from messages where session_key = ?', (session_key,))
    conn.executemany(
        'insert into messages '
        '(session_key, session_id, role, message_id, content) '
        'values (?, ?, ?, ?, ?)',
        payload,
    )
    conn.execute(
        'insert into files (session_key, stamp) values (?, ?) '
        'on conflict(session_key) do update set stamp = excluded.stamp',
        (session_key, stamp),
    )
    return len(payload)


def _transcript_files() -> dict[str, Path]:
    """Every transcript on disk, keyed by its (sanitised) file stem."""
    try:
        return {p.stem: p for p in home(TRANSCRIPTS_DIR).glob('*.jsonl')}
    except OSError:
        return {}


def sync() -> int:
    """Bring the index up to date with the transcripts on disk.

    Incremental: only files whose ``(size, mtime)`` changed are re-read, and a
    file that vanished has its rows dropped. Safe to call on every search — a
    no-op costs one directory listing plus one indexed lookup.
    """
    conn = _try_open()[0]
    if conn is None:
        return 0
    with _LOCK:
        files = _transcript_files()
        try:
            known = {
                as_str(r[0]): as_str(r[1])
                for r in conn.execute('select session_key, stamp from files')
            }
        except sqlite3.Error:
            known = {}
        changed = 0
        for key, path in files.items():
            stamp = _stamp(path)
            if not stamp:
                continue
            if known.get(key) == stamp:
                continue
            _index_session(conn, path, key)
            changed += 1
        # A transcript deleted outside the app must not keep serving hits.
        stale = [k for k in known if k not in files]
        for key in stale:
            conn.execute('delete from messages where session_key = ?', (key,))
            conn.execute('delete from files where session_key = ?', (key,))
            changed += 1
        if changed:
            conn.commit()
        return changed


def rebuild() -> int:
    """Drop the index and read every transcript again. Returns rows indexed.

    The recovery from a corrupt file: nothing is repaired in place, because
    there is nothing here worth repairing — the transcripts are the record.
    """
    conn = _try_open()[0]
    if conn is None:
        return 0
    with _LOCK:
        conn.execute('delete from messages')
        conn.execute('delete from files')
        conn.execute("delete from meta where key = 'schema_version'")
        conn.execute(
            "insert into meta (key, value) values ('schema_version', ?) "
            "on conflict(key) do update set value = excluded.value",
            (str(_SCHEMA_VERSION),),
        )
        conn.commit()
    return sync()


def search(
    terms: list[str], limit: int = 20
) -> list[tuple[float, str, str, str, int, str]] | None:
    """Indexed search. ``None`` means "no index, use the scan".

    Returns ``(rank, session_key, session_id, role, message_id, text)``
    candidates, best first. ``session_id`` rides along so the caller does not
    have to re-read a transcript to learn which session a hit belongs to — the
    sanitised file stem and the recorded id are not always the same string
    (see ``sessions._session_file``).

    Ranking is the SCAN's ranking: a message scores by how many of the query
    terms it contains, the best message per session wins, and the sessions
    come back most-terms-first with the newest message as the tiebreak.
    FTS5's own bm25 is a different measure and is NOT used — using it made
    the two paths order the same hits differently.
    Presentation — snippets, one hit per session, titles — stays with the
    caller, so there is exactly one place that decides what a hit looks like.

    **The index is synced FIRST.** A stale index is the one failure a derived
    cache must not have: it answers "no such message" for a message that is
    on disk, and the caller's fallback only fires on an UNAVAILABLE index, not
    a stale one. Measured before this fix: a message appended to a transcript
    after the last sync was invisible to the search box — the scan found it,
    the index did not, and the product returned an empty result rather than
    falling back.

    The sync is cheap by construction: one directory listing, one indexed
    lookup per unchanged file's stamp, and a re-read only of the files whose
    ``(size, mtime)`` moved.
    """
    if not terms:
        return []
    # A term shorter than the trigram window cannot be matched by the index
    # at all, so the index would answer "not found" for a term the scan
    # matches. Say "no index" instead, and the scan answers.
    if any(len(t) < _MIN_TRIGRAM_TERM for t in terms):
        return None
    conn = _try_open()[0]
    if conn is None:
        return None
    # Sync BEFORE the query: an index that is a moment behind the record is
    # a cache that answers wrongly, and the fallback cannot see that.
    try:
        sync()
    except Exception:  # noqa: BLE001 — a failed sync must not fail the search
        logger.debug('transcript index sync before search failed', exc_info=True)
    # FTS5 MATCH takes a query EXPRESSION. The terms are OR-ed, not
    # AND-ed: the scan scores a message by HOW MANY of the query terms it
    # contains and keeps any message with at least one, so an AND here
    # returned fewer sessions than the scan for the same query — measured:
    # "staging build memory" found one session by index and two by scan.
    # Each term is quoted so punctuation in it is literal, and doubled
    # quotes are FTS5's own escape.
    match = ' OR '.join(f'"{t.replace(chr(34), chr(34) * 2)}"' for t in terms)
    try:
        rows = conn.execute(
            'select session_key, session_id, role, message_id, content '
            'from messages where messages match ? '
            'order by session_key, message_id',
            (match,),
        ).fetchall()
    except sqlite3.Error:
        # A malformed MATCH is a caller bug, not a reason to serve stale data:
        # say so and let the scan answer.
        return None
    # Rank the way the SCAN ranks: a message scores by how many of the
    # query terms it contains, and the best message per session wins with
    # the newest as the tiebreak. FTS5's own bm25 is a different measure,
    # so using it here made the two paths order the same hits differently
    # — measured: "build" returned wb_a before wb_b by scan and wb_b
    # before wb_a by index. bm25 is not the contract; the scan is.
    #
    # The score is computed here because the index knows the content, and
    # the OR match above means a row may hold only SOME of the terms — the
    # same "how many terms are present" count the scan does.
    best: dict[str, tuple[int, int, str, str, str, int, str]] = {}
    for key, sid, role, mid, content in rows:
        text = as_str(content)
        lowered = text.lower()
        score = sum(1 for t in terms if t in lowered)
        if score == 0:
            continue
        message_id = as_int(mid, 0)
        current = best.get(as_str(key))
        # More terms wins; on a tie the newer message wins (the scan walks
        # the file backwards, so a later message takes the slot).
        if current is None or score > current[0] or (
            score == current[0] and message_id >= current[1]
        ):
            best[as_str(key)] = (
                score,
                message_id,
                as_str(sid),
                as_str(role),
                text,
                message_id,
                as_str(key),
            )
    # Most terms matched first, then the newest message — the scan's order.
    ordered = sorted(best.values(), key=lambda r: (-r[0], -r[1]))
    return [
        (
            float(score),
            key,
            sid,
            role,
            message_id,
            text,
        )
        for score, message_id, sid, role, text, _mid, key in ordered[: max(1, min(limit, 100))]
    ]


def search_sessions_by_term(
    terms: list[str], limit: int = 20
) -> list[tuple[float, str, str, str, int, str]] | None:
    """One best hit per session, best first — the search route's contract.

    ``search`` already returns one hit per session, in the scan's order (most
    terms matched first, then the newest message), so this is the same list
    with the same ordering — the deduplication here is belt-and-braces for a
    caller that reaches this function directly.
    """
    hits = search(terms, limit=limit)
    if hits is None:
        return None
    best: dict[str, tuple[float, str, str, str, int, str]] = {}
    for hit in hits:
        best.setdefault(hit[1], hit)
    # Most terms matched first, then the newest message — the scan's order.
    return sorted(best.values(), key=lambda h: (-h[0], -h[4]))


def close() -> None:
    """Release the connection (tests, profile swaps)."""
    with _LOCK:
        conn = _conn()
        _state['conn'] = None
        _state['available'] = None
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass