"""Attention, search, idle pause, scheduled routines, skill-from-episode.

The ``workstream_reads`` table (per-session read markers) became
``<dataDir>/state/workstream-reads.json``; the scheduled-routine fields live
in the routines state file owned by ``harness_playbook``.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.json_narrowing import as_str
from app.lib.paths import dataPath
from app.services.filestore import STATE_DIR, home, read_json, write_json

logger = logging.getLogger(__name__)

IDLE_SECONDS = 24 * 60 * 60
_ACTIVITY_FILE = 'harness-activity.json'
_READS_FILE = 'workstream-reads.json'


def _reads_path():
    return home(STATE_DIR) / _READS_FILE


def touch_activity() -> None:
    path = dataPath(_ACTIVITY_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'lastUserAt': time.time()}), encoding='utf-8')


def last_activity_at() -> float:
    path = dataPath(_ACTIVITY_FILE)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return float(data.get('lastUserAt') or 0)
    except Exception:
        return time.time()


def is_unattended(idle_seconds: int = IDLE_SECONDS) -> bool:
    last = last_activity_at()
    if last <= 0:
        return False
    return (time.time() - last) >= idle_seconds


def mark_read(session_id: str, name: str, seq: int = 0) -> None:
    reads = read_json(_reads_path(), default={})
    if not isinstance(reads, dict):
        reads = {}
    per_session = reads.get(session_id)
    if not isinstance(per_session, dict):
        per_session = {}
    per_session[name] = {
        'last_seen_seq': int(seq or 0),
        'seen_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    reads[session_id] = per_session
    write_json(_reads_path(), reads)


def last_seen_seq(session_id: str, name: str) -> int:
    reads = read_json(_reads_path(), default={})
    if not isinstance(reads, dict):
        return 0
    per_session = reads.get(session_id)
    if not isinstance(per_session, dict):
        return 0
    entry = per_session.get(name)
    if not isinstance(entry, dict):
        return 0
    return int(entry.get('last_seen_seq') or 0)


def annotate_attention(session_id: str, rows: list[dict[str, Any]], running_names: set[str] | None = None) -> list[dict[str, Any]]:
    live = running_names or set()
    for row in rows:
        name = as_str(row.get('name'), '')
        latest = row.get('latest') or {}
        seq = int(latest.get('seq') or 0)
        seen = last_seen_seq(session_id, name)
        if name in live:
            row['attention'] = 'working'
        elif row.get('dirty'):
            row['attention'] = 'needs'
        elif seq > seen:
            row['attention'] = 'unread'
        else:
            row['attention'] = 'idle'
        row['unread'] = seq > seen
    return rows


def needs_attention_summary() -> list[dict[str, object]]:
    """Per-session workstream attention counts, across all sessions.

    Powers the sidebar "needs handoff" dots. Only sessions with at least one
    running or attention-needing workstream are returned; never raises.
    """
    from app.services.workstreams import list_session_ids, list_workstreams

    out: list[dict[str, object]] = []
    for sid in list_session_ids():
        if not sid:
            continue
        try:
            annotated = annotate_attention(sid, list_workstreams(sid))
        except Exception:
            continue
        needs = sum(1 for w in annotated if w.get('attention') in ('needs', 'unread'))
        working = sum(1 for w in annotated if w.get('attention') == 'working')
        if needs or working:
            out.append({'sessionId': sid, 'needs': needs, 'working': working})
    return out


def search_harness(session_id: str, query: str, *, workspace: str = '') -> dict[str, Any]:
    from app.services.harness_playbook import list_routines, list_specialists
    from app.services.workstreams import list_episodes, list_workstreams

    q = (query or '').strip().lower()
    streams = list_workstreams(session_id)
    hits: list[dict[str, Any]] = []
    if not q:
        return {'workstreams': streams[:12], 'episodes': [], 'routines': list_routines(session_id, workspace)[:8]}
    for ws in streams:
        blob = f"{ws.get('name')} {(ws.get('latest') or {}).get('summary') or ''} {(ws.get('latest') or {}).get('next') or ''}".lower()
        if q in blob:
            hits.append({'kind': 'workstream', **ws})
        for ep in list_episodes(session_id, as_str(ws.get('name'), ''), limit=20):
            eblob = f"{ep.get('summary') or ''} {ep.get('next') or ''} {' '.join(ep.get('artifacts') or [])}".lower()
            if q in eblob:
                hits.append({'kind': 'episode', 'workstream': ws.get('name'), **ep})
    for rtn in list_routines(session_id, workspace):
        if q in f"{rtn.get('name')} {rtn.get('goal')} {rtn.get('workstream')}".lower():
            hits.append({'kind': 'routine', **rtn})
    for spec in list_specialists(session_id, workspace):
        if q in f"{spec.get('name')} {spec.get('workstream')}".lower():
            hits.append({'kind': 'specialist', **spec})
    return {'hits': hits[:40], 'query': query}


def set_routine_schedule(routine_id: str, schedule: str, paused: bool | None = None) -> dict[str, Any]:
    from app.services.harness_playbook import get_routine, update_routine_fields

    fields: dict[str, object] = {'schedule': schedule.strip()}
    if paused is not None:
        fields['paused'] = 1 if paused else 0
    row = update_routine_fields(routine_id, **fields)
    if not row:
        row = get_routine(routine_id)
    if not row:
        raise ValueError('Routine not found')
    return row


def due_scheduled_routines(now: datetime | None = None) -> list[dict[str, Any]]:
    from app.services.automations_schedule import matches_cron
    from app.services.harness_playbook import _row_routine, scheduled_routines

    stamp = now or datetime.now(timezone.utc)
    try:
        rows = [
            r
            for r in scheduled_routines()
            if str(r.get('schedule') or '') and not int(r.get('paused') or 0)
        ]
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    minute_key = stamp.strftime('%Y-%m-%dT%H:%M')
    for r in rows:
        try:
            sched = as_str(r.get('schedule'), '')
            if not sched or not matches_cron(sched, stamp):
                continue
            last = as_str(r.get('last_run'), '')
            if last.startswith(minute_key):
                continue
            out.append(_row_routine(r))
        except Exception:
            continue
    return out


def mark_routine_ran(routine_id: str) -> None:
    from app.services.harness_playbook import update_routine_fields

    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    update_routine_fields(routine_id, last_run=now)


async def fire_due_routines() -> int:
    if is_unattended():
        logger.info('harness routines paused — user unattended >24h')
        return 0
    due = due_scheduled_routines()
    if not due:
        return 0
    from types import SimpleNamespace

    from app.services.harness_playbook import routine_work_item
    from app.services.runtime_services import get_orchestrator
    from app.services.tools.spawn_subagents_tool import executeSpawnSubagents

    n = 0
    orch = get_orchestrator()
    for rtn in due:
        sid = as_str(rtn.get('sessionId'), '')
        if not sid:
            continue
        session = SimpleNamespace(
            id=sid,
            workspacePath=as_str(rtn.get('workspacePath'), ''),
            agent_id='general',
        )
        item = routine_work_item(sid, rtn)
        try:
            await executeSpawnSubagents(orch, session, [item], mode='auto', emit=None, background=True)
            mark_routine_ran(as_str(rtn.get('id'), ''))
            n += 1
        except Exception:
            logger.debug('scheduled routine failed', exc_info=True)
    return n
