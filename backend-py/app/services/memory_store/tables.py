"""File-backed homes for the content-domain tables (SQLite retired).

Every table the brain database used to carry now lives in ONE of the four
store homes (see ``app.services.filestore``):

  state/       kv.json, internal-state.json, proposals.json, exams.json,
               blackboard.json, episodic-timeline.json, episodes.json,
               goals.json, config-audit.json, daemons.json, workstreams.json,
               recurring-tasks.json, refine-entries.json …
  telemetry/   decision-ledger.jsonl, usage-events.jsonl, lifecycle.jsonl,
               routing-evidence.jsonl, failure-fingerprints.json,
               session-topics.json, turn-outcomes.jsonl (the transcript
               domain's runtime files — this module only reads those it
               does not own) …
  memory/      the markdown fact record (``mirror.py`` owns it)

Rows keep their SQL-era snake_case column names verbatim — every read door
already camelCases them on the way out (``wire._row_as_wire``), so the wire
shapes are untouched. JSON stores are whole-file, atomic (filestore rule 1);
JSONL stores are append-only ledgers with rotation caps (rules 2–4).

Module-level locks: writers are the backend's own threads and every whole-file
write is an atomic replace, so a per-store lock around read-modify-write
cycles is the whole serialization story. It replaces SQLite's write lock.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from app.services import filestore

logger = logging.getLogger('august.memory_store.tables')

# Ledgers cap themselves at write time (filestore rule 3) instead of waiting
# for a date-based sweep that has to keep pace with ingest.
_DECISION_LEDGER_MAX_BYTES = 10 * 1024 * 1024
_USAGE_EVENTS_MAX_BYTES = 20 * 1024 * 1024
_LIFECYCLE_MAX_BYTES = 10 * 1024 * 1024
_ROUTING_EVIDENCE_MAX_BYTES = 10 * 1024 * 1024

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def store_lock(name: str) -> threading.RLock:
    """The per-store lock serializing read-modify-write cycles."""
    with _locks_guard:
        lock = _locks.get(name)
        if lock is None:
            lock = threading.RLock()
            _locks[name] = lock
        return lock


def state_file(name: str) -> Path:
    return filestore.home(filestore.STATE_DIR) / name


def telemetry_file(name: str) -> Path:
    return filestore.home(filestore.TELEMETRY_DIR) / name


def record_dir() -> Path:
    return filestore.home(filestore.MEMORY_DIR)


# ── whole-file JSON row stores ──────────────────────────────────────────────


def load_rows(path: Path) -> list[dict[str, Any]]:
    """Rows of a JSON-list store. A corrupted store reads as empty and the
    read failure is logged with the path (filestore.read_json's contract)."""
    data = filestore.read_json(path, default=[])
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def save_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    filestore.write_json(path, rows)


def next_id(rows: list[dict[str, Any]]) -> int:
    """MAX(id) + 1 over a row store; 1 for an empty one."""
    top = 0
    for row in rows:
        try:
            top = max(top, int(row.get('id') or 0))
        except (TypeError, ValueError):
            continue
    return top + 1


# ── named content stores ────────────────────────────────────────────────────


def kv_file() -> Path:
    """The ``memory_store`` KV table (machine registry + user notes)."""
    return state_file('kv.json')


def internal_state_file() -> Path:
    return state_file('internal-state.json')


def proposals_file() -> Path:
    return state_file('proposals.json')


def timeline_file() -> Path:
    """``episodic_timeline``."""
    return state_file('episodic-timeline.json')


def episodes_file() -> Path:
    return state_file('episodes.json')


def blackboard_file() -> Path:
    return state_file('blackboard.json')


def config_audit_file() -> Path:
    return state_file('config-audit.json')


def exams_file() -> Path:
    """exams + exam_questions + exam_attempts in one file, three sections."""
    return state_file('exams.json')


def goals_file() -> Path:
    return state_file('goals.json')


def session_topics_file() -> Path:
    return telemetry_file('session-topics.json')


def failure_fingerprints_file() -> Path:
    return telemetry_file('failure-fingerprints.json')


def decision_ledger_file() -> Path:
    return telemetry_file('decision-ledger.jsonl')


def usage_events_file() -> Path:
    return telemetry_file('usage-events.jsonl')


def lifecycle_file() -> Path:
    return telemetry_file('lifecycle.jsonl')


def routing_evidence_file() -> Path:
    return telemetry_file('routing-evidence.jsonl')


def turn_outcomes_file() -> Path:
    """The turn-outcomes ledger. RUNTIME ownership is the transcript domain's;
    this module reads it (recall quality, latency stats) and the privacy wipe
    clears it. Rows are camelCase wire dicts, as written by the one-time
    SQLite import and by the transcript-domain writers."""
    return telemetry_file('turn-outcomes.jsonl')


def load_section(path: Path, section: str) -> list[dict[str, Any]]:
    """One section of a multi-table JSON store (``exams.json``)."""
    data = filestore.read_json(path, default={})
    if isinstance(data, dict):
        rows = data.get(section)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def save_section(path: Path, section: str, rows: list[dict[str, Any]]) -> None:
    with store_lock(str(path)):
        data = filestore.read_json(path, default={})
        doc: dict[str, object] = data if isinstance(data, dict) else {}
        doc[section] = rows
        filestore.write_json(path, doc)


# ── JSONL ledgers ───────────────────────────────────────────────────────────


def append_ledger(path: Path, row: dict[str, Any], *, max_bytes: int = 0) -> None:
    """Append one row to a JSONL ledger, then enforce its rotation cap."""
    filestore.append_jsonl(path, row)
    if max_bytes > 0:
        filestore.rotate_jsonl(path, max_bytes)


def rewrite_ledger(path: Path, rows: list[dict[str, Any]]) -> None:
    filestore.rewrite_jsonl(path, [dict(r) for r in rows])


def clear_ledger(path: Path) -> int:
    """Empty a ledger in place. Returns the rows it held (for purge counts)."""
    with store_lock(str(path)):
        rows = filestore.load_jsonl(path)
        if rows:
            rewrite_ledger(path, [])
        return len(rows)


def ledger_rows(path: Path) -> list[dict[str, Any]]:
    return [row for row in filestore.load_jsonl(path) if isinstance(row, dict)]


def clear_rows(path: Path) -> int:
    """Empty a whole-file JSON row store. Returns the rows it held."""
    with store_lock(str(path)):
        rows = load_rows(path)
        if rows:
            save_rows(path, [])
        return len(rows)


def clear_record() -> int:
    """Delete every fact file (the markdown record).

    The privacy purge's "erase my memory". Returns the number of fact files
    removed. The MEMORY.md index is the caller's business (one
    ``mirror.rebuild_mirror()`` after the sweep regenerates it empty).
    """
    root = record_dir()
    removed = 0
    for child in root.glob('*.md'):
        try:
            child.unlink()
            removed += 1
        except OSError as exc:
            logger.warning('record purge could not remove %s: %s', child, exc)
    return removed


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


__all__ = [
    'append_ledger',
    'blackboard_file',
    'clear_ledger',
    'clear_record',
    'clear_rows',
    'config_audit_file',
    'decision_ledger_file',
    'episodes_file',
    'exams_file',
    'failure_fingerprints_file',
    'goals_file',
    'internal_state_file',
    'kv_file',
    'ledger_rows',
    'lifecycle_file',
    'load_rows',
    'load_section',
    'next_id',
    'proposals_file',
    'record_dir',
    'rewrite_ledger',
    'routing_evidence_file',
    'save_rows',
    'save_section',
    'session_topics_file',
    'state_file',
    'store_lock',
    'telemetry_file',
    'timeline_file',
    'turn_outcomes_file',
    'usage_events_file',
]
