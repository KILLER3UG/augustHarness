"""Automation notepad door (M-11).

The Bot-owned routine tools that used to live here (create_routine,
list_routines, delete_routine) were removed in the redesign: routines ARE
automations_store jobs, and the orchestrator attaches one to a Bot with the
existing automation tool, so a second namespaced surface was redundant. Only
the notepad door remains, because it is not bot-specific — it is the door an
automation RUN session uses to persist state between runs.

``job_notes`` is callable only from a session whose metadata marks it as an
automation run (the same double-check pattern as the Phase C injection gate): a
forged call from a regular chat gets a structured error, not a write.
"""

from __future__ import annotations

import json

from app.json_narrowing import as_str


def _ok(**fields: object) -> str:
    return json.dumps({'status': 'success', **fields}, default=str)


def _err(message: str, **fields: object) -> str:
    return json.dumps({'status': 'error', 'error': message, **fields}, default=str)



async def jobNotes(
    action: str = 'get',
    key: str = '',
    value: str = '',
    jobId: str = '',
) -> str:
    """M-11 notepad door — get/set/delete per-job notes.

    Callable only from a session whose metadata marks it as an automation
    run (the same double-check pattern as the Phase C injection gate): a
    forged call from a regular chat gets a structured error, not a write.
    An explicit ``jobId`` may only narrow the target WITHIN an automation
    session — otherwise any chat could write notes that land in an
    unattended routine's next wake context.
    """
    from app.services import automation_memory
    from app.services.workbench.workbench import get_session

    try:
        session = get_session()
        meta = getattr(session, 'metadata', None) if session else None
        meta = meta if isinstance(meta, dict) else {}
        session_job = as_str(meta.get('automationJobId'))
        if not session_job and not jobId:
            return _err('job_notes is only callable from an automation run session.')
        if jobId and not session_job:
            return _err(
                'job_notes cannot target an arbitrary jobId — explicit ids are '
                'only valid inside that job\'s own automation session.'
            )
        job_id = jobId or session_job
        act = (action or 'get').strip().lower()
        if act == 'get':
            return _ok(notes=automation_memory.get_notes(job_id))
        if act == 'set':
            if not key:
                return _err('set requires a key.')
            err = automation_memory.set_note(job_id, key, value)
            if err:
                return _err(f'Note not saved: {err}.')
            return _ok(saved=key)
        if act == 'delete':
            if not key:
                return _err('delete requires a key.')
            err = automation_memory.delete_note(job_id, key)
            if err:
                return _err(f'Note not deleted: {err}.')
            return _ok(deleted=key)
        return _err(f"Unknown action '{action}' — use get | set | delete.")
    except Exception as exc:
        return _err(f'job_notes failed: {exc}')


def register() -> None:
    """Register the automation notepad door (job_notes)."""
    from app.services import tool_registry

    tool_registry.register(
        'job_notes',
        'Per-run-job notepad for automation sessions: persist decisions/state between runs '
        '(action=get|set|delete, key, value). Only callable from automation run sessions.',
        jobNotes,
        {
            'type': 'object',
            'properties': {
                'action': {'type': 'string', 'description': 'get | set | delete (default get).'},
                'key': {'type': 'string', 'description': 'Note key (e.g. last_cursor, pending_items).'},
                'value': {'type': 'string', 'description': 'Note value for set (4 KiB cap per key).'},
                'jobId': {
                    'type': 'string',
                    'description': "Explicit job id (default: this session's job).",
                },
            },
            'required': [],
        },
    )
