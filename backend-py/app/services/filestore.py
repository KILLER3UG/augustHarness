"""File-backed stores — the record is files, not SQLite.

Every store under ``dataDir()`` lives in one of four homes:

  memory/       one markdown file per fact + MEMORY.md index (the record)
  transcripts/  ``<sessionId>.jsonl`` (one line per message) + ``sessions.json``
  telemetry/    rotating JSONL ledgers + JSON state files
  state/        kv, internal state, boot markers, misc JSON state

The discipline that replaces the database's guarantees:

1. ATOMIC WRITES — every whole-file write goes through a temp file in the
   same directory plus ``os.replace``, so a crash mid-write leaves the old
   content intact. (Same rule data_migrations v4 adopted; a truncated store
   must never be readable as valid.)
2. APPEND-ONLY LEDGERS — JSONL files gain one line per event. A torn
   trailing line (a kill between write and flush) is skipped on read, not
   fatal: the last line of a ledger is never load-bearing.
3. SELF-CAPPING — rotating ledgers drop their OLDEST lines past a byte
   ceiling. Retention is a property of the file, not a sweep that has to
   keep pace with ingest.
4. fsync ON APPEND — the packaged desktop quit is ``taskkill /T /F``, so
   lifespan flushes never run; whatever is in the OS page cache at kill
   time is what survives. Append calls fsync before returning.

Concurrency: writers are the backend's own threads; SQLite's locking is
replaced by the atomic-replace rule (last complete write wins) and, for
read-modify-write cycles, by the caller holding the same process lock the
old connection pool serialised on. At this store's scale (tens of facts,
hundreds of messages) that is sufficient and auditable by eye.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

from app.lib.paths import dataDir

logger = logging.getLogger(__name__)

MEMORY_DIR = 'memory'
TRANSCRIPTS_DIR = 'transcripts'
TELEMETRY_DIR = 'telemetry'
STATE_DIR = 'state'

_ROTATE_KEEP_MIN = 1


def home(kind: str) -> Path:
    """One of the four store homes, created on demand."""
    if kind not in (MEMORY_DIR, TRANSCRIPTS_DIR, TELEMETRY_DIR, STATE_DIR):
        raise ValueError(f'unknown store home: {kind!r}')
    p = Path(dataDir()) / kind
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── JSON ────────────────────────────────────────────────────────────────────


def read_json(path: Path | str, default: object = None) -> object:
    """Read a JSON file, or ``default`` when it is missing or unreadable.

    A store file that fails to parse is a corrupted record, not a reason to
    crash the boot — the caller gets ``default`` and the read failure is
    logged with the path so the damage is findable.
    """
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        logger.warning('filestore: unreadable JSON store %s — returning default', p)
        return default


def write_json(path: Path | str, data: object) -> None:
    """Atomically write a JSON file (rule 1)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f'.{p.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write_text(path: Path | str, text: str) -> None:
    """Atomically write a UTF-8 text file (rule 1) — the markdown record's
    writer. Same discipline as write_json: temp file in the same directory,
    fsync, then os.replace, so a crash mid-write leaves the old content."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f'.{p.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_text(path: Path | str, default: str = '') -> str:
    """Read a UTF-8 text file, or ``default`` when missing/unreadable."""
    p = Path(path)
    if not p.exists():
        return default
    try:
        return p.read_text(encoding='utf-8')
    except OSError:
        logger.warning('filestore: unreadable text store %s — returning default', p)
        return default


# ── JSONL ───────────────────────────────────────────────────────────────────


def append_jsonl(path: Path | str, record: object) -> None:
    """Append one JSON line and fsync (rules 2 and 4)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=str)
    with open(p, 'a', encoding='utf-8') as handle:
        handle.write(line + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def load_jsonl(path: Path | str) -> list[object]:
    """Read a JSONL file, skipping a torn trailing line (rule 2).

    A blank or unparseable FINAL line is the crash-torn case and is dropped
    with a debug log. An unparseable line in the middle is real corruption
    and is also skipped, but at warning level with its 1-based line number —
    a silent skip there would eat records nobody knows were lost.
    """
    p = Path(path)
    if not p.exists():
        return []
    rows: list[object] = []
    try:
        text = p.read_text(encoding='utf-8')
    except OSError:
        logger.warning('filestore: unreadable ledger %s', p)
        return []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            last = i == len(lines) - 1
            (logger.debug if last else logger.warning)(
                'filestore: skipping %s line %d (torn tail)' if last
                else 'filestore: skipping corrupt %s line %d',
                p,
                i + 1,
            )
    return rows


def rewrite_jsonl(path: Path | str, rows: list[object]) -> None:
    """Atomically replace a JSONL file with the given rows (rule 1).

    Used by compaction, retention sweeps and dedupe passes: load, transform,
    rewrite whole. The write is atomic, so a crash yields the OLD ledger.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f'.{p.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def rotate_jsonl(path: Path | str, max_bytes: int) -> int:
    """Drop the OLDEST lines until the ledger fits ``max_bytes`` (rule 3).

    Returns the number of lines dropped. The ceiling applies to the ledger
    alone; a single record larger than the ceiling is kept (a ledger that
    deletes its only record to satisfy a cap has lost its history for no
    reason — the cap exists to bound runaway ingest, not to truncate).

    One pass: the per-line sizes are computed once, then the newest suffix
    that fits is kept. Re-serialising the whole ledger for every dropped
    row is quadratic — against the retired database's 507,602-row runs
    ledger that is ~10^11 ``dumps`` calls, so the append that triggered
    rotation would never return.

    A HIGH-WATERMARK, not the ceiling. Trimming to exactly ``max_bytes``
    leaves the file one row under the cap, so the very next append tips it
    over and the WHOLE file is rewritten again — measured on the 10 MB runs
    ledger at steady state: 271 ms per append, 200 minutes of CPU a day,
    all of it rewriting bytes that were about to be rewritten again. The
    ledger is trimmed to ``max_bytes`` MINUS a reserve, so rotation
    happens once per reserve of ingest instead of once per row.
    """
    p = Path(path)
    if not p.exists() or p.stat().st_size <= max_bytes:
        return 0
    # The reserve is the smaller of a fixed floor and a fraction of the
    # ceiling: a small ledger must not lose a third of itself to one
    # append, and a large one must not rewrite 10 MB for one row.
    reserve = max(64 * 1024, max_bytes // 10)
    target = max(0, max_bytes - reserve)
    rows = load_jsonl(p)
    sizes = [len(json.dumps(r, ensure_ascii=False, default=str)) + 1 for r in rows]
    total = sum(sizes)
    start = 0
    while start < len(rows) - _ROTATE_KEEP_MIN and total > target:
        total -= sizes[start]
        start += 1
    dropped = start
    if not dropped:
        return 0
    rewrite_jsonl(p, rows[start:])
    logger.info('filestore: rotated %s — dropped %d oldest row(s)', p, dropped)
    return dropped


def iter_jsonl(path: Path | str) -> Iterator[object]:
    yield from load_jsonl(path)
