"""Named specialists, episode routines, and ping-vs-continue policy.

The ``harness_specialists`` / ``harness_routines`` tables became two JSON
state files under ``<dataDir>/state/`` (``harness-specialists.json`` /
``harness-routines.json``), each a dict keyed by id carrying the same fields
the row did.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.json_narrowing import as_list, as_str
from app.services.filestore import STATE_DIR, home, read_json, write_json

logger = logging.getLogger(__name__)

AUTONOMY_ASK = 'ask'
AUTONOMY_ON_FAIL = 'on_fail'
AUTONOMY_SILENT = 'silent'
AUTONOMY_MODES = (AUTONOMY_ASK, AUTONOMY_ON_FAIL, AUTONOMY_SILENT)
MAX_AUTO_HOPS = 3

_SPECIALISTS_FILE = 'harness-specialists.json'
_ROUTINES_FILE = 'harness-routines.json'
_LOCK = threading.RLock()


def _specialists_path() -> Path:
    return home(STATE_DIR) / _SPECIALISTS_FILE


def _routines_path() -> Path:
    return home(STATE_DIR) / _ROUTINES_FILE


def _load_state(path: Path) -> dict[str, dict[str, Any]]:
    raw = read_json(path, default={})
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}


def _save_state(path: Path, rows: dict[str, dict[str, Any]]) -> None:
    write_json(path, rows)


def _now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _json_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                return [str(x).strip() for x in loaded if str(x).strip()]
        except Exception:
            return [p.strip() for p in raw.split(',') if p.strip()]
    return []


def should_ping(
    autonomy: str,
    status: str,
    next_action: str = '',
    unmet: str = '',
) -> bool:
    """Whether the user should be interrupted (dirty / inbox ping)."""
    mode = (autonomy or AUTONOMY_ASK).strip().lower()
    if mode not in AUTONOMY_MODES:
        mode = AUTONOMY_ASK
    st = (status or '').strip().lower()
    failed = st in ('blocked', 'partial', 'failed', 'error', 'cancelled')
    if mode == AUTONOMY_SILENT:
        return failed
    if mode == AUTONOMY_ON_FAIL:
        return failed or bool(unmet)
    return failed or bool(next_action) or bool(unmet) or st != 'completed'


def should_auto_continue(
    autonomy: str,
    status: str,
    next_action: str = '',
    hops: int = 0,
) -> bool:
    """Silent lanes with a next step keep going, up to MAX_AUTO_HOPS."""
    mode = (autonomy or AUTONOMY_ASK).strip().lower()
    st = (status or '').strip().lower()
    nxt = (next_action or '').strip()
    return mode == AUTONOMY_SILENT and st == 'completed' and bool(nxt) and hops < MAX_AUTO_HOPS


def count_auto_hops(session_id: str, workstream: str) -> int:
    from app.services.workstreams import list_episodes

    n = 0
    for ep in reversed(list_episodes(session_id, workstream, limit=20)):
        if ep.get('autoHop'):
            n += 1
        else:
            break
    return n


def _row_specialist(r: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': r.get('id'),
        'sessionId': r.get('session_id') or '',
        'name': r.get('name'),
        'workstream': r.get('workstream') or '',
        'agentId': r.get('agent_id') or 'general',
        'skills': _json_list(r.get('skills_json')),
        'model': r.get('model') or '',
        'acceptance': r.get('acceptance') or '',
        'restrictedTools': _json_list(r.get('restricted_tools_json')),
        'autonomy': r.get('autonomy') or AUTONOMY_ASK,
        'workspacePath': r.get('workspace_path') or '',
        'createdAt': r.get('created_at') or '',
    }


def _row_routine(r: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': r.get('id'),
        'sessionId': r.get('session_id') or '',
        'name': r.get('name'),
        'workstream': r.get('workstream') or '',
        'goal': r.get('goal') or '',
        'skills': _json_list(r.get('skills_json')),
        'agentId': r.get('agent_id') or 'general',
        'specialistId': r.get('specialist_id') or '',
        'sourceSeq': r.get('source_seq') or 0,
        'workspacePath': r.get('workspace_path') or '',
        'schedule': r.get('schedule') or '',
        'paused': bool(r.get('paused')),
        'lastRun': r.get('last_run') or '',
        'createdAt': r.get('created_at') or '',
    }


def _workspace_match(row: dict[str, Any], session_id: str, workspace: str) -> bool:
    """The old WHERE: own session, the shared '' session, or the workspace."""
    if str(row.get('session_id') or '') == session_id:
        return True
    if str(row.get('session_id') or '') == '':
        return True
    path = (workspace or '').strip()
    return bool(path) and str(row.get('workspace_path') or '') == path


def list_specialists(session_id: str, workspace: str = '') -> list[dict[str, Any]]:
    with _LOCK:
        rows = list(_load_state(_specialists_path()).values())
    rows = [r for r in rows if _workspace_match(r, session_id, workspace)]
    rows.sort(key=lambda r: str(r.get('created_at') or ''), reverse=True)
    return [_row_specialist(r) for r in rows]


def specialist_for_workstream(
    session_id: str, workstream: str, workspace: str = ''
) -> dict[str, Any] | None:
    name = as_str(workstream, '').strip()
    if not name:
        return None
    with _LOCK:
        rows = list(_load_state(_specialists_path()).values())
    rows = [r for r in rows if str(r.get('workstream') or '') == name and _workspace_match(r, session_id, workspace)]
    # Session-owned rows win over the shared '' rows, newest first within
    # each group (two stable sorts = the old ORDER BY CASE + created_at DESC).
    rows.sort(key=lambda r: str(r.get('created_at') or ''), reverse=True)
    rows.sort(key=lambda r: 0 if str(r.get('session_id') or '') == session_id else 1)
    return _row_specialist(rows[0]) if rows else None


def upsert_specialist(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    sid = as_str(body.get('id'), '')
    name = as_str(body.get('name') or body.get('workstream'), '').strip()
    workstream = as_str(body.get('workstream') or name, '').strip()
    workspace = as_str(body.get('workspacePath') or body.get('workspace_path'), '').strip()
    if not name:
        raise ValueError('Specialist needs a name')
    autonomy = as_str(body.get('autonomy'), AUTONOMY_ASK).strip().lower()
    if autonomy not in AUTONOMY_MODES:
        autonomy = AUTONOMY_ASK
    skills = json.dumps(_json_list(body.get('skills')), ensure_ascii=False)
    tools = json.dumps(_json_list(body.get('restrictedTools') or body.get('restricted_tools')), ensure_ascii=False)
    with _LOCK:
        rows = _load_state(_specialists_path())
        if not sid:
            sid = f'spec_{uuid.uuid4().hex[:12]}'
            rows[sid] = {
                'id': sid,
                'session_id': session_id,
                'name': name[:80],
                'workstream': workstream[:80],
                'agent_id': as_str(body.get('agentId') or body.get('agent_id'), 'general') or 'general',
                'skills_json': skills,
                'model': as_str(body.get('model'), ''),
                'acceptance': as_str(body.get('acceptance'), ''),
                'restricted_tools_json': tools,
                'autonomy': autonomy,
                'workspace_path': workspace[:500],
                'created_at': _now(),
            }
        else:
            row = rows.get(sid)
            if not row:
                raise ValueError('Specialist not found')
            row.update(
                {
                    'name': name[:80],
                    'workstream': workstream[:80],
                    'agent_id': as_str(body.get('agentId') or body.get('agent_id'), 'general') or 'general',
                    'skills_json': skills,
                    'model': as_str(body.get('model'), ''),
                    'acceptance': as_str(body.get('acceptance'), ''),
                    'restricted_tools_json': tools,
                    'autonomy': autonomy,
                    'workspace_path': workspace[:500],
                }
            )
        _save_state(_specialists_path(), rows)
        row = rows.get(sid)
        if not row:
            raise ValueError('Specialist not found')
        return _row_specialist(row)


def set_autonomy(specialist_id: str, autonomy: str) -> dict[str, Any]:
    mode = (autonomy or AUTONOMY_ASK).strip().lower()
    if mode not in AUTONOMY_MODES:
        raise ValueError(f'autonomy must be one of {AUTONOMY_MODES}')
    with _LOCK:
        rows = _load_state(_specialists_path())
        row = rows.get(specialist_id)
        if not row:
            raise ValueError('Specialist not found')
        row['autonomy'] = mode
        _save_state(_specialists_path(), rows)
        return _row_specialist(row)


def delete_specialist(specialist_id: str) -> None:
    with _LOCK:
        rows = _load_state(_specialists_path())
        if specialist_id in rows:
            del rows[specialist_id]
            _save_state(_specialists_path(), rows)


def list_routines(session_id: str, workspace: str = '') -> list[dict[str, Any]]:
    with _LOCK:
        rows = list(_load_state(_routines_path()).values())
    rows = [r for r in rows if _workspace_match(r, session_id, workspace)]
    rows.sort(key=lambda r: str(r.get('created_at') or ''), reverse=True)
    return [_row_routine(r) for r in rows]


def save_routine(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    name = as_str(body.get('name'), '').strip()
    workstream = as_str(body.get('workstream'), '').strip()
    goal = as_str(body.get('goal'), '').strip()
    workspace = as_str(body.get('workspacePath') or body.get('workspace_path'), '').strip()
    if not name or not workstream:
        raise ValueError('Routine needs name and workstream')
    rid = f'rtn_{uuid.uuid4().hex[:12]}'
    with _LOCK:
        rows = _load_state(_routines_path())
        rows[rid] = {
            'id': rid,
            'session_id': session_id,
            'name': name[:120],
            'workstream': workstream[:80],
            'goal': goal[:4000],
            'skills_json': json.dumps(_json_list(body.get('skills')), ensure_ascii=False),
            'agent_id': as_str(body.get('agentId') or body.get('agent_id'), 'general') or 'general',
            'specialist_id': as_str(body.get('specialistId') or body.get('specialist_id'), ''),
            'source_seq': int(body.get('sourceSeq') or body.get('source_seq') or 0),
            'created_at': _now(),
            'workspace_path': workspace[:500],
            'schedule': '',
            'paused': 0,
            'last_run': '',
        }
        _save_state(_routines_path(), rows)
        return _row_routine(rows[rid])


def save_routine_from_episode(
    session_id: str, workstream: str, seq: int | None = None, workspace: str = ''
) -> dict[str, Any]:
    from app.services.workstreams import list_episodes

    eps = list_episodes(session_id, workstream, limit=80)
    if not eps:
        raise ValueError('No episodes on this workstream')
    ep = eps[-1]
    if seq is not None:
        hit = next((e for e in eps if int(e.get('seq') or 0) == int(seq)), None)
        if hit:
            ep = hit
    next_or_summary = as_str(ep.get('next') or ep.get('summary'), '').strip()
    spec = specialist_for_workstream(session_id, workstream, workspace)
    return save_routine(
        session_id,
        {
            'name': f'{workstream} #{ep.get("seq")}',
            'workstream': workstream,
            'goal': next_or_summary or f'Continue {workstream}',
            'skills': ep.get('skills') or (spec or {}).get('skills') or [],
            'agentId': (spec or {}).get('agentId') or 'general',
            'specialistId': (spec or {}).get('id') or '',
            'sourceSeq': ep.get('seq') or 0,
            'workspacePath': workspace,
        },
    )


def get_routine(routine_id: str) -> dict[str, Any] | None:
    with _LOCK:
        row = _load_state(_routines_path()).get(routine_id)
    return _row_routine(row) if row else None


def delete_routine(routine_id: str) -> None:
    with _LOCK:
        rows = _load_state(_routines_path())
        if routine_id in rows:
            del rows[routine_id]
            _save_state(_routines_path(), rows)


def update_routine_fields(routine_id: str, **fields: object) -> dict[str, Any] | None:
    """Internal writer for schedule/paused/last_run maintenance (harness_ops)."""
    with _LOCK:
        rows = _load_state(_routines_path())
        row = rows.get(routine_id)
        if not row:
            return None
        row.update(fields)
        _save_state(_routines_path(), rows)
        return _row_routine(row)


def scheduled_routines() -> list[dict[str, Any]]:
    """Every routine with a schedule set, paused or not (raw row dicts)."""
    with _LOCK:
        rows = list(_load_state(_routines_path()).values())
    return [r for r in rows if str(r.get('schedule') or '')]


def continue_work_item(
    session_id: str, name: str, user_message: str = '', workspace: str = ''
) -> dict[str, Any]:
    """One spawn item that inherits specialist + last episode card."""
    from app.services.workstreams import continue_goal, continue_handoff

    card = continue_handoff(session_id, name) or {}
    spec = specialist_for_workstream(session_id, name, workspace) or {}
    skills = list(as_list(spec.get('skills'), []) or card.get('skills') or [])
    default = as_str(card.get('next'), '').strip() or 'Continue from the last episode.'
    user = as_str(user_message, '').strip() or default
    item: dict[str, Any] = {
        'goal': continue_goal(session_id, name, user),
        'agentId': spec.get('agentId') or 'general',
        'workstream': name,
        'name': name,
        'skills': [str(s) for s in skills if s],
    }
    if spec.get('acceptance'):
        item['acceptanceCriteria'] = spec['acceptance']
    if spec.get('model'):
        item['model'] = spec['model']
    if spec.get('restrictedTools'):
        item['restrictedTools'] = spec['restrictedTools']
    return item


def routine_work_item(session_id: str, routine: dict[str, Any]) -> dict[str, Any]:
    name = as_str(routine.get('workstream'), '')
    item = continue_work_item(
        session_id, name, as_str(routine.get('goal'), ''), as_str(routine.get('workspacePath'), '')
    )
    extra = _json_list(routine.get('skills'))
    if extra:
        merged = list(dict.fromkeys([*(item.get('skills') or []), *extra]))
        item['skills'] = merged
    if routine.get('agentId'):
        item['agentId'] = routine['agentId']
    return item


def session_digest(session_id: str, workspace: str = '') -> dict[str, Any]:
    from app.services.harness_jobs import list_jobs
    from app.services.workstreams import list_workstreams

    jobs = list_jobs(session_id)
    streams = list_workstreams(session_id)
    dirty_jobs = [j for j in jobs if j.get('dirty')]
    running = [j for j in jobs if j.get('status') == 'running']
    needs = []
    for ws in streams:
        latest = ws.get('latest') or {}
        if ws.get('dirty') or latest.get('status') in ('partial', 'blocked'):
            needs.append(
                {
                    'workstream': ws['name'],
                    'status': latest.get('status') or '',
                    'next': latest.get('next') or '',
                    'summary': (latest.get('summary') or '')[:160],
                }
            )
    out = {
        'running': len(running),
        'dirtyJobs': len(dirty_jobs),
        'needsHandoff': needs,
        'unread': len(needs) + len(running),
        'needsCount': len(needs),
        'workingCount': len(running),
        'specialists': list_specialists(session_id, workspace),
        'routines': list_routines(session_id, workspace),
        'unattended': False,
    }
    try:
        from app.services.harness_ops import is_unattended

        out['unattended'] = is_unattended()
    except Exception:
        pass
    return out


def schedule_auto_continue(session: object, emit: Any, workstream: str, next_action: str, hops: int) -> None:
    """Fire-and-forget Continue for a silent specialist lane."""
    import asyncio

    try:
        from app.services.harness_ops import is_unattended

        if is_unattended():
            if emit:
                emit(
                    {
                        'type': 'info',
                        'kind': 'harnessLaneDone',
                        'message': f'{workstream} paused — August has been idle over 24h',
                        'workstream': workstream,
                    }
                )
            return
    except Exception:
        pass

    sid = str(getattr(session, 'id', '') or '')
    workspace = str(getattr(session, 'workspacePath', '') or getattr(session, 'workspace_path', '') or '')
    if not sid or not workstream:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _run() -> None:
        from app.services.runtime_services import get_orchestrator
        from app.services.tools.spawn_subagents_tool import executeSpawnSubagents

        item = continue_work_item(sid, workstream, next_action, workspace)
        item['autoHop'] = True
        if emit:
            emit(
                {
                    'type': 'info',
                    'kind': 'harnessAutoContinue',
                    'message': f'{workstream} continuing ({hops + 1}/{MAX_AUTO_HOPS}) → {next_action[:80]}',
                    'workstream': workstream,
                    'hop': hops + 1,
                }
            )
        try:
            from app.services.harness_jobs import record_lane

            record_lane('', workstream, 'continuing')
        except Exception:
            pass
        orch = get_orchestrator()
        await executeSpawnSubagents(orch, session, [item], mode='auto', emit=emit, background=True)

    loop.create_task(_run())
