"""Recurring-task daemon (B7) — user-defined reminders that fire at the
right moment.

Trigger grammar (plain text, matched case-insensitively):
  * "every N minutes|hours|days"  — interval-based; fires when the interval
    has elapsed since the last fire.
  * "when I open <X>" / "on open" — fires once per 24h when a workbench
    session starts (workspace-aware: ``<X>`` matches the workspace name).
  * anything else — treated as "on open" with a workspace match.

The ``recurring_tasks`` / ``recurring_task_runs`` tables became one JSON
state file, ``<dataDir>/state/recurring-tasks.json`` — ``{'tasks': [...],
'runs': {taskId: [fires]}}`` with the per-task run window capped at 20.

Firing never raises: it records ``last_fired_at`` and returns the messages
so the caller can surface them (SSE → notification bell / toast).
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.json_narrowing import as_int, as_str
from app.services.filestore import STATE_DIR, home, read_json, write_json

logger = logging.getLogger(__name__)

_INTERVAL_RE = re.compile(r'every\s+(\d+)\s*(minute|minutes|min|hour|hours|hr|day|days)', re.IGNORECASE)
_OPEN_RE = re.compile(r'(when\s+i\s+open|when\s+opening|on\s+open|whenever\s+i\s+open)', re.IGNORECASE)
_DAY_SECONDS = 24 * 60 * 60

_FILE = 'recurring-tasks.json'
_LOCK = threading.RLock()


def _path() -> Path:
    return home(STATE_DIR) / _FILE


def _load() -> tuple[list[dict], dict[str, list]]:
    """(tasks, runs-by-taskId) — validated on read."""
    raw = read_json(_path(), default={})
    if not isinstance(raw, dict):
        return ([], {})
    tasks_raw = raw.get('tasks')
    runs_raw = raw.get('runs')
    tasks = [t for t in (tasks_raw or []) if isinstance(t, dict)] if isinstance(tasks_raw, list) else []
    runs = (
        {str(k): v for k, v in runs_raw.items() if isinstance(v, list)}
        if isinstance(runs_raw, dict)
        else {}
    )
    return (tasks, runs)


def _save(tasks: list[dict], runs: dict[str, list]) -> None:
    write_json(_path(), {'tasks': tasks, 'runs': runs})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_tasks(active_only: bool = True) -> list[dict]:
    with _LOCK:
        tasks, _runs = _load()
    if active_only:
        tasks = [t for t in tasks if int(t.get('active') or 0) == 1]
    tasks.sort(key=lambda t: int(t.get('id') or 0), reverse=True)
    return [
        {
            'id': t.get('id'),
            'trigger': t.get('trigger'),
            'message': t.get('message'),
            'model': t.get('model'),
            'active': t.get('active'),
            'created_at': t.get('created_at'),
            'last_fired_at': t.get('last_fired_at'),
        }
        for t in tasks
    ]


def add_task(trigger: str, message: str, model: str = '') -> int | None:
    trigger = (trigger or '').strip()
    message = (message or '').strip()
    if not trigger or not message:
        return None
    try:
        with _LOCK:
            tasks, runs = _load()
            next_id = 1 + max((as_int(t.get('id'), 0) for t in tasks), default=0)
            tasks.append(
                {
                    'id': next_id,
                    'trigger': trigger[:300],
                    'message': message[:2000],
                    'model': (model or '').strip()[:120],
                    'active': 1,
                    'created_at': _now_iso(),
                    'last_fired_at': None,
                }
            )
            _save(tasks, runs)
            return next_id
    except Exception as exc:
        logger.error('recurring task add failed: %s', exc)
        return None


def delete_task(task_id: int) -> bool:
    try:
        with _LOCK:
            tasks, runs = _load()
            kept = [t for t in tasks if as_int(t.get('id'), 0) != as_int(task_id, 0)]
            if len(kept) == len(tasks):
                return False
            runs.pop(str(task_id), None)
            _save(kept, runs)
            return True
    except Exception as exc:
        logger.debug('recurring task delete failed: %s', exc)
        return False


def set_active(task_id: int, active: bool) -> bool:
    """Pause (False) / resume (True) one task — parity with the automations
    store's pause/resume; a paused task never fires."""
    try:
        with _LOCK:
            tasks, runs = _load()
            changed = False
            for t in tasks:
                if as_int(t.get('id'), 0) == as_int(task_id, 0):
                    t['active'] = 1 if active else 0
                    changed = True
            if not changed:
                return False
            _save(tasks, runs)
            return True
    except Exception as exc:
        logger.debug('recurring task set_active failed: %s', exc)
        return False


def get_runs(task_id: int, limit: int = 20) -> list[dict]:
    """Newest-first per-fire history (write-time capped to 20 rows)."""
    try:
        with _LOCK:
            _tasks, runs = _load()
        fires = list(runs.get(str(task_id)) or [])[-max(1, min(int(limit or 20), 20)) :]
        fires.reverse()
        return [{'fired_at': f.get('fired_at'), 'message': f.get('message')} for f in fires if isinstance(f, dict)]
    except Exception as exc:
        logger.debug('recurring task runs read failed: %s', exc)
        return []


def _record_run(runs: dict[str, list], task_id: object, message: str) -> None:
    """Append one fire to the per-task window (capped at 20 rows)."""
    key = str(task_id)
    fires = list(runs.get(key) or [])
    fires.append({'fired_at': _now_iso(), 'message': message[:300]})
    runs[key] = fires[-20:]


def _interval_seconds(trigger: str) -> int | None:
    m = _INTERVAL_RE.search(trigger)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith('day'):
        return max(60, n * 86400)
    if unit.startswith('hour') or unit.startswith('hr'):
        return max(60, n * 3600)
    return max(60, n * 60)


def _matches_workspace(trigger: str, workspace_name: str) -> bool:
    if not workspace_name:
        return True
    # "when I open <X>" — match X against the workspace folder name.
    m = re.search(r'(?:open|opening)\s+(?:up\s+)?["\']?([^"\']+)["\']?', trigger, re.IGNORECASE)
    if m:
        target = m.group(1).strip().strip('.')
        if not target:
            return True
        return target.lower() in workspace_name.lower() or workspace_name.lower() in target.lower()
    return True


_AGENT_DIRECTIVE_RE = re.compile(r'\[agent:([\w.-]+)(?:\s+model:([\w.\-/@]+))?\]', re.IGNORECASE)


def parse_agent_directive(message: str) -> tuple[str, str, str]:
    """Extract an optional ``[agent:ID model:MODEL]`` directive from a task message.

    Returns ``(clean_message, agent_id, model)`` — the directive is stripped
    from the message, and blank strings mean "not set". Lets a recurring task
    dispatch a sub-agent with a chosen agent + model on schedule.
    """
    m = _AGENT_DIRECTIVE_RE.search(message or '')
    if not m:
        return (message or '', '', '')
    clean = _AGENT_DIRECTIVE_RE.sub('', message).strip()
    return (clean, m.group(1) or '', m.group(2) or '')


def check_and_fire(session_id: str, workspace_path: str = '') -> list[tuple[str, str]]:
    """Evaluate all active tasks against a session start / turn.

    Returns ``(message, model)`` pairs of tasks that fire now — ``model`` is
    the task's pinned sub-agent model ('' when unset; the ``[agent:ID
    model:MODEL]`` text directive still wins when present). Records each
    task's ``last_fired_at``. Never raises.
    """
    fired: list[tuple[str, str]] = []
    try:
        workspace_name = Path(workspace_path).name if workspace_path else ''
        now = datetime.now(timezone.utc)
        for task in list_tasks(active_only=True):
            trigger = as_str(task.get('trigger'), '')
            message = as_str(task.get('message'), '')
            last = task.get('last_fired_at')
            interval = _interval_seconds(trigger)
            if interval is not None:
                # Interval task: fire when elapsed since last fire.
                if last:
                    try:
                        last_dt = datetime.fromisoformat(str(last))
                        if last_dt.tzinfo is None:
                            last_dt = last_dt.replace(tzinfo=timezone.utc)
                        if (now - last_dt).total_seconds() < interval:
                            continue
                    except (ValueError, TypeError):
                        pass
            else:
                # "On open" task: fire at most once per 24h.
                if last:
                    try:
                        last_dt = datetime.fromisoformat(str(last))
                        if last_dt.tzinfo is None:
                            last_dt = last_dt.replace(tzinfo=timezone.utc)
                        if (now - last_dt).total_seconds() < _DAY_SECONDS:
                            continue
                    except (ValueError, TypeError):
                        pass
                if not _matches_workspace(trigger, workspace_name):
                    continue
            try:
                with _LOCK:
                    tasks, runs = _load()
                    target = next(
                        (t for t in tasks if as_int(t.get('id'), 0) == as_int(task.get('id'), 0)), None
                    )
                    if target is None:
                        continue
                    target['last_fired_at'] = _now_iso()
                    _record_run(runs, target.get('id'), message)
                    _save(tasks, runs)
            except Exception as exc:
                # Do NOT fall through to fired.append(). `last_fired_at` is the
                # only thing standing between this task and firing again on the
                # next call, so reporting a fire whose receipt was never written
                # produces a duplicate notification every cycle. `fired` is what
                # the caller acts on, so it must mean "fired AND recorded".
                logger.warning(
                    'recurring task %s not recorded as fired: %s',
                    task.get('id'),
                    exc,
                )
                continue
            fired.append((message, as_str(task.get('model'), '')))
    except Exception as exc:
        logger.debug('recurring task check failed: %s', exc)
    return fired
