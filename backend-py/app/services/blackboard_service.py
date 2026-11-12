"""Blackboard service — inter-agent shared cognitive workspace (Phase 10.1).

Allows the main loop and background daemons to share real-time state via
``<state>/blackboard.json``. TTL-based cleanup. Session-scoped.

v2: Adaptive TTL (`max(poll_interval*2, 60s)` or 3 turns), `ack` parameter
on read to delete-on-read, and Tier 3 injection support.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import cast

from app.json_narrowing import as_int
from app.services.memory_store import tables
from app.services.memory_store.timeutil import utc_now_text
from app.services.memory_store.wire import _row_as_wire
from app.type_aliases import BlackboardNoteDict

_ROW_COLUMNS = (
    'id', 'session_id', 'agent', 'key', 'value', 'priority',
    'created_at', 'expires_at', 'workspace_path', 'folder_id',
)


def computeTtl(pollInterval: int) -> str:
    """v2: Adaptive TTL = max(poll_interval * 2, 60). Returns timestamp string.

    A CI watcher polling every 30s gets notes that live >= 60s.
    A fast env-watcher polling every 2s gets notes that live >= 4s.
    """
    ttlSeconds = max(pollInterval * 2, 60)
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttlSeconds)
    return expires.strftime('%Y-%m-%d %H:%M:%S')


def _resolve_workspace_path(sessionId: str, workspacePath: str | None) -> str:
    if workspacePath:
        return str(workspacePath)
    try:
        from app.services.workbench.sessions import get_workbench_session

        sess = get_workbench_session(sessionId)
        if sess is not None:
            return str(getattr(sess, 'workspacePath', '') or getattr(sess, 'workspace_path', '') or '')
    except Exception:
        pass
    return ''


def _rows() -> list[dict[str, object]]:
    return tables.load_rows(tables.blackboard_file())


def _save(rows: list[dict[str, object]]) -> None:
    tables.save_rows(tables.blackboard_file(), rows)


def writeNote(
    sessionId: str,
    agent: str,
    key: str,
    value: object,
    priority: int = 0,
    ttlSeconds: int | None = None,
    pollInterval: int | None = None,
    workspacePath: str | None = None,
    persist: str = 'session',
) -> None:
    """Write a note to the blackboard.

    v2: If `poll_interval` is provided, the TTL is computed adaptively
    (max(poll_interval*2, 60)). If `ttl_seconds` is also provided, ttl_seconds wins.
    When `persist='workspace'`, the note is visible to the next session in the
    same workspace (handoff) and expires after 7 days.
    """
    expires = None
    if persist == 'workspace':
        if ttlSeconds and ttlSeconds > 0:
            expires = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(time.time() + ttlSeconds))
        else:
            expires = (datetime.now(timezone.utc) + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    elif pollInterval is not None and ttlSeconds is None:
        expires = computeTtl(pollInterval)
    elif ttlSeconds and ttlSeconds > 0:
        expires = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(time.time() + ttlSeconds))
    ws_path = _resolve_workspace_path(sessionId, workspacePath) if persist == 'workspace' else (workspacePath or '')
    fid = ''
    try:
        from app.services.workbench.sessions import get_workbench_session

        sess = get_workbench_session(sessionId)
        if sess is not None:
            fid = str(getattr(sess, 'folderId', '') or getattr(sess, 'folder_id', '') or '')
    except Exception:
        pass
    path = tables.blackboard_file()
    with tables.store_lock(str(path)):
        rows = _rows()
        rows.append(
            {
                'id': tables.next_id(rows),
                'session_id': sessionId,
                'agent': agent,
                'key': key,
                'value': json.dumps(value) if not isinstance(value, str) else value,
                'priority': priority,
                'created_at': utc_now_text(),
                'expires_at': expires,
                'workspace_path': ws_path,
                'folder_id': fid,
            }
        )
        _save(rows)


def readNotes(
    sessionId: str,
    agent: str = '',
    key: str = '',
    ack: bool = False,
    includeWorkspace: bool = True,
) -> list[BlackboardNoteDict]:
    """Read notes from the blackboard, with optional agent/key filters.

    v2: If `ack=True`, the read notes are deleted on read (acknowledged
    by the consumer). Returns camelCase wire dicts via ``_row_as_wire``.
    When `includeWorkspace` is true, notes with persist=workspace in the same
    workspace_path are also returned (tagged workspace).
    """
    path = tables.blackboard_file()
    with tables.store_lock(str(path)):
        _cleanupExpired()
        ws_path = _resolve_workspace_path(sessionId, None) if includeWorkspace else ''
        rows = _rows()
        if includeWorkspace and ws_path:
            rows = [
                r for r in rows
                if str(r.get('session_id') or '') == sessionId or str(r.get('workspace_path') or '') == ws_path
            ]
        else:
            rows = [r for r in rows if str(r.get('session_id') or '') == sessionId]
        if agent:
            rows = [r for r in rows if str(r.get('agent') or '') == agent]
        if key:
            rows = [r for r in rows if str(r.get('key') or '') == key]
        rows.sort(key=lambda r: (as_int(r.get('priority'), 0), str(r.get('created_at') or '')), reverse=True)
        notes: list[dict[str, object]] = [_row_as_wire({c: r.get(c) for c in _ROW_COLUMNS}) for r in rows]
        if ack and notes:
            keep: list[dict[str, object]] = []
            for r in rows:
                # Workspace-persisted notes are not deleted on ack — they live until TTL.
                if r.get('workspace_path'):
                    keep.append(r)
            _save(keep)
    return cast('list[BlackboardNoteDict]', notes)


def clearNotes(sessionId: str, agent: str = '', scope: str = 'session') -> int:
    """Clear blackboard notes, optionally for a specific agent.

    When scope='workspace', also clears workspace-persisted notes for the session's workspace.
    """
    path = tables.blackboard_file()
    with tables.store_lock(str(path)):
        rows = _rows()
        if scope == 'workspace':
            ws_path = _resolve_workspace_path(sessionId, None)
            if not ws_path:
                return 0
            if agent:
                kept = [
                    r for r in rows
                    if not (str(r.get('workspace_path') or '') == ws_path and str(r.get('agent') or '') == agent)
                ]
            else:
                kept = [r for r in rows if str(r.get('workspace_path') or '') != ws_path]
            removed = len(rows) - len(kept)
            if removed:
                _save(kept)
            return removed
        if agent:
            kept = [
                r for r in rows
                if not (str(r.get('session_id') or '') == sessionId and str(r.get('agent') or '') == agent)
            ]
        else:
            kept = [r for r in rows if str(r.get('session_id') or '') != sessionId]
        removed = len(rows) - len(kept)
        if removed:
            _save(kept)
        return removed


def _cleanupExpired() -> None:
    """Delete expired notes (called under the store lock)."""
    rows = _rows()
    now = utc_now_text()
    kept = [
        r for r in rows
        if not (r.get('expires_at') and str(r.get('expires_at')) < now)
    ]
    if len(kept) != len(rows):
        _save(kept)
