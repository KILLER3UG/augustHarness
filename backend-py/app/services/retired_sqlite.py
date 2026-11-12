"""One-time import of the retired SQLite brain database into the file stores.

Runs once, in ``main.py``'s lifespan, AFTER a staged backup restore and BEFORE
any store opens. When ``<dataDir>/august_brain.sqlite`` exists and the
``<state>/sqlite-imported.json`` marker does not:

1. The owner's file is never written. It is opened ``file:...?mode=ro``
   (READ-ONLY) when no WAL is pending; when ``-wal`` is non-empty — the
   normal state after a ``taskkill /T /F`` quit — the database plus its
   sidecars are copied to a temp dir and the COPY is opened read-write so
   SQLite replays the WAL, then the copy is deleted. After the export the
   original is renamed to ``august_brain.sqlite.retired`` (with ``-wal`` and
   ``-shm``) — never deleted: the pre-import bytes are the owner's
   last-resort copy.
2. Every user table WITH ROWS is exported to its file home:

     facts               → memory/             (one markdown file per fact,
                                                 written by the same writer
                                                 the fact CRUD uses)
     memory_store        → state/kv.json
     internal_state      → state/internal-state.json
     proposals           → state/proposals.json
     config_audit        → state/config-audit.json
     episodic_timeline   → state/episodic-timeline.json
     episodes            → state/episodes.json
     blackboard          → state/blackboard.json
     exams / exam_questions / exam_attempts
                         → state/exams.json    (three sections)
     goal_*              → state/ (generic <name>.json fallback)
     daemons             → state/daemons.json
     recurring_tasks(+runs) → state/recurring-tasks.json
     workstreams(+episodes) → state/workstreams.json
     refine_entries / refine_ledger
                         → state/refine-entries.json / refine-ledger.jsonl
     session_topics      → telemetry/session-topics.json
     failure_fingerprints→ telemetry/failure-fingerprints.json
     decision_ledger     → telemetry/decision-ledger.jsonl
     routing_evidence    → telemetry/routing-evidence.jsonl
     tool_guardrail_log  → telemetry/tool-guardrail-log.jsonl
     subagent_runs       → telemetry/subagent-runs.jsonl
     sessions            → transcripts/sessions.json   (dict keyed by id)
     messages            → transcripts/<sessionId>.jsonl (one line per row)
     turn_outcomes       → telemetry/turn-outcomes.jsonl
     usage_events        → telemetry/usage-events.jsonl
     lifecycle           → telemetry/lifecycle.jsonl
     learning_job_run    → telemetry/learning-job-run.jsonl
     automation_runs     → telemetry/automation-runs.jsonl
                            (capped at the runtime ceiling on import: the
                            retired database held 507,602 rows / 143 MB of
                            one repeating cron error, and importing them
                            verbatim would make the file store larger than
                            the database it replaces)
     automation_incidents→ telemetry/automation-incidents.json
     harness_* and anything else with rows
                         → telemetry/<dashed-table>.jsonl

   Rows are exported VERBATIM — the SQL column names and the stored text
   (JSON-encoded values, JSON list columns) survive unchanged, so the file
   readers see exactly the rows the database held.

3. MEMORY RECOVERY: ``config.json``'s ``rollbackLog`` is scanned for
   ``type='restore_memory_item'`` entries with ``status='available'`` — every
   memory a rollback entry could still put back. Entries are deduped by
   target key keeping the LATEST ``at``, and each is written into the
   markdown record through the same writer the fact CRUD uses (value from
   ``after.value``, title/kind/category from ``after`` when present,
   source='rollback-recovery'). This is the owner's lost-memory recovery
   path: a memory the SQL store already lost is often still named in an
   available rollback entry, and retiring the database without reading them
   would destroy it. The recovered count goes in the marker.

4. The marker (row counts + recovery count + timestamp) is written, and only
   then is the database renamed aside. A failure at ANY step logs loudly,
   writes a ``status: 'failed'`` marker (with the error) so the boot never
   loops on the same broken file, writes NOTHING ELSE, and leaves the
   database exactly where it is.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.lib.paths import dataPath
from app.services import filestore

logger = logging.getLogger('august.retired_sqlite')

_DB_NAME = 'august_brain.sqlite'
# The fixture import's auto-key shape (`facts:1053`) — a bare numeric key
# carries no content identity and only the pytest leak ever produced one.
_FIXTURE_KEY_RE = re.compile(r'^facts:\d+$')
_MARKER_NAME = 'sqlite-imported.json'
_RETIRED_SUFFIX = '.retired'

# Table → (kind, name). kind 'jsonl' appends rows verbatim to a telemetry
# ledger; 'json' writes a whole-file list; 'dict' writes an id-keyed object.
_LEDGER_TABLES: dict[str, str] = {
    'turn_outcomes': 'turn-outcomes.jsonl',
    'usage_events': 'usage-events.jsonl',
    'lifecycle': 'lifecycle.jsonl',
    'learning_job_run': 'learning-job-run.jsonl',
    'decision_ledger': 'decision-ledger.jsonl',
    'routing_evidence': 'routing-evidence.jsonl',
    'tool_guardrail_log': 'tool-guardrail-log.jsonl',
    'subagent_runs': 'subagent-runs.jsonl',
}
_JSON_TABLES: dict[str, tuple[str, str]] = {
    # table → (home, file name)
    'memory_store': ('state', 'kv.json'),
    'internal_state': ('state', 'internal-state.json'),
    'proposals': ('state', 'proposals.json'),
    'config_audit': ('state', 'config-audit.json'),
    'episodic_timeline': ('state', 'episodic-timeline.json'),
    'episodes': ('state', 'episodes.json'),
    'blackboard': ('state', 'blackboard.json'),
    'session_topics': ('telemetry', 'session-topics.json'),
    'failure_fingerprints': ('telemetry', 'failure-fingerprints.json'),
    'automation_incidents': ('telemetry', 'automation-incidents.json'),
    'refine_entries': ('state', 'refine-entries.json'),
}
_LEDGER_STATE_TABLES: dict[str, str] = {
    'refine_ledger': 'refine-ledger.jsonl',
}
_EXAMS_TABLES = {'exams': 'exams', 'exam_questions': 'examQuestions', 'exam_attempts': 'examAttempts'}
_GOAL_TABLES = ('goal_envs', 'goal_runs', 'goal_artifacts', 'goal_movements', 'goal_claims', 'goal_marks')


def marker_path() -> Path:
    return filestore.home(filestore.STATE_DIR) / _MARKER_NAME


def _home(kind: str) -> Path:
    return filestore.home(kind)


def _open_read_only(path: Path) -> sqlite3.Connection:
    """The ONLY way this module touches the owner's SQL file. ``mode=ro``
    fails the open rather than creating or locking the database — a migration
    that could write the live brain file would be exactly the risk this
    module exists to retire."""
    return _connect(path, read_only=True)


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    conn = (
        sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)
        if read_only
        else sqlite3.connect(str(path))
    )
    conn.row_factory = sqlite3.Row
    return conn


def _stage_for_import(db: Path) -> tuple[Path, Path]:
    """Copy the database and its ``-wal``/``-shm`` sidecars to a temp dir.

    A read-only connection cannot replay a WAL (it needs write access to the
    ``-shm``), and the packaged desktop quit is ``taskkill /T /F`` — a
    non-empty ``-wal`` is the NORMAL state of this install, not corruption.
    Refusing it would leave the record empty on every update. So the import
    reads a private copy opened read-write: the copy replays its own WAL,
    the owner's file is never written, and the copy is deleted afterwards.
    """
    tmp = Path(tempfile.mkdtemp(prefix='august-import-'))
    staged = tmp / db.name
    shutil.copy2(db, staged)
    for suffix in ('-wal', '-shm'):
        side = db.with_name(db.name + suffix)
        if side.exists():
            shutil.copy2(side, tmp / side.name)
    return tmp, staged


def _table_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def _user_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%'"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _write_ledger(home_kind: str, name: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    path = _home(home_kind) / name
    existing = [r for r in filestore.load_jsonl(path) if isinstance(r, dict)]
    filestore.rewrite_jsonl(path, [*existing, *rows])
    return len(rows)


def _write_json_rows(
    home_kind: str, name: str, rows: list[dict[str, Any]], dedupe_key: str = ''
) -> int:
    """Append rows to a whole-file JSON store.

    ``dedupe_key`` names the primary-key column of the SQL table: an imported
    row REPLACES a file row with the same key (the database is the
    authoritative pre-migration state, and ``memory_store``/``internal_state``
    were PRIMARY KEY tables). Without the key the rows simply append."""
    if not rows:
        return 0
    path = _home(home_kind) / name
    raw = filestore.read_json(path, default=[])
    existing = [r for r in (raw if isinstance(raw, list) else []) if isinstance(r, dict)]
    if dedupe_key:
        imported_keys = {str(r.get(dedupe_key)) for r in rows if r.get(dedupe_key) is not None}
        existing = [r for r in existing if str(r.get(dedupe_key)) not in imported_keys]
    filestore.write_json(path, [*existing, *rows])
    return len(rows)


def _write_dict_rows(home_kind: str, name: str, rows: list[dict[str, Any]], key_field: str) -> int:
    if not rows:
        return 0
    path = _home(home_kind) / name
    data = filestore.read_json(path, default={})
    doc = data if isinstance(data, dict) else {}
    written = 0
    for row in rows:
        key = str(row.get(key_field) or '')
        if not key:
            continue
        doc[key] = row
        written += 1
    if written:
        filestore.write_json(path, doc)
    return written


def _export_workstreams(conn: sqlite3.Connection, tables: list[str]) -> int:
    if 'workstreams' not in tables and 'workstream_episodes' not in tables:
        return 0
    streams = _table_rows(conn, 'workstreams')
    episodes = _table_rows(conn, 'workstream_episodes')
    if not streams and not episodes:
        return 0
    path = _home(filestore.STATE_DIR) / 'workstreams.json'
    doc = filestore.read_json(path, default={})
    data = doc if isinstance(doc, dict) else {}
    written = 0
    for stream in streams:
        sid = str(stream.get('session_id') or '')
        name = str(stream.get('name') or '')
        if not sid or not name:
            continue
        entry = data.setdefault(sid, {})
        if name in entry and isinstance(entry[name], dict):
            continue
        entry[name] = {
            'id': stream.get('id'),
            'created_at': stream.get('created_at') or '',
            'updated_at': stream.get('updated_at') or '',
            'episodes': [],
        }
        written += 1
    by_ws: dict[str, list[dict[str, Any]]] = {}
    for ep in episodes:
        by_ws.setdefault(str(ep.get('workstream_id') or ''), []).append(ep)
    for stream in streams:
        sid = str(stream.get('session_id') or '')
        name = str(stream.get('name') or '')
        entry = data.get(sid, {}).get(name)
        if not isinstance(entry, dict):
            continue
        eps = entry.setdefault('episodes', [])
        have = {int(e.get('seq') or 0) for e in eps if isinstance(e, dict)}
        for ep in sorted(by_ws.get(str(stream.get('id')), []), key=lambda r: int(r.get('seq') or 0)):
            if int(ep.get('seq') or 0) in have:
                continue
            eps.append(
                {
                    'seq': ep.get('seq'),
                    'task_id': ep.get('task_id') or '',
                    'status': ep.get('status') or '',
                    'summary': ep.get('summary') or '',
                    'artifacts': ep.get('artifacts') or '[]',
                    'next_action': ep.get('next_action') or '',
                    'raw_json': ep.get('raw_json') or '',
                    'created_at': ep.get('created_at') or '',
                }
            )
            written += 1
    if written:
        filestore.write_json(path, data)
    return written


def _export_recurring_tasks(conn: sqlite3.Connection) -> int:
    tasks = _table_rows(conn, 'recurring_tasks')
    runs = _table_rows(conn, 'recurring_task_runs')
    if not tasks and not runs:
        return 0
    path = _home(filestore.STATE_DIR) / 'recurring-tasks.json'
    data = filestore.read_json(path, default={})
    doc = data if isinstance(data, dict) else {}
    task_list = [t for t in doc.get('tasks') or [] if isinstance(t, dict)]
    have = {str(t.get('id')) for t in task_list}
    written = 0
    for t in tasks:
        if str(t.get('id')) in have:
            continue
        task_list.append(t)
        written += 1
    runs_raw = doc.get('runs')
    runs_by_task: dict[str, list[dict[str, Any]]] = (
        {str(k): v for k, v in runs_raw.items() if isinstance(v, list)} if isinstance(runs_raw, dict) else {}
    )
    for r in runs:
        key = str(r.get('task_id') or '')
        if not key:
            continue
        bucket = runs_by_task.setdefault(key, [])
        stamp = str(r.get('fired_at') or '')
        if any(str(x.get('fired_at') or '') == stamp and str(x.get('message') or '') == str(r.get('message') or '') for x in bucket):
            continue
        bucket.append({'fired_at': stamp, 'message': r.get('message') or ''})
        written += 1
    if written:
        filestore.write_json(path, {'tasks': task_list, 'runs': runs_by_task})
    return written


def _export_exams(conn: sqlite3.Connection) -> int:
    sections = {section: _table_rows(conn, table) for table, section in _EXAMS_TABLES.items()}
    if not any(sections.values()):
        return 0
    path = _home(filestore.STATE_DIR) / 'exams.json'
    doc = filestore.read_json(path, default={})
    data = doc if isinstance(doc, dict) else {}
    written = 0
    for section, rows in sections.items():
        existing = [r for r in data.get(section) or [] if isinstance(r, dict)]
        have = {str(r.get('id')) for r in existing}
        for row in rows:
            if str(row.get('id')) in have:
                continue
            existing.append(row)
            written += 1
        data[section] = existing
    if written:
        filestore.write_json(path, data)
    return written


def _bump_message_seq(max_message_id: int) -> None:
    """Push the transcript id counter past every imported id, so a rewritten
    transcript never hands a new message an id an imported row already used."""
    if max_message_id <= 0:
        return
    from app.services.memory_store.sessions import _SEQ_FILE, _SEQ_KEY

    path = _home(filestore.STATE_DIR) / _SEQ_FILE
    counter = filestore.read_json(path, default={})
    doc = counter if isinstance(counter, dict) else {}
    current = doc.get(_SEQ_KEY)
    try:
        current_n = int(current) if current is not None else 1
    except (TypeError, ValueError):
        current_n = 1
    if max_message_id + 1 > current_n:
        filestore.write_json(path, {_SEQ_KEY: max_message_id + 1})


def _import_facts(conn: sqlite3.Connection) -> int:
    from app.services.memory_store import mirror as record

    rows = _table_rows(conn, 'facts')
    written = 0
    for raw in rows:
        row = dict(raw)
        key = str(row.get('fact_key') or '')
        if not key:
            continue
        try:
            row.setdefault('version', 1)
            row.setdefault('use_count', 0)
            row.setdefault('injected_count', 0)
            row.setdefault('aliases', '[]')
            row.setdefault('supersedes', '[]')
            row.setdefault('derived_from', '[]')
            row.setdefault('previous_value', '')
            row.setdefault('sensitive', 0)
            row.setdefault('status', 'active')
            record.write_fact_file(row)  # type: ignore[arg-type]
            written += 1
        except Exception:  # noqa: BLE001 — one bad row must not cost the rest
            logger.warning('fact import failed for key %r', key, exc_info=True)
    if written:
        try:
            record.rebuild_mirror()
        except Exception:  # noqa: BLE001
            logger.debug('post-import index rebuild failed', exc_info=True)
    return written


def _recover_rollback_memories() -> int:
    """The owner's lost-memory recovery: every ``restore_memory_item`` entry
    the rollback log still marks ``available`` becomes a fact again.

    Dedupe by target key keeping the LATEST ``at`` — an entry per overwrite
    means the newest snapshot is what the memory became, and restoring an
    older one would undo a later correction. Written through the same writer
    the fact CRUD uses, with source='rollback-recovery'.
    """
    from app.json_narrowing import as_dict, as_list, as_str
    from app.services.config_service import getConfig
    from app.services.memory_store import mirror as record
    from app.services.memory_store.rest import _json

    cfg = getConfig() or {}
    entries = [as_dict(x) for x in as_list(cfg.get('rollbackLog'))]
    latest: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if str(entry.get('type') or '') != 'restore_memory_item':
            continue
        if str(entry.get('status') or '') != 'available':
            continue
        target = as_str(entry.get('target') or '')
        if not target or target.startswith('project:'):
            continue  # project memory is a different store with its own door
        at = str(entry.get('at') or '')
        if target not in latest or at > str(latest[target].get('at') or ''):
            latest[target] = entry

    recovered = 0
    skipped_probes = 0
    for target, entry in latest.items():
        after = as_dict(entry.get('after'))
        if not after:
            continue
        raw_value = after.get('factValue') if 'factValue' in after else after.get('value')
        value: object = raw_value
        if isinstance(raw_value, str):
            try:
                value = json.loads(raw_value)
            except (json.JSONDecodeError, TypeError, ValueError):
                value = raw_value
        if value in (None, ''):
            continue
        # The pytest leak class (paths.py's warning): probe tests wrote
        # watermarked rows into the live store and their rollback entries ride
        # the same log as real memory. The tests watermark themselves —
        # 'remember-probe', 'audit probe'/'AUDITPROBE', the IMPMA import
        # marker — and the fixture import auto-keyed rows `facts:<id>` with no
        # content identity. Skipping them here is the leak guard's last sweep,
        # not a judgment about the owner's data: a watermark exists to be
        # found, and a fixture row must not become recallable user memory.
        probe_text = f'{target} {value}'
        if (
            'probe' in target.lower()
            or _FIXTURE_KEY_RE.match(target)
            or 'AUDITPROBE' in probe_text
            or 'IMPMA' in probe_text
        ):
            skipped_probes += 1
            logger.info('rollback recovery: skipping probe/fixture entry %r', target)
            continue
        existing = record.load_fact(target)
        row: dict[str, Any] = {
            'fact_key': target,
            'scope': as_str(existing.get('scope') if existing else '') or 'global',
            'fact_value': _json(value),
            'title': as_str(after.get('title') or '') or (as_str(existing.get('title') if existing else '') or ''),
            'kind': as_str(after.get('kind') or '') or (as_str(existing.get('kind') if existing else '') or 'fact'),
            'description': as_str(after.get('description') or '') or (as_str(existing.get('description') if existing else '') or ''),
            'category': as_str(after.get('category') or '') or (as_str(existing.get('category') if existing else '') or 'general'),
            'source': 'rollback-recovery',
            'tag': 'observed',
            'confidence': 1.0,
            'status': 'active',
            'version': (int(str(existing.get('version') or 0)) + 1) if existing else 1,
            'created_at': as_str(existing.get('created_at') if existing else '') or datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'),
            'updated_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'),
            'expires_at': as_str(after.get('expiresAt') or '') or (as_str(existing.get('expires_at') if existing else '') or ''),
            'sensitive': 1 if (existing.get('sensitive') if existing else 0) else 0,
            'use_count': int(str(existing.get('use_count') or 0)) if existing else 0,
            'last_used_at': as_str(existing.get('last_used_at') if existing else '') or '',
            'injected_count': 0,
            'last_injected_at': '',
            'aliases': '[]',
            'supersedes': '[]',
            'superseded_by': '',
            'contradicts': '',
            'derived_from': '[]',
            'previous_value': str(existing.get('fact_value') or '') if existing else '',
            'verified_at': '',
            'source_session': '',
        }
        try:
            record.write_fact_file(row)
            recovered += 1
        except Exception:  # noqa: BLE001
            logger.warning('rollback recovery failed for key %r', target, exc_info=True)
    if recovered:
        try:
            record.rebuild_mirror()
        except Exception:  # noqa: BLE001
            logger.debug('post-recovery index rebuild failed', exc_info=True)
    if skipped_probes:
        logger.info('rollback recovery: skipped %d probe/fixture entr(y/ies)', skipped_probes)
    return recovered


def run_sqlite_retirement() -> dict[str, object]:
    """Import the retired database into the file stores (once, without writing
    the owner's file).

    Never raises. Any failure writes the failed marker and leaves the SQL
    file in place; the marker keeps the boot from looping on it.
    """
    db = dataPath(_DB_NAME)
    marker = marker_path()
    if not db.exists():
        return {'ok': True, 'skipped': 'no retired database'}
    if marker.exists():
        return {'ok': True, 'skipped': 'already imported'}
    wal = db.with_name(db.name + '-wal')
    staged: Path | None = None
    if wal.exists() and wal.stat().st_size > 0:
        # A non-empty WAL is the normal state after a ``taskkill /T /F`` quit,
        # and a read-only connection cannot replay it. Import a private copy
        # instead of refusing: the owner's file is never written, the copy
        # replays its own WAL, and the copy is deleted when the export ends.
        staged, source = _stage_for_import(db)
        logger.info(
            'sqlite retirement: importing a private copy — %s holds %d byte(s) of WAL',
            wal.name, wal.stat().st_size,
        )
    else:
        source = db

    counts: dict[str, int] = {}
    try:
        conn = _connect(source, read_only=staged is None)
        try:
            tables = _user_tables(conn)
            # 1. The markdown record.
            counts['facts'] = _import_facts(conn)
            # 2. The plain row stores.
            for table, (home_kind, name) in _JSON_TABLES.items():
                if table not in tables:
                    continue
                counts[table] = _write_json_rows(
                    home_kind, name, _table_rows(conn, table),
                    dedupe_key='key' if table in ('memory_store', 'internal_state') else '',
                )
            # 3. The telemetry ledgers (append verbatim).
            for table, name in _LEDGER_TABLES.items():
                if table not in tables:
                    continue
                counts[table] = _write_ledger(filestore.TELEMETRY_DIR, name, _table_rows(conn, table))
            for table, name in _LEDGER_STATE_TABLES.items():
                if table not in tables:
                    continue
                counts[table] = _write_ledger(filestore.STATE_DIR, name, _table_rows(conn, table))
            # 4. The multi-section stores.
            if any(t in tables for t in _EXAMS_TABLES):
                counts['exams'] = _export_exams(conn)
            if 'workstreams' in tables or 'workstream_episodes' in tables:
                counts['workstreams'] = _export_workstreams(conn, tables)
            if 'recurring_tasks' in tables or 'recurring_task_runs' in tables:
                counts['recurring_tasks'] = _export_recurring_tasks(conn)
            if 'daemons' in tables:
                counts['daemons'] = _write_dict_rows(
                    filestore.STATE_DIR, 'daemons.json', _table_rows(conn, 'daemons'), 'id'
                )
            if 'harness_jobs' in tables:
                # The runtime store is a dict keyed by job id
                # (harness_jobs.py) — merge by id, never append a ledger.
                counts['harness_jobs'] = _write_dict_rows(
                    filestore.TELEMETRY_DIR, 'harness-jobs.json',
                    _table_rows(conn, 'harness_jobs'), 'id',
                )
            if 'harness_specialists' in tables:
                counts['harness_specialists'] = _write_dict_rows(
                    filestore.STATE_DIR, 'harness-specialists.json',
                    _table_rows(conn, 'harness_specialists'), 'id',
                )
            if 'harness_routines' in tables:
                counts['harness_routines'] = _write_dict_rows(
                    filestore.STATE_DIR, 'harness-routines.json',
                    _table_rows(conn, 'harness_routines'), 'id',
                )
            if 'harness_outcome' in tables:
                counts['harness_outcome'] = _write_dict_rows(
                    filestore.TELEMETRY_DIR, 'harness-outcomes.json',
                    _table_rows(conn, 'harness_outcome'), 'id',
                )
            # 5. The transcripts (sessions dict + per-session jsonl).
            sessions = _table_rows(conn, 'sessions') if 'sessions' in tables else []
            counts['sessions'] = _write_dict_rows(
                filestore.TRANSCRIPTS_DIR, 'sessions.json', sessions, 'id'
            )
            messages = _table_rows(conn, 'messages') if 'messages' in tables else []
            if messages:
                by_session: dict[str, list[dict[str, Any]]] = {}
                max_id = 0
                for row in messages:
                    sid = str(row.get('session_id') or '')
                    if not sid:
                        continue
                    by_session.setdefault(sid, []).append(row)
                    try:
                        max_id = max(max_id, int(row.get('id') or 0))
                    except (TypeError, ValueError):
                        continue
                for sid, rows in by_session.items():
                    from app.services.memory_store.sessions import _session_file

                    rows.sort(key=lambda r: int(r.get('id') or 0))
                    _write_ledger(filestore.TRANSCRIPTS_DIR, _session_file(sid).name, rows)
                counts['messages'] = len(messages)
                _bump_message_seq(max_id)
            # 6. Harness-era ledgers and any table this mapping never named:
            # a table with rows must not be dropped silently — it exports to
            # a telemetry ledger named after itself.
            mapped = (
                set(_LEDGER_TABLES) | set(_LEDGER_STATE_TABLES) | set(_JSON_TABLES)
                | set(_EXAMS_TABLES) | {
                    'facts', 'sessions', 'messages', 'workstreams', 'workstream_episodes',
                    'recurring_tasks', 'recurring_task_runs', 'daemons', 'harness_jobs',
                    'harness_specialists', 'harness_routines', 'harness_outcome',
                    *_GOAL_TABLES,
                }
            )
            for table in tables:
                if table in mapped:
                    continue
                rows = _table_rows(conn, table)
                if not rows:
                    continue
                name = table.replace('_', '-') + '.jsonl'
                counts[table] = _write_ledger(filestore.TELEMETRY_DIR, name, rows)
        finally:
            conn.close()
            if staged is not None:
                shutil.rmtree(staged.parent, ignore_errors=True)
        # 7. The runs ledger is the one table big enough to matter: the
        # retired database held 507,602 rows — 143 MB, ~96% the same cron
        # error — and the file store would hold MORE (277 MB) if every row
        # were written verbatim. The runtime ceiling is what the ledger
        # holds in steady state, so apply it here, while the full history
        # is still in the SQL file for anyone who needs it. The dropped
        # count goes in the marker; nothing else is discarded.
        from app.services import automation_memory

        runs = filestore.home(filestore.TELEMETRY_DIR) / automation_memory._RUNS_FILE
        if runs.exists():
            counts['automationRunsDropped'] = filestore.rotate_jsonl(
                runs, automation_memory._RUNS_LEDGER_MAX_BYTES
            )
        # 8. MEMORY RECOVERY from the rollback log.
        try:
            counts['rollbackRecovered'] = _recover_rollback_memories()
        except Exception as exc:  # noqa: BLE001 — recovery failure must not abort the marker
            logger.error('rollback memory recovery FAILED: %s', exc, exc_info=True)
            counts['rollbackRecovered'] = 0
            counts['rollbackRecoveryError'] = 1  # truthy flag; details are in the log
    except Exception as exc:
        # Failure: log loudly, write NOTHING except the failed marker, leave
        # the database in place, and never loop on it.
        logger.error(
            'SQLite retirement import FAILED — the database is untouched and the '
            'record may be incomplete: %s', exc, exc_info=True,
        )
        filestore.write_json(
            marker,
            {
                'status': 'failed',
                'error': str(exc)[:2000],
                'at': datetime.now(timezone.utc).isoformat(),
                'database': str(db),
            },
        )
        return {'ok': False, 'error': str(exc)}

    filestore.write_json(
        marker,
        {
            'status': 'imported',
            'at': datetime.now(timezone.utc).isoformat(),
            'database': str(db),
            'tables': counts,
            'rollbackRecovered': counts.get('rollbackRecovered', 0),
        },
    )
    recovered = counts.get('rollbackRecovered', 0)
    logger.info(
        'SQLite retired: imported %s; rollback recovery restored %d fact(s)',
        counts, recovered,
    )
    for src in (db, db.with_name(db.name + '-wal'), db.with_name(db.name + '-shm')):
        if not src.exists():
            continue
        try:
            os.replace(src, Path(str(src) + _RETIRED_SUFFIX))
        except OSError as exc:
            # The import landed and the marker is written; a rename that fails
            # (a held handle, a permissions quirk) is logged and retried on the
            # next boot — the marker still says 'imported', so nothing re-imports.
            logger.warning('could not rename %s aside: %s', src, exc)
    return {'ok': True, 'tables': counts, 'rollbackRecovered': recovered}
