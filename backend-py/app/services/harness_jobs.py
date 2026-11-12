"""Long-running harness jobs (distinct from a chat turn).

The ``harness_jobs`` table became a JSON state file,
``<dataDir>/telemetry/harness-jobs.json`` — a dict keyed by job id with the
same fields the row carried. Jobs are few and updated often (one status flip
per wave transition), so a whole-file atomic state beats a ledger here.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.json_narrowing import as_str
from app.services.filestore import TELEMETRY_DIR, home, read_json, write_json

logger = logging.getLogger(__name__)

_JOBS_FILE = 'harness-jobs.json'
_LOCK = threading.RLock()


def _jobs_path() -> Path:
    return home(TELEMETRY_DIR) / _JOBS_FILE


def _load_jobs() -> dict[str, dict[str, Any]]:
    raw = read_json(_jobs_path(), default={})
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}


def _save_jobs(rows: dict[str, dict[str, Any]]) -> None:
    write_json(_jobs_path(), rows)


def create_job(
    session_id: str,
    waves: list[list[dict[str, Any]]] | None = None,
    work_items: list[dict[str, Any]] | None = None,
) -> str:
    job_id = f'job_{uuid.uuid4().hex[:12]}'
    with _LOCK:
        rows = _load_jobs()
        rows[job_id] = {
            'id': job_id,
            'session_id': session_id,
            'status': 'running',
            'dirty': 0,
            'error': '',
            'waves_json': json.dumps(_wave_names(waves or []), ensure_ascii=False),
            'work_items_json': json.dumps(work_items or [], ensure_ascii=False, default=str)[:20000],
            'task_ids': '[]',
            'outcomes_json': '{}',
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'finished_at': None,
        }
        _save_jobs(rows)
    return job_id


def _wave_names(waves: list[list[dict[str, Any]]]) -> list[list[str]]:
    from app.services.workstreams import item_name

    out: list[list[str]] = []
    for wave in waves:
        names: list[str] = []
        for i, item in enumerate(wave):
            try:
                names.append(item_name(item, i))
            except Exception:
                names.append(as_str(item.get('goal'), '')[:40] or f'item_{i}')
        out.append(names)
    return out


def record_lane(job_id: str, name: str, status: str, error: str = '', task_id: str = '') -> None:
    if not job_id or not name:
        return
    try:
        with _LOCK:
            rows = _load_jobs()
            row = rows.get(job_id)
            if not row:
                return
            try:
                loaded = json.loads(str(row.get('outcomes_json') or '{}'))
                raw = loaded if isinstance(loaded, dict) else {}
            except Exception:
                raw = {}
            prev_item = raw.get(name) if isinstance(raw, dict) else None
            prev: dict[str, Any] = prev_item if isinstance(prev_item, dict) else {}
            raw[name] = {
                'status': status,
                'error': error[:400],
                'taskId': task_id or as_str(prev.get('taskId'), ''),
            }
            row['outcomes_json'] = json.dumps(raw, ensure_ascii=False)
            _save_jobs(rows)
    except Exception:
        logger.debug('record_lane failed', exc_info=True)


def _parse_outcomes(row: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = json.loads(str(row.get('outcomes_json') or '{}'))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def attach_task(job_id: str, task_id: str) -> None:
    if not job_id or not task_id:
        return
    try:
        with _LOCK:
            rows = _load_jobs()
            row = rows.get(job_id)
            if not row:
                return
            try:
                ids = json.loads(str(row.get('task_ids') or '[]'))
            except Exception:
                ids = []
            if not isinstance(ids, list):
                ids = []
            if task_id not in ids:
                ids.append(task_id)
            row['task_ids'] = json.dumps(ids)
            _save_jobs(rows)
    except Exception:
        logger.debug('attach_task failed', exc_info=True)


def finish_job(
    job_id: str, status: str, *, dirty: bool | None = None, error: str = ''
) -> None:
    """Close a job.

    ``dirty`` is tri-state on purpose. It used to default to False, so the
    ordinary close — ``finish_job(job_id, 'cancelled')`` from cancel_job — wrote
    dirty=0 and blanked the error, erasing the mark_dirty "a worker mutated the
    environment" receipt. The fleet then settled and the run was reported clean
    even though a worker had changed things. None means "not supplied, keep the
    stored value"; True/False set it.

    A terminal status is terminal: a cancelled job is never overwritten by the
    wave driver's later finish_job('completed').
    """
    if not job_id:
        return
    try:
        with _LOCK:
            rows = _load_jobs()
            row = rows.get(job_id)
            if not row:
                return
            if str(row.get('status')) in ('cancelled', 'completed', 'failed'):
                return
            row['status'] = status
            if dirty:
                row['dirty'] = 1
            if (error or '') != '':
                row['error'] = error[:2000]
            if not row.get('finished_at'):
                row['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            _save_jobs(rows)
    except Exception:
        logger.debug('finish_job failed', exc_info=True)


def sweep_orphaned_jobs() -> int:
    """Resolve every job left ``running`` by the previous process.

    The wave driver records the job in ``create_job`` and flips it to a terminal
    status in ``finish_job``. The packaged desktop quit is ``taskkill /T /F``
    (see ``src-tauri/src/backend.rs``), so the lifespan shutdown tail never runs
    and a quit mid-fleet leaves the job at ``running`` FOREVER. Nothing swept it
    at boot — only ``subagent_runs`` did, via ``sweep_orphaned_runs`` — so
    ``harness_playbook`` computed ``workingCount`` from a dead job and the
    sidebar reported a session as working indefinitely.

    Same contract as the sub-agent sweep: runs before any request is served, so
    every active row is by definition orphaned, and the partial work the job
    managed to record is left alone rather than destroyed.
    """
    try:
        with _LOCK:
            rows = _load_jobs()
            count = 0
            for row in rows.values():
                if str(row.get('status')) != 'running':
                    continue
                row['status'] = 'failed'
                if not row.get('error'):
                    row['error'] = 'Lost: the backend restarted while this job was running.'
                if not row.get('finished_at'):
                    row['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                count += 1
            if count:
                _save_jobs(rows)
                logger.info('marked %d orphaned harness job(s) as failed', count)
            return count
    except Exception:
        logger.debug('harness job orphan sweep skipped', exc_info=True)
        return 0


def mark_dirty(job_id: str, note: str = '') -> None:
    if not job_id:
        return
    try:
        with _LOCK:
            rows = _load_jobs()
            row = rows.get(job_id)
            if not row:
                return
            row['dirty'] = 1
            if not row.get('error'):
                row['error'] = (
                    note[:2000]
                    or 'Worker mutated the environment then exited without a clean episode.'
                )
            _save_jobs(rows)
    except Exception:
        logger.debug('mark_dirty failed', exc_info=True)


def _row_to_job(r: dict[str, Any]) -> dict[str, Any]:
    try:
        waves = json.loads(str(r.get('waves_json') or '[]'))
    except Exception:
        waves = []
    try:
        tasks = json.loads(str(r.get('task_ids') or '[]'))
    except Exception:
        tasks = []
    return {
        'id': r.get('id'),
        'sessionId': r.get('session_id'),
        'status': r.get('status'),
        'dirty': bool(r.get('dirty')),
        'error': r.get('error') or '',
        'waves': waves,
        'taskIds': tasks,
        'outcomes': _parse_outcomes(r),
        'createdAt': r.get('created_at'),
        'finishedAt': r.get('finished_at'),
    }


def list_jobs(session_id: str, *, limit: int = 30) -> list[dict[str, Any]]:
    rows = [
        row
        for row in _load_jobs().values()
        if str(row.get('session_id') or '') == session_id
    ]
    rows.sort(key=lambda row: str(row.get('created_at') or ''), reverse=True)
    return [_row_to_job(row) for row in rows[: max(1, int(limit))]]


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        row = _load_jobs().get(job_id)
    return _row_to_job(row) if row else None


async def cancel_job(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if not job:
        return {'status': 'error', 'error': 'job not found'}
    from app.services.runtime_services import get_orchestrator

    orch = get_orchestrator()
    stopped = 0
    for tid in job.get('taskIds') or []:
        try:
            if await orch.terminate(str(tid)):
                stopped += 1
        except Exception:
            pass
    finish_job(job_id, 'cancelled')
    return {'status': 'cancelled', 'jobId': job_id, 'stopped': stopped}


async def cancel_wave(job_id: str, wave_index: int) -> dict[str, Any]:
    job = get_job(job_id)
    if not job:
        return {'status': 'error', 'error': 'job not found'}
    waves = job.get('waves') or []
    if wave_index < 0 or wave_index >= len(waves):
        return {'status': 'error', 'error': 'wave not found'}
    names = [n for n in (waves[wave_index] or []) if n]
    outcomes = job.get('outcomes') or {}
    from app.services.runtime_services import get_orchestrator

    orch = get_orchestrator()
    stopped = 0
    for name in names:
        info_item = outcomes.get(name) if isinstance(outcomes, dict) else None
        info: dict[str, Any] = info_item if isinstance(info_item, dict) else {}
        tid = as_str(info.get('taskId'), '')
        st = as_str(info.get('status'), '')
        if tid and st in ('running', 'pending', ''):
            try:
                if await orch.terminate(tid):
                    stopped += 1
            except Exception:
                pass
        record_lane(job_id, name, 'cancelled', 'Stopped with this wave', tid)
    return {'status': 'ok', 'jobId': job_id, 'wave': wave_index, 'stopped': stopped, 'names': names}
