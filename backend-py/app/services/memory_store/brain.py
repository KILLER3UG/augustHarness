"""brain_query tool domain (multi-store search over the file-backed stores).

The doors keep their exact returned row shapes (``fact_key``/``fact_value``/
``status``/``scope``/... camelCased on the way out) — they now read the file
homes (``tables.py``) and the markdown record (``mirror.py``) instead of SQL.
Each store declares its projected columns so a caller sees the same wire
shape the SQL SELECT returned.
"""

from __future__ import annotations

import json
import re
from typing import cast

from app.adapters.case_converters import camelToSnake
from app.json_narrowing import as_int, as_list, as_str
from app.services.memory_store import mirror as record
from app.services.memory_store import tables
from app.services.memory_store.kv import fts_match
from app.services.memory_store.timeutil import utc_now_text
from app.services.memory_store.wire import _row_as_wire
from app.services.sensitive_topics import sensitiveMemoryReason
from app.type_aliases import JsonValue

_BRAINStores: dict[str, dict[str, object]] = {
    'memory': {
        'store': 'kv',
        'fts': True,
        'columns': 'key, value, updated_at',
        'search_cols': ['key', 'value'],
        'label': 'key-value memory store',
    },
    # 'heuristics' (learned_heuristics) RETIRED 2026-10-30: no live writer ever
    # existed. Queries for it fall through to the unknown-store error below.
    'facts': {
        'store': 'facts',
        'fts': False,
        'columns': (
            'id, fact_key, fact_value, title, kind, category, source, confidence, '
            # `description` is the one-line recall hook (fact_retrieval reads it,
            # rest.py writes it, _EDITABLE below allows patching it) and it was
            # absent from this list, so the human browse door — the one surface
            # that shows a memory to a person — rendered every fact with an
            # empty description while the model's lane had the real one.
            'use_count, last_used_at, status, expires_at, scope, created_at, updated_at, '
            'description, '
            # 055: the supersession lineage (M-3) and the trust signals
            # (M-7, M-8). The Memory UI reads them to show "replaces N",
            # "superseded by X" and a conflict badge, and the detail view
            # offers the restore.
            'superseded_by, supersedes, contradicts, verified_at, '
            'injected_count, last_injected_at, '
            # 059: the value this row's latest edit replaced ("now X,
            # previously Y") — the detail pane renders it when set.
            'previous_value, '
            # 056: the main-goal record shape (aliases/tag/source session/
            # sensitive/version/derived_from) — the Memory UI shows the tag
            # marker and the detail reads them.
            'aliases, subject_type, tag, source_session, sensitive, version, derived_from'
        ),
        'search_cols': ['fact_key', 'fact_value', 'title'],
        'label': 'structured semantic facts',
    },
    'sessions': {
        'store': 'sessions',
        'fts': False,
        'columns': 'id, title, started_at, message_count, provider, model, workspace_path',
        'search_cols': ['title', 'id'],
        'label': 'conversation sessions',
    },
    'messages': {
        'store': 'messages',
        'fts': True,
        'columns': 'id, session_id, role, content, created_at',
        'search_cols': ['content'],
        'label': 'chat messages',
    },
    'timeline': {
        'store': 'timeline',
        'fts': False,
        'columns': 'id, timestamp, session_id, event_summary, category',
        'search_cols': ['event_summary', 'category', 'session_id'],
        'label': 'episodic timeline entries',
    },
    'blackboard': {
        'store': 'blackboard',
        'fts': False,
        'columns': 'id, session_id, agent, key, value, priority, created_at, expires_at',
        'search_cols': ['agent', 'key', 'value'],
        'label': 'inter-agent blackboard notes',
    },
    'exams': {
        'store': 'exams',
        'fts': False,
        'columns': 'id, title, topic, created_at, source, source_files',
        'search_cols': ['title', 'topic'],
        'label': 'exam sessions',
    },
    'examAttempts': {
        'store': 'examAttempts',
        'fts': False,
        'columns': 'id, exam_id, question_id, selected_index, is_correct, asked_for_help, answered_at',
        'search_cols': ['exam_id'],
        'label': 'exam attempt history',
    },
}

# Snake_case / alternate names → canonical store key (models often use SQL names).
_STORE_ALIASES: dict[str, str] = {
    'exam_attempts': 'examAttempts',
    'exam-attempts': 'examAttempts',
    'semantic_facts': 'facts',
    'kv': 'memory',
    'memory_store': 'memory',
}

# Virtual (non-table) stores handled by dedicated query fns.
_VIRTUAL_STORES = frozenset({'failure-fingerprints'})
_STORE_ALIASES.update(
    {
        'failure_fingerprints': 'failure-fingerprints',
        'failureFingerprints': 'failure-fingerprints',
    }
)

# KV keys that are app plumbing, not user-visible memory. The
# Memories browse filters these out (Raw state lookup still sees them).
# agent_jobs itself was purged by migration 037; the entry stays as a guard
# against a legacy store re-seeding it before the import runs.
_KV_MACHINE_KEYS: tuple[str, ...] = ('agent_registry', 'agent_jobs')
_KV_MACHINE_PREFIXES: tuple[str, ...] = ('diff_learn:', 'internal:', 'cognitive:')


def _is_machine_kv_key(key: str) -> bool:
    if key in _KV_MACHINE_KEYS:
        return True
    return any(key.startswith(prefix) for prefix in _KV_MACHINE_PREFIXES)


def count_user_notes() -> int:
    """Non-machine KV rows — the user's Notes, the same set the boot index
    lists and ``brain_browse('memory')`` pages. 0 on an empty store."""
    from app.services.memory_store import kv

    return sum(1 for row in kv.kv_rows() if not _is_machine_kv_key(str(row.get('key') or '')))


def _resolve_store(store: str) -> str:
    """Map wire/SQL aliases to a canonical ``_BRAINStores`` key."""
    if store in _BRAINStores:
        return store
    if store in _STORE_ALIASES:
        return _STORE_ALIASES[store]
    # camelCase ↔ snake_case soft match
    snake = ''.join((('_' + c.lower()) if c.isupper() else c) for c in store)
    if snake in _STORE_ALIASES:
        return _STORE_ALIASES[snake]
    if snake in _BRAINStores:
        return snake
    return store


def _brain_query_daemons(query: str, filters: dict | None, limit: int) -> str:
    """v1.1: Read live daemon registry (Phase 8).

    Returns list of {sessionId, name, status, watchCondition, lastCheck, error} rows.
    If no daemons are running, returns an empty list.
    Gracefully degrades if daemon_manager is unavailable (returns []).
    """
    import json as _json

    try:
        from app.services import daemon_manager
    except ImportError:
        return _json.dumps([])
    try:
        internal = getattr(daemon_manager, '_daemons', None)
        if not isinstance(internal, dict):
            return _json.dumps([])
        rows: list[dict] = []
        for sessionId, daemons in internal.items():
            for d in daemons or []:
                if hasattr(d, '__dict__'):
                    info = dict(d.__dict__)
                elif isinstance(d, dict):
                    info = d
                else:
                    continue
                row = {
                    'sessionId': sessionId,
                    'name': info.get('name', ''),
                    'status': info.get('status', 'unknown'),
                    'watchCondition': info.get('watch_condition'),
                    'lastCheck': info.get('last_check'),
                    'error': info.get('error'),
                }
                if filters and filters.get('sessionId') and (filters['sessionId'] != sessionId):
                    continue
                if query and query.lower() not in row['name'].lower():
                    continue
                rows.append(row)
                if len(rows) >= limit:
                    break
            if len(rows) >= limit:
                break
        return _json.dumps(rows[:limit], ensure_ascii=False)
    except Exception:
        return _json.dumps([])


def _brain_query_project_memory(query: str, filters: dict | None, limit: int) -> str:
    """The project-memory store (virtual, md-file backed).

    Rows = {file, title, body, updated} from the current session's
    workspace ``<ws>/.aug/memory/*.md``, BM25-ranked for a query (fallback:
    file order). Workspace comes from the calling session via the
    ContextVar; ``filters={'workspace': …}`` overrides for admin surfaces.
    """
    import json as _json

    ws = ''
    if filters and isinstance(filters.get('workspace'), str) and filters['workspace'].strip():
        ws = str(filters['workspace']).strip()
    if not ws:
        try:
            from app.services.workbench import workbench as _wb
            from app.services.workbench.context import currentSessionId

            sid = str(currentSessionId.get() or '')
            session = _wb.getWorkbenchSession(sid) if sid else None
            if session is not None:
                ws = str(getattr(session, 'workspacePath', '') or '')
        except Exception:
            ws = ''
    if not ws:
        return _json.dumps(
            {'error': 'project-memory requires a workspace (no workspacePath on this session)'}
        )
    try:
        from app.services import project_memory as _pm

        if query:
            entries = _pm.search_entries(ws, query, k=limit)
        else:
            entries = _pm.read_entries(ws)[: max(1, limit)]
        rows = [
            {
                'file': e.file,
                'title': e.title,
                'body': e.body,
                'updated': e.updated,
            }
            for e in entries
        ]
        return _json.dumps(rows, ensure_ascii=False)
    except Exception as exc:
        return _json.dumps({'error': f'project-memory query failed: {exc}'})


def _brain_query_fingerprints(query: str, filters: dict | None, limit: int) -> str:
    """Failure fingerprints as a virtual brain_query store.

    Fingerprints are the workflow-grain recurrence evidence — querying them
    via ``brain_query store=failure-fingerprints`` makes them model-visible
    without touching the ``search`` tool (the "search memory section" add
    is CUT — brain_query already IS the memory search tool). Query matches
    against the signature text; rows rank by episode_count (recurrence)
    then recency.
    """
    import json as _json

    rows = tables.load_rows(tables.failure_fingerprints_file())
    q = (query or '').strip().lower()
    if q:
        rows = [
            r for r in rows
            if q in str(r.get('fingerprint') or '').lower()
            or q in str(r.get('status') or '').lower()
        ]
    if filters and isinstance(filters.get('status'), str) and filters['status'].strip():
        wanted = str(filters['status']).strip()
        rows = [r for r in rows if str(r.get('status') or '') == wanted]
    rows.sort(key=lambda r: (as_int(r.get('episode_count'), 0), str(r.get('last_seen') or '')), reverse=True)
    cap = max(1, min(limit, 100))
    try:
        rows = [_row_as_wire(r) for r in rows[:cap]]
        return _json.dumps(rows, default=str, ensure_ascii=False)
    except Exception as exc:
        return _json.dumps({'error': f'failure-fingerprints query failed: {exc}'})


def _brain_query_facts_ranked(query: str, filters: dict | None, limit: int) -> str:
    """BM25-ranked facts retrieval for ``brain_query``.

    Mirrors the tail block's ranking (``fact_retrieval`` — one shared
    implementation) instead of LIKE-in-file-order. An exact ``fact_key``
    hit short-circuits to the direct row. Returns the same wire shape as
    the generic path (camelCase rows) so callers see no difference.
    """
    q = (query or '').strip()
    cap = max(1, min(limit, 100))
    from app.services import session_scope as _scope
    from app.services.memory_store.rest import _factRow, _visible

    turnScope = _scope.resolve_scope()
    # The exact-key fast path honors the same visibility policy
    # as list_facts/search_facts — a superseded or expired row must not come
    # back verbatim through the model's memory-search tool while every other
    # door hides it (reproduced live during the scan).
    all_rows = record.load_facts()
    exact = next(
        (r for r in all_rows if str(r.get('fact_key') or '') == q and _visible(r, turnScope)),
        None,
    )
    if exact is not None:
        return json.dumps([_factRow(exact)], default=str, ensure_ascii=False)
    try:
        from app.services.memory_store.fact_retrieval import retrieve_relevant_facts

        # An explicit tool call is a deliberate lookup, so it must not be
        # silenced by the auto-inject floor: a model searching "my gpu" used to
        # fall through to the LIKE scan (rows, but unranked). The caller below
        # still falls back to LIKE when nothing scores.
        facts = retrieve_relevant_facts(
            q, k=cap, scope=turnScope, min_query_chars=1
        )
    except Exception as exc:
        return json.dumps({'error': f'brain_query(facts): ranked retrieval failed: {exc}'})
    if not facts:
        # No BM25 hits — fall through to the old substring scan so partial
        # tokens and punctuation-heavy queries still find their rows.
        return ''
    # Enrich the compact ranking rows with the full wire columns the
    # generic path returns (join back by key).
    byKey = {
        str(r.get('fact_key')): r
        for r in all_rows
        if str(r.get('fact_key') or '')
    }
    ordered = [byKey[str(f.get('key'))] for f in facts if str(f.get('key')) in byKey]
    results = [_factRow(r) for r in ordered]
    # Filters apply after ranking (status/category equality on wire keys).
    if filters:
        import re as _re
        from typing import Mapping as _Mapping

        def _match(row: _Mapping[str, object]) -> bool:
            for fk, fv in (filters or {}).items():
                snake = _re.sub(r'(?<!^)(?=[A-Z])', '_', str(fk)).lower()
                for cand in (fk, snake):
                    if cand in row and str(row[cand]) != str(fv):
                        return False
            return True

        results = [r for r in results if _match(r)]
    resultJson = json.dumps(results, default=str, ensure_ascii=False)
    if len(resultJson) > 2000 * 4:
        wrapped = {
            'rows': results[:10],
            'note': f'{max(0, len(results) - 10)} more rows; narrow your query',
        }
        resultJson = json.dumps(wrapped, default=str, ensure_ascii=False)
    return resultJson


def _project(row: dict[str, object], columns: list[str]) -> dict[str, object]:
    """The declared-column projection of one row — the wire shape a
    ``SELECT <columns>`` returned. A column the row does not carry reads
    None, exactly as a NULL column did."""
    out: dict[str, object] = {}
    for col in columns:
        if col in row:
            out[col] = row.get(col)
        else:
            camel = col[:1] + re.sub(r'_(\w)', lambda m: m.group(1).upper(), col[1:])
            out[col] = row.get(camel)
    return out


def _session_rows() -> list[dict[str, object]]:
    """The sessions store, snake_case-normalized.

    Runtime ownership is the transcript domain; this reads its
    ``transcripts/sessions.json`` tolerantly (list or id-keyed object, camel
    or snake keys) so the model-facing doors keep working whichever spelling
    the live writer uses.
    """
    from app.lib.paths import dataPath

    data = _read_json_any(dataPath('transcripts') / 'sessions.json')
    items: list[dict[str, object]] = []
    if isinstance(data, list):
        items = [r for r in data if isinstance(r, dict)]
    elif isinstance(data, dict):
        items = [r for r in data.values() if isinstance(r, dict)]
    out: list[dict[str, object]] = []
    for item in items:
        row = {
            'id': item.get('id', item.get('sessionId')),
            'title': item.get('title'),
            'started_at': item.get('started_at', item.get('startedAt')),
            'message_count': item.get('message_count', item.get('messageCount')),
            'provider': item.get('provider', ''),
            'model': item.get('model', ''),
            'folder_id': item.get('folder_id', item.get('folderId')),
            'is_archived': item.get('is_archived', item.get('isArchived')),
            'workspace_path': item.get('workspace_path', item.get('workspacePath')),
            'updated_at': item.get('updated_at', item.get('updatedAt')),
        }
        out.append(row)
    return out


def _message_rows(session_cap: int = 400) -> list[dict[str, object]]:
    """Every transcript message, snake_case-normalized, oldest file first.

    The transcripts live as ``transcripts/<sessionId>.jsonl`` (one line per
    message). Reads are bounded: a store with more than ``session_cap``
    transcript files stops scanning — the search door answers over the most
    recently modified transcripts rather than stalling on an archive.
    """
    from app.lib.paths import dataPath

    directory = dataPath('transcripts')
    out: list[dict[str, object]] = []
    if not directory.is_dir():
        return out
    files = [p for p in directory.glob('*.jsonl')]
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
    for path in files[:session_cap]:
        sid = path.stem
        for raw in tables.ledger_rows(path):
            out.append(
                {
                    'id': raw.get('id'),
                    'session_id': raw.get('session_id', raw.get('sessionId')) or sid,
                    'role': raw.get('role'),
                    'content': raw.get('content'),
                    'created_at': raw.get('created_at', raw.get('createdAt')),
                }
            )
    return out


def _read_json_any(path) -> object:
    from app.services import filestore

    return filestore.read_json(path, default=[])


def _store_rows(store_key: str) -> list[dict[str, object]]:
    """Every row of one store, in file (insertion) order."""
    if store_key == 'memory':
        from app.services.memory_store import kv

        return [dict(r) for r in kv.kv_rows()]
    if store_key == 'facts':
        return [dict(r) for r in record.load_facts()]
    if store_key == 'sessions':
        return _session_rows()
    if store_key == 'messages':
        return _message_rows()
    if store_key == 'timeline':
        return tables.load_rows(tables.timeline_file())
    if store_key == 'blackboard':
        return tables.load_rows(tables.blackboard_file())
    if store_key == 'exams':
        return tables.load_section(tables.exams_file(), 'exams')
    if store_key == 'examAttempts':
        return tables.load_section(tables.exams_file(), 'examAttempts')
    return []


def brain_query(store: str, query: str = '', filters: dict | None = None, limit: int = 10) -> str:
    """Read-only query across any brain store.

    Returns compact JSON rows. Capped at ``limit`` and at a hard token
    ceiling (truncated with "N more rows; narrow your query" if exceeded).

    Unknown or not-yet-shipped stores return a structured error string
    rather than raising — keeps the tool stable across phases.

    Accepts canonical wire names (``examAttempts``) and common aliases
    (``exam_attempts``, SQL-ish names).
    """
    _TOKENCeiling = 2000
    if store in ('daemons',):
        return _brain_query_daemons(query, filters, limit)
    if store in ('project-memory', 'project_memory', 'projectMemory'):
        return _brain_query_project_memory(query, filters, limit)
    store = _resolve_store(store)
    if store in _VIRTUAL_STORES:
        return _brain_query_fingerprints(query, filters, limit)
    if store not in _BRAINStores:
        available = sorted(
            set(list(_BRAINStores.keys()) + list(_STORE_ALIASES.keys()) + ['daemons'] + list(_VIRTUAL_STORES))
        )
        return json.dumps(
            {'error': f"store '{store}' not available in this build", 'available': available}
        )
    info = _BRAINStores[store]
    # Facts queries rank through the BM25 index shared
    # with the tail block — relevance instead of substring-in-file-order. An
    # exact fact_key match keeps the direct fast path (a key lookup is a
    # point query, not a search). '' = no BM25 hits → generic scan.
    if store == 'facts' and query:
        ranked = _brain_query_facts_ranked(query, filters, limit)
        if ranked:
            return ranked
    try:
        cols = [c.strip() for c in as_str(info['columns']).split(',') if c.strip()]
        rows = _store_rows(store)
        if store == 'facts':
            # M-2 union + Part 26 6.1: the substring fallback honors the SAME
            # visibility policy as the ranked path and list/search — active,
            # unexpired, scope union. A bot never sees another bot's notes
            # (or a superseded/expired row) through a narrow query that
            # misses BM25.
            from app.services import session_scope as _ss
            from app.services.memory_store.rest import _visible

            rows = [r for r in rows if _visible(r, _ss.resolve_scope())]
        if query:
            fts = bool(info.get('fts'))
            search_cols = [str(c) for c in as_list(info['search_cols'])]
            if fts:
                # The KV/messages matcher: term match over the search columns.
                rows = [
                    r for r in rows
                    if any(fts_match(str(r.get(c) or ''), query) for c in search_cols)
                ]
            else:
                needle = query.lower()
                rows = [
                    r for r in rows
                    if any(needle in str(r.get(c) or '').lower() for c in search_cols)
                ]
        if filters:
            # Accept camelCase filter keys (wire) by converting to snake for columns
            rowKeys = set(rows[0].keys()) if rows else set()
            for key, val in filters.items():
                snake_key = key
                if key not in rowKeys:
                    converted = camelToSnake({key: val})
                    if isinstance(converted, dict) and converted:
                        snake_key = next(iter(converted.keys()))
                rows = [
                    r for r in rows
                    if (snake_key if snake_key in rowKeys else key) in r
                    and str(r.get(snake_key if snake_key in rowKeys else key)) == str(val)
                ]
        cap = max(1, min(limit, 100))
        # Newest first: the SQL era ordered by rowid DESC — insertion order
        # reversed, which the file stores preserve natively.
        rows = list(reversed(rows[-cap:])) if len(rows) > cap else list(reversed(rows))
        total: int | None = None
        if len(rows) >= cap:
            total = None  # the file stores carry no cheap total past the cap
        results = [_project(r, cols) for r in rows]
        results = [_row_as_wire(r) for r in results]
        resultJson = json.dumps(results, default=str, ensure_ascii=False)
        if len(resultJson) > _TOKENCeiling * 4:
            truncated: list[dict[str, object]] = []
            charBudget = _TOKENCeiling * 4
            for r in results:
                rowS = json.dumps(r, default=str, ensure_ascii=False)
                if len(json.dumps(truncated, default=str, ensure_ascii=False)) + len(rowS) < charBudget:
                    truncated.append(r)
                else:
                    break
            nMore = len(results) - len(truncated)
            wrapped: dict[str, object] = {
                'rows': truncated,
                'note': f'{nMore} more rows; narrow your query',
            }
            if total is not None and total > len(truncated):
                wrapped['total'] = total
            resultJson = json.dumps(wrapped, default=str, ensure_ascii=False)
        elif total is not None and total > len(results):
            resultJson = json.dumps(
                {
                    'rows': results,
                    'total': total,
                    'note': f'showing first {len(results)} of {total}; raise limit to see all',
                },
                default=str,
                ensure_ascii=False,
            )
        return resultJson
    except Exception as exc:
        return json.dumps({'error': f'brain_query({store}): {exc}'})


def brain_store_summary() -> list[dict[str, object]]:
    """Per-store counts for the settings Memory page (read-only)."""
    out: list[dict[str, object]] = []
    for name, info in _BRAINStores.items():
        try:
            rows = _store_rows(name)
            # The `memory` (KV) count must match what
            # brain_browse('memory') returns — both exclude machine-state keys
            # (agent_registry, diff_learn:*, …). Otherwise /api/brain/stores
            # shows a count the per-store browse can never page to.
            if name == 'memory':
                count = sum(
                    1 for r in rows if not _is_machine_kv_key(str(r.get('key') or ''))
                )
            else:
                count = len(rows)
        except Exception:
            count = 0  # store not created yet on this install
        out.append({'name': name, 'label': as_str(info.get('label')), 'count': count})
    return out


def _readable_value(raw: object) -> object:
    """Unwrap one stored-text column for the human browse door.

    ``save_fact``/``save_internal`` JSON-encode their value, so the stored
    text of a plain sentence is the eight-characters-longer
    ``"\\\"a sentence\\\"\"``. The boot index decodes it
    (``brain_index_snippet``); browse used to hand the quoting straight to the
    Memory page, which both displayed it and — because the edit field
    round-trips what it was shown — added another layer on every save. Only a
    JSON *string* unwraps: an object value stays verbatim rather than being
    rendered as ``[object Object]`` in the row that edits it."""
    if not isinstance(raw, str) or not raw.startswith('"'):
        return raw
    try:
        decoded = json.loads(raw)
    except ValueError:
        return raw
    return decoded if isinstance(decoded, str) else raw


def _browse_wire(row: dict[str, object]) -> dict[str, object]:
    wire = _row_as_wire(row)
    for field in ('factValue', 'value'):
        if field in wire:
            wire[field] = _readable_value(wire[field])
    return wire


def brain_browse(
    store: str,
    limit: int = 50,
    offset: int = 0,
    query: str = '',
    sort: str = '',
    category: str = '',
    source: str = '',
    confidence: str = '',
    scope: str = '',
    status: str = '',
) -> dict[str, object]:
    """Paginated browse over a brain store for the settings UI.

    Unlike ``brain_query`` (token-capped JSON string for models) this
    returns structured rows + total so a human can page through what
    August stores. Read-only; unknown stores return an error dict.

    Part 17 Phase C: ``sort`` (newest|oldest|updated|confidence) picks the
    order; ``category``/``source``/``confidence`` are server-side
    equality filters (gap 3-4) so filtering works past the fetch cap.
    Part 26 6.7: optional ``scope`` equality filter (the UI lists all
    scopes by design — the human door — but a per-scope view was
    impossible).
    ``status`` is the same equality filter for the retirement flag, and it
    exists because the library needs a RETIRED count: without it the number
    on the card could only be counted off the fetched page, which is a claim
    about a window and not about the store.
    """
    resolved = _resolve_store(store)
    if resolved not in _BRAINStores:
        return {'error': f"store '{store}' not available", 'rows': [], 'total': 0}
    info = _BRAINStores[resolved]
    cols = [c.strip() for c in as_str(info['columns']).split(',') if c.strip()]
    lim = max(1, min(int(limit), 200))
    off = max(0, int(offset))
    try:
        rows = _store_rows(resolved)
        # Machine-state KV keys never render in the human-facing
        # Memories browse — the registry roster and diff-learn cursors are
        # app plumbing, not notes about the user. They stay reachable via
        # Raw state lookup (GET /api/brain/state-lookup).
        if resolved == 'memory':
            rows = [r for r in rows if not _is_machine_kv_key(str(r.get('key') or ''))]
        scopeVal = (scope or '').strip()
        if scopeVal and resolved == 'facts':
            rows = [
                r for r in rows
                if str(r.get('scope') or 'global') == scopeVal
                or (scopeVal == 'global' and not r.get('scope'))
            ]
        q = (query or '').strip()
        if q:
            searchCols = [str(c) for c in as_list(info['search_cols'])]
            needle = q.lower()
            rows = [
                r for r in rows
                if any(needle in str(r.get(c) or '').lower() for c in searchCols)
            ]
        # Server-side equality filters.
        for key, val in (('category', category), ('source', source), ('status', status)):
            v = (val or '').strip()
            if not v:
                continue
            rows = [r for r in rows if str(r.get(key) or '') == v]
        confVal = (confidence or '').strip()
        if confVal in ('low', 'medium', 'high') and resolved == 'facts':
            def _conf(r: dict[str, object]) -> float:
                try:
                    return float(str(r.get('confidence') or 0.0))
                except (TypeError, ValueError):
                    return 0.0

            if confVal == 'low':
                rows = [r for r in rows if _conf(r) < 0.5]
            elif confVal == 'medium':
                rows = [r for r in rows if 0.5 <= _conf(r) < 0.8]
            else:
                rows = [r for r in rows if _conf(r) >= 0.8]
        total = len(rows)
        # Sort control (gap 4). Newest/oldest ride the insertion order; the
        # stores that carry no updated_at fall back to it.
        s = (sort or '').strip().lower()
        if s == 'oldest':
            rows = list(rows)
        elif s == 'updated':
            rows = sorted(rows, key=lambda r: str(r.get('updated_at') or ''), reverse=True)
        elif s == 'confidence':
            rows = sorted(
                rows,
                key=lambda r: float(str(r.get('confidence') or 0.0)) if isinstance(r.get('confidence'), (int, float)) else 0.0,
                reverse=True,
            )
        else:
            # Newest first = insertion order reversed (the rowid DESC order).
            rows = list(reversed(rows))
        page = rows[off : off + lim]
        return {
            'store': resolved,
            'label': as_str(info.get('label')),
            'rows': [_browse_wire(_project(r, cols)) for r in page],
            'total': total,
            'limit': lim,
            'offset': off,
        }
    except Exception as exc:
        return {'error': f'brain_browse({store}): {exc}', 'rows': [], 'total': 0}


def _kv_note_hook(value_raw: object) -> str:
    """One-line recall hook for a KV note: the first line of its value,
    whitespace-collapsed and clamped to 80 chars. Only a JSON *string*
    unwraps (``save_internal`` JSON-encodes everything) — an object value
    stays verbatim rather than being rendered as ``[object Object]`` in the
    line a future session trusts."""
    raw = str(value_raw or '')
    if raw.startswith('"'):
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, str):
                raw = decoded
        except ValueError:
            pass
    return ' '.join(raw.split('\n', 1)[0].split())[:80]


def brain_index_snippet(scope: str = 'global', categories: str = '') -> str:
    """Compact boot index of durable memory for intake injection (B3).

    ZCode-parity (044): one line per stored fact — ``- title (key) — hook`` —
    where the hook is the fact's ``description`` (its one-line recall
    summary), falling back to the fact text's first clause. Lists up to 40
    active, unexpired facts (newest first) plus the last-5 episodic timeline
    summaries, capped by the memory slice of the combined Tier-1 index budget
    (A1: 2400 chars unscaled, smaller on a small-window model — see
    ``workbench.prompt_build.tier1IndexBudget``). Injected at session start
    (frozen per session) so the model always knows what it remembers and can
    pull or update an entry by key without a ``list_facts`` round-trip — the
    same contract as this environment's ``MEMORY.md`` index.

    Notes lane (2026-10-17): the KV store the Memory pane writes is listed
    too — up to 10 user notes (machine keys excluded), one line per key with
    a first-line hook. ``remember`` never writes this store, so without the
    lane everything the user filed by hand was invisible in the boot index
    and reachable only through an unadvertised ``brain_query`` store name.
    Ten keys, not forty: notes are an index, and ``brain_query
    store=memory`` pages the rest with an explicit ``… +N more`` receipt.

    2.1 (Part 25): the facts selection is scope-filtered (global ∪ this-scope)
    — otherwise a Bot's private fact names ride into every other session's
    frozen boot index and vice versa.
    """
    import json as _json

    from app.services.memory_store import kv
    from app.services.memory_store.rest import _visible
    from app.services.session_scope import GLOBAL_SCOPE, normalize_scope

    lines: list[str] = []
    s = normalize_scope(scope)
    _cats = [c.strip().lower() for c in str(categories or '').split(',') if c.strip()]
    try:
        factRows = [
            r for r in record.load_facts()
            if _visible(r, s)
            and (not _cats or str(r.get('category') or '').lower() in _cats)
        ]
        # ORDER BY updated_at DESC — the same selection order as the SQL.
        factRows.sort(key=lambda r: str(r.get('updated_at') or ''), reverse=True)
        factRows = factRows[:40]
        if factRows:
            lines.append('Facts:')
            for r in factRows:
                # Sensitive rows and other people's details never ride the
                # boot index: it is injected into EVERY session, unrelated
                # turns included, and a derived title carries the fact's own
                # text. Explicit reads (brain_query / list_facts) still return
                # them when the user raises them.
                if as_int(r.get('sensitive'), 0) or str(r.get('subject_type') or '') == 'person':
                    continue
                if sensitiveMemoryReason(
                    str(r.get('title') or ''), str(r.get('fact_key') or ''),
                    str(r.get('description') or ''), str(r.get('fact_value') or ''),
                ):
                    continue
                label = str(r.get('title') or '').strip() or str(r.get('fact_key'))
                hook = ' '.join(str(r.get('description') or '').split())
                if not hook:
                    # No explicit description — first clause of the fact text.
                    raw = str(r.get('fact_value') or '')
                    try:
                        obj = _json.loads(raw)
                        if isinstance(obj, dict):
                            raw = str(obj.get('fact') or '')
                        elif isinstance(obj, str):
                            raw = obj
                    except Exception:
                        pass
                    hook = ' '.join(raw.split())[:100]
                # Item 8: the index line is a SLUG, a <=150-char description,
                # and its aliases — not the whole recall hook.
                hook = hook[:150].rstrip()
                line = f"  - {label} ({r.get('fact_key')})"
                if hook:
                    line += f' — {hook}'
                try:
                    _aliases = _json.loads(str(r.get('aliases') or '[]'))
                except (json.JSONDecodeError, TypeError):
                    _aliases = []
                if isinstance(_aliases, list) and _aliases:
                    line += ' [aliases: ' + ', '.join(str(a) for a in _aliases[:4]) + ']'
                lines.append(line)
            try:
                total = sum(
                    1
                    for r in record.load_facts()
                    if _visible(r, s)
                    and (not _cats or str(r.get('category') or '').lower() in _cats)
                    and as_int(r.get('sensitive'), 0) == 0
                    and str(r.get('subject_type') or '') != 'person'
                )
                if total > len(factRows):
                    lines.append(f'  … +{total - len(factRows)} more (brain_query store=facts)')
            except Exception:
                pass
    except Exception:
        pass
    try:
        # Notes — the KV store the Memory pane writes. `remember` never
        # touches this store, so without this lane the model was blind to
        # everything the user filed by hand. Newest first, ten keys, machine
        # keys excluded by the same rule the browse door uses.
        noteRows = [
            r for r in kv.kv_rows()
            if not _is_machine_kv_key(str(r.get('key') or ''))
        ]
        noteRows.sort(key=lambda r: str(r.get('updated_at') or ''), reverse=True)
        if noteRows:
            lines.append('Notes:')
            for r in noteRows[:10]:
                hook = _kv_note_hook(r.get('value'))
                line = f"  - {r.get('key')}"
                if hook:
                    line += f' — {hook}'
                lines.append(line)
            if len(noteRows) > 10:
                lines.append(
                    f'  … +{len(noteRows) - 10} more (brain_query store=memory)'
                )
    except Exception:
        pass
    try:
        # Part 27 T1 (privacy): fence the timeline by the same global∪scope
        # clause the facts half uses, so a Bot's private last-user-message
        # never rides into an unrelated session's boot index.
        tlRows = [
            r for r in tables.load_rows(tables.timeline_file())
            if str(r.get('scope') or 'global') == s
            or (s == GLOBAL_SCOPE and str(r.get('scope') or 'global') == 'global')
            or (s != GLOBAL_SCOPE and str(r.get('scope') or 'global') == 'global')
        ]
        tlRows.sort(key=lambda r: str(r.get('timestamp') or ''), reverse=True)
        tlRows = tlRows[:5]
        if tlRows:
            lines.append('Recent events:')
            for r in tlRows:
                summary = str(r.get('event_summary') or '').strip()
                if summary:
                    lines.append(f'  {summary[:160]}')
    except Exception:
        pass
    if not lines:
        return ''
    # The cap is the memory slice of the combined Tier-1 index budget (A1) —
    # read off the context var the prompt build published, so a small-window
    # model gets a smaller boot index and the unscaled 2400 when no build is
    # running. Cut at a LINE boundary: a mid-line slice leaves half a hook
    # that reads like a complete fact — the exact lie this index exists to
    # avoid.
    from app.services.workbench.prompt_build import currentIndexBudget

    capChars = currentIndexBudget()[0]
    text = '\n'.join(lines)
    if len(text) > capChars:
        cut = text.rfind('\n', 0, capChars)
        text = text[:cut] if cut > 0 else text[:capChars]
    return text


# Per-store writable field whitelists for brain_update_row (B5). Stores absent
# here are not inline-editable from the settings UI.
_ROW_EDIT_FIELDS: dict[str, frozenset[str]] = {
    # `status` is whitelisted for exactly one writer: the Memory UI's
    # "Restore" on a retired row (status='active'; brain_update_row refuses
    # any other value). Consolidation remains the only writer of 'retired' /
    # 'superseded'.
    'facts': frozenset({'fact_value', 'title', 'kind', 'description', 'category', 'confidence', 'expires_at', 'status'}),
    'memory': frozenset({'value'}),
    'timeline': frozenset({'event_summary', 'category'}),
}
_ROW_DELETABLE: frozenset[str] = frozenset({'facts', 'memory', 'timeline'})


def _store_id_column(store: str) -> str:
    """Row identifier for a store: ``key`` for the KV memory store, else ``id``."""
    return 'key' if store == 'memory' else 'id'


def _historyValue(obj: object) -> str:
    """The fact text carried by one rollback snapshot (before or after)."""
    if not isinstance(obj, dict):
        return ''
    raw: object = obj.get('factValue') if 'factValue' in obj else obj.get('value')
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    if isinstance(raw, dict):
        raw = raw.get('fact') or ''
    return ' '.join(str(raw or '').split())


def fact_history(row_id: object) -> dict[str, object]:
    """The edit history for one fact row, read from the rollback store.

    The rollback log IS the history system (a snapshot is taken before every
    model/UI overwrite); this projects the entries for one key instead of
    adding a second store. Newest first, bounded.
    """
    try:
        wanted_id = as_int(row_id, -1)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'row id must be an integer'}
    target = next(
        (r for r in record.load_facts() if as_int(r.get('id'), 0) == wanted_id),
        None,
    )
    if target is None:
        return {'ok': False, 'error': 'row not found'}
    key = str(target.get('fact_key') or '')
    from app.services.rollback_store import list_entries

    targets = {key, f'facts:{key}'}
    entries: list[dict[str, object]] = []
    for entry in list_entries():
        if entry.get('type') != 'restore_memory_item':
            continue
        if str(entry.get('target') or '') not in targets:
            continue
        entries.append(
            {
                'entryId': entry.get('id'),
                'at': entry.get('at'),
                'before': _historyValue(entry.get('before')),
                'after': _historyValue(entry.get('after')),
            }
        )
    return {'ok': True, 'key': key, 'entries': list(reversed(entries))[:20]}


def _record_row_rollback(store: str, target: str, before: object, after: object) -> None:
    """Best-effort undo record for a settings-UI memory mutation."""
    try:
        from app.services.rollback_store import record_rollback

        record_rollback(
            type='restore_memory_item',
            target=f'{store}:{target}',
            before=before,
            after=after,
            extra={'store': store},
        )
    except Exception:
        pass


def _invalidate_fact_cache_if_facts(resolved: str) -> None:
    """2.6: a Settings-UI edit/delete of a fact must drop the cached
    BM25 corpus, or the stale row keeps being injected until an unrelated
    write clears it."""
    if resolved != 'facts':
        return
    try:
        from app.services.memory_store.fact_retrieval import invalidate_fact_index

        invalidate_fact_index()
    except Exception:
        pass


def brain_delete_row(store: str, row_id: object) -> dict[str, object]:
    """Delete one row from a brain store for the settings UI (B5)."""
    resolved = _resolve_store(store)
    if resolved not in _ROW_DELETABLE or resolved not in _BRAINStores:
        return {'ok': False, 'error': f"store '{store}' does not support per-entry delete"}
    try:
        if resolved == 'facts':
            target = next(
                (r for r in record.load_facts() if as_int(r.get('id'), 0) == as_int(row_id, -1)),
                None,
            )
            if target is None:
                return {'ok': False, 'error': 'row not found'}
            beforeWire: dict[str, object] | None = _row_as_wire(_project(target, [
                c.strip() for c in as_str(_BRAINStores['facts']['columns']).split(',')
            ]))
            # M-3: deleting a fact that REPLACED others brings those others back
            # first. A superseded row is kept, not deleted — so without this it
            # would stay parked against a winner that no longer exists, and the
            # content it holds would be invisible to recall and to this list.
            restored: list[str] = []
            try:
                factKey = str(target.get('fact_key') or '')
                if factKey:
                    from app.services.memory_store.rest import delete_fact, restore_superseded_losers

                    restored = restore_superseded_losers(factKey)
                    ok = delete_fact(factKey)
                else:
                    ok = False
            except Exception:
                restored = []
                ok = False
            if ok:
                _record_row_rollback(resolved, str(row_id), beforeWire, None)
                _invalidate_fact_cache_if_facts(resolved)
            out: dict[str, object] = {'ok': ok, 'store': resolved}
            if restored:
                out['restoredSuperseded'] = restored
            return out
        if resolved == 'memory':
            from app.services.memory_store.kv import delete_memory

            key = str(row_id or '')
            before = next(
                (r for r in tables.load_rows(tables.kv_file()) if str(r.get('key') or '') == key),
                None,
            )
            ok = delete_memory(key)
            if ok:
                _record_row_rollback(resolved, key, _row_as_wire(before) if before else None, None)
            return {'ok': ok, 'store': resolved}
        # timeline
        path = tables.timeline_file()
        with tables.store_lock(str(path)):
            rows = tables.load_rows(path)
            before = next(
                (r for r in rows if as_int(r.get('id'), 0) == as_int(row_id, -1)),
                None,
            )
            if before is None:
                return {'ok': False, 'error': 'row not found'}
            beforeWire = _row_as_wire(before)  # type: ignore[assignment]
            tables.save_rows(path, [r for r in rows if r is not before])
        _record_row_rollback(resolved, str(row_id), beforeWire, None)
        return {'ok': True, 'store': resolved}
    except Exception as exc:
        return {'ok': False, 'error': f'brain_delete_row({store}): {exc}'}


def brain_update_row(store: str, row_id: object, patch: dict[str, object]) -> dict[str, object]:
    """Update whitelisted fields of one brain-store row for the settings UI (B5).

    Only fields in the per-store whitelist are applied; unknown or read-only
    fields are ignored.
    """
    resolved = _resolve_store(store)
    # `status` is whitelisted for exactly one direction: LIFTING retirement.
    # Consolidation's proposal flow stays the only writer of 'retired' /
    # 'superseded' — the settings door must never park a row the model is
    # using, even if a future UI bug asks it to.
    if resolved == 'facts' and 'status' in patch:
        # Normalise BEFORE the check and write the normalised value, so the
        # string tested is the string stored. Stripping only inside the guard
        # let {'status': ' active '} through the door and into the row, where
        # both the visibility test (`status = 'active'`) and the UI badge miss
        # it: the toast said "Entry restored — it is recalled again" while the
        # fact stayed parked. Lowercasing closes the same hole for 'RETIRED',
        # which now falls into the refusal instead of storing a fourth value.
        wanted = str(patch.get('status') or '').strip().lower()
        if wanted not in ('', 'active'):
            return {'ok': False, 'error': "status may only be set to 'active' (retirement is consolidation's decision)"}
        if not wanted:
            # An empty field is "leave it alone", not "store ''" — '' is not
            # 'active' and would hide the row the same way 'retired' does. The
            # loop below then finds no writable field and answers as it already
            # does for an empty patch.
            patch = {k: v for k, v in (patch or {}).items() if k != 'status'}
        else:
            patch = {**patch, 'status': wanted}
    allowed = _ROW_EDIT_FIELDS.get(resolved)
    if allowed is None or resolved not in _BRAINStores:
        return {'ok': False, 'error': f"store '{store}' does not support per-entry edit"}
    try:
        if resolved == 'facts':
            target = next(
                (r for r in record.load_facts() if as_int(r.get('id'), 0) == as_int(row_id, -1)),
                None,
            )
            if target is None:
                return {'ok': False, 'error': 'row not found'}
            sets = {k: v for k, v in (patch or {}).items() if k in allowed}
            if not sets:
                return {'ok': False, 'error': 'no writable fields in patch'}
            # M-3: a row that is ACTIVE again is neither superseded nor in
            # conflict, so lifting its status lifts the lineage with it. Leaving
            # `superseded_by` pointing at the fact that replaced it would make a
            # restored row claim a parent it no longer has.
            if str(sets.get('status') or '').strip().lower() == 'active':
                sets.setdefault('superseded_by', '')
                sets.setdefault('contradicts', '')
            cols = [c.strip() for c in as_str(_BRAINStores['facts']['columns']).split(',')]
            beforeWire = _row_as_wire(_project(target, cols))
            row = dict(target)
            row.update(sets)
            # Bump updated_at so edited entries re-sort to the top of recency views.
            if 'updated_at' not in sets:
                row['updated_at'] = utc_now_text()
            # A fact_value edit is a value TEXT patch — the file body stores it
            # JSON-encoded exactly as delivered, byte for byte.
            record.write_fact_file(row)
            afterWire = _row_as_wire(_project(row, cols))
            _record_row_rollback(resolved, str(row_id), beforeWire, afterWire)
            _invalidate_fact_cache_if_facts(resolved)
            return {'ok': True, 'store': resolved, 'row': afterWire}
        if resolved == 'memory':
            if 'value' not in (patch or {}):
                return {'ok': False, 'error': 'no writable fields in patch'}
            from app.services.memory_store.kv import save_internal

            key = str(row_id or '')
            path = tables.kv_file()
            with tables.store_lock(str(path)):
                before_row = next(
                    (r for r in tables.load_rows(path) if str(r.get('key') or '') == key),
                    None,
                )
            beforeWire = _row_as_wire(before_row) if before_row else None  # type: ignore[assignment]
            save_internal(key, cast(JsonValue, patch.get('value')))
            with tables.store_lock(str(path)):
                after_row = next(
                    (r for r in tables.load_rows(path) if str(r.get('key') or '') == key),
                    None,
                )
            afterWire = _row_as_wire(after_row) if after_row else None  # type: ignore[assignment]
            _record_row_rollback(resolved, key, beforeWire, afterWire)
            return {'ok': True, 'store': resolved, 'row': afterWire}
        # timeline
        allowed_tl = {'event_summary', 'category'} & set(patch or {})
        if not allowed_tl:
            return {'ok': False, 'error': 'no writable fields in patch'}
        path = tables.timeline_file()
        with tables.store_lock(str(path)):
            rows = tables.load_rows(path)
            target = next(
                (r for r in rows if as_int(r.get('id'), 0) == as_int(row_id, -1)),
                None,
            )
            if target is None:
                return {'ok': False, 'error': 'row not found'}
            beforeWire = _row_as_wire(target)
            target.update({k: patch[k] for k in allowed_tl})
            tables.save_rows(path, rows)
            afterWire = _row_as_wire(target)
        _record_row_rollback(resolved, str(row_id), beforeWire, afterWire)
        return {'ok': True, 'store': resolved, 'row': afterWire}
    except Exception as exc:
        return {'ok': False, 'error': f'brain_update_row({store}): {exc}'}
