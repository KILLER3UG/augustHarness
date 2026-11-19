"""Automation tools — create/update/list/delete recurring automations by chat.

The owner ruling (Part D bugs, 2026-11-17): the automations GAP — the user
could not create or manage an automation by asking the agent, only through the
heavy manual form. These four tools wrap ``automations_store`` (the one
routines record): a "routine" is a job bound to a bot via ``agentId`` +
``deliver: 'bot-chat'``. The receipt is the tool RESULT — the UI renders it as
a compact card with undo, mirroring the bot receipts.
"""

from __future__ import annotations

import json
from typing import Any

from app.json_narrowing import as_list, as_str


def _compact_job(job: dict[str, Any]) -> dict[str, Any]:
    """One automation as the model reads it: the fields that decide behavior,
    no webhook internals (the trigger token never enters a transcript)."""
    runs: list[dict[str, Any]] = [
        r for r in as_list(job.get('runs'), []) if isinstance(r, dict)
    ]
    last: dict[str, Any] = runs[-1] if runs else {}
    return {
        'jobId': as_str(job.get('id')),
        'name': as_str(job.get('name')),
        'schedule': as_str(job.get('schedule')) or '(one-shot / manual)',
        'jobType': as_str(job.get('jobType'), 'workbench'),
        'prompt': as_str(job.get('prompt'))[:200],
        'agentId': as_str(job.get('agentId')) or '',
        'deliver': as_str(job.get('deliver'), 'bot-chat') if job.get('agentId') else '',
        'enabled': bool(job.get('enabled', True)),
        'paused': bool(job.get('paused', False)),
        'status': as_str(job.get('status'), 'idle'),
        'nextRunAt': as_str(job.get('nextRunAt')) or '',
        'lastRunAt': as_str(job.get('lastRunAt')) or as_str(last.get('startedAt')) or '',
        'lastStatus': as_str(last.get('status')) or '',
        'runs': len(runs),
    }


async def _createAutomation(
    name: str = '',
    schedule: str = '',
    prompt: str = '',
    agentId: str = '',
    deliver: str = '',
    respond: bool = True,
    jobType: str = 'workbench',
    model: str = '',
    timezone: str = '',
    guardMode: str = '',
) -> str:
    """Create an automation (optionally bound to a bot). See the schema."""
    from app.services import automations_store

    clean = (name or '').strip()
    if not clean:
        return 'Error: name is required.'
    if not (schedule or '').strip():
        return "Error: schedule is required — e.g. 'daily at 09:00', 'every 6h', or a cron like '0 9 * * 1-5'."
    body: dict[str, Any] = {
        'name': clean,
        'schedule': schedule.strip(),
        'jobType': (jobType or 'workbench').strip() or 'workbench',
        'prompt': (prompt or '').strip(),
    }
    if (agentId or '').strip():
        # A bot-bound routine: results deliver to the bot's canonical chat.
        body['agentId'] = agentId.strip()
        body['deliver'] = (deliver or 'bot-chat').strip() or 'bot-chat'
        body['respond'] = bool(respond)
    if (model or '').strip():
        body['model'] = model.strip()
    if (timezone or '').strip():
        body['timezone'] = timezone.strip()
    if (guardMode or '').strip():
        body['guardMode'] = guardMode.strip()
    try:
        job = await automations_store.upsert_job_async(body)
    except ValueError as exc:
        return f'Error: {exc}'
    receipt = {
        'kind': 'automation_created',
        'jobId': as_str(job.get('id')),
        'name': as_str(job.get('name')),
        'schedule': as_str(job.get('schedule')),
        'agentId': as_str(job.get('agentId')) or '',
        'nextRunAt': as_str(job.get('nextRunAt')) or '',
        'note': 'Undo by deleting the automation.',
    }
    return json.dumps(receipt, default=str)


async def _updateAutomation(
    jobId: str = '',
    name: str = '',
    schedule: str = '',
    prompt: str = '',
    paused: bool | None = None,
    enabled: bool | None = None,
    model: str = '',
    timezone: str = '',
) -> str:
    """Change one automation's schedule, prompt, name or state. See the schema."""
    from app.services import automations_store

    existing = automations_store.get_job((jobId or '').strip())
    if not existing:
        return f"Error: no automation '{jobId}'. Use list_automations to see the ids."
    body: dict[str, Any] = {'id': as_str(existing.get('id'))}
    updated: list[str] = []
    if (name or '').strip() and name.strip() != as_str(existing.get('name')):
        body['name'] = name.strip()
        updated.append('name')
    if (schedule or '').strip() and schedule.strip() != as_str(existing.get('schedule')):
        body['schedule'] = schedule.strip()
        updated.append('schedule')
    if (prompt or '').strip() and prompt.strip() != as_str(existing.get('prompt')):
        body['prompt'] = prompt.strip()
        updated.append('prompt')
    if (model or '').strip() and model.strip() != as_str(existing.get('model')):
        body['model'] = model.strip()
        updated.append('model')
    if (timezone or '').strip() and timezone.strip() != as_str(existing.get('timezone')):
        body['timezone'] = timezone.strip()
        updated.append('timezone')
    if paused is not None and bool(paused) != bool(existing.get('paused', False)):
        body['paused'] = bool(paused)
        updated.append('paused')
    if enabled is not None and bool(enabled) != bool(existing.get('enabled', True)):
        body['enabled'] = bool(enabled)
        updated.append('enabled')
    if not updated:
        return f"Automation '{as_str(existing.get('name'))}' already has those values — nothing changed."
    try:
        job = await automations_store.upsert_job_async(body)
    except ValueError as exc:
        return f'Error: {exc}'
    receipt = {
        'kind': 'automation_updated',
        'jobId': as_str(job.get('id')),
        'name': as_str(job.get('name')),
        'updated': updated,
        'schedule': as_str(job.get('schedule')),
        'nextRunAt': as_str(job.get('nextRunAt')) or '',
    }
    return json.dumps(receipt, default=str)


async def _listAutomations() -> str:
    """List every automation with its schedule and state. See the schema."""
    jobs = automations_store_jobs()
    if not jobs:
        return 'No automations yet. Create one with create_automation.'
    return json.dumps({'automations': jobs}, default=str)


def automations_store_jobs() -> list[dict[str, Any]]:
    from app.services import automations_store

    return [_compact_job(j) for j in automations_store.list_jobs()]


async def _deleteAutomation(jobId: str = '') -> str:
    """Delete one automation. See the schema."""
    from app.services import automations_store

    existing = automations_store.get_job((jobId or '').strip())
    if not existing:
        return f"Error: no automation '{jobId}'. Use list_automations to see the ids."
    deleted = await automations_store.delete_job_async(as_str(existing.get('id')))
    if not deleted:
        return f"Error: could not delete '{as_str(existing.get('name'))}'."
    receipt = {
        'kind': 'automation_deleted',
        'jobId': as_str(existing.get('id')),
        'name': as_str(existing.get('name')),
        'schedule': as_str(existing.get('schedule')),
        'note': 'Recreate it with create_automation to restore.',
    }
    return json.dumps(receipt, default=str)


def register() -> None:
    """Register the automation tools."""
    from app.services import tool_registry

    tool_registry.register(
        'create_automation',
        'Create a recurring automation (optionally bound to a bot — a routine). '
        'Schedule forms: "every 30m"/"every 6h"/"every 1d", "daily at 09:00", '
        '"weekly monday at 14:00", or a 5-field cron. The receipt card carries undo.',
        _createAutomation,
        {
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'Short human name, e.g. "Morning paper digest".'},
                'schedule': {
                    'type': 'string',
                    'description': 'When it runs: "every 30m"/"every 6h"/"every 1d", '
                    '"daily at 09:00", "weekly monday at 14:00", or 5-field cron ("0 9 * * 1-5").',
                },
                'prompt': {
                    'type': 'string',
                    'description': 'What the agent does on each run (workbench automations).',
                },
                'agentId': {
                    'type': 'string',
                    'description': 'Bot to bind the routine to (optional — results deliver to that bot\'s chat).',
                },
                'deliver': {
                    'type': 'string',
                    'enum': ['bot-chat'],
                    'description': 'Delivery channel for a bot-bound routine; default bot-chat.',
                },
                'respond': {
                    'type': 'boolean',
                    'default': True,
                    'description': 'Whether the bound bot reacts to the delivered result (default true).',
                },
                'jobType': {
                    'type': 'string',
                    'enum': ['workbench', 'shell', 'http'],
                    'default': 'workbench',
                    'description': 'Runner: workbench (agent turn), shell (command), http (webhook call).',
                },
                'model': {'type': 'string', 'description': 'Model override for the runs (optional).'},
                'timezone': {'type': 'string', 'description': 'IANA timezone for the schedule (optional, default system).'},
                'guardMode': {
                    'type': 'string',
                    'enum': ['ask', 'plan', 'full', 'readonly'],
                    'description': 'Permission mode for the run (optional).',
                },
            },
            'required': ['name', 'schedule', 'prompt'],
        },
    )
    tool_registry.register(
        'update_automation',
        'Change one automation: schedule, prompt, name, model, timezone, or pause/enable it. '
        'list_automations shows the ids.',
        _updateAutomation,
        {
            'type': 'object',
            'properties': {
                'jobId': {'type': 'string', 'description': 'The automation id (list_automations).'},
                'name': {'type': 'string'},
                'schedule': {'type': 'string', 'description': 'Same grammar as create_automation.'},
                'prompt': {'type': 'string'},
                'paused': {'type': 'boolean', 'description': 'Pause (true) or resume (false) the schedule.'},
                'enabled': {'type': 'boolean', 'description': 'Enable/disable the automation.'},
                'model': {'type': 'string'},
                'timezone': {'type': 'string'},
            },
            'required': ['jobId'],
        },
    )
    tool_registry.register(
        'list_automations',
        'List every automation with its schedule, binding and last run.',
        _listAutomations,
        {'type': 'object', 'properties': {}, 'required': []},
    )
    tool_registry.register(
        'delete_automation',
        'Delete one automation permanently. The receipt card names what was removed.',
        _deleteAutomation,
        {
            'type': 'object',
            'properties': {'jobId': {'type': 'string', 'description': 'The automation id (list_automations).'}},
            'required': ['jobId'],
        },
    )
