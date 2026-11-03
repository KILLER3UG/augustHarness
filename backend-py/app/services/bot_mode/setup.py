"""Guided setup for bots — the rerun INSTALL pattern.

A bot created with ``setup_required`` carries a pending walkthrough: its
owner opens the bot's chat, the bot guides the configuration conversation
(the prompt section below), and ``install_complete`` closes the setup and
activates the routines that were parked for it. The tool is advertised ONLY
while setup is pending — a knob that always refuses is a lie — and the
handler re-checks so a forged call fails closed, same convention as the
DM tools.
"""

from __future__ import annotations

import logging

from app.json_narrowing import as_bool, as_dict, as_str

logger = logging.getLogger('august.bot_mode')

_INSTALL_COMPLETE = 'install_complete'


async def installComplete() -> str:
    """The install_complete tool handler — close the bot's guided setup."""
    from app.services import automations_store, session_scope
    from app.services.bot_mode import roster
    from app.services.workbench.workbench import get_session

    session = get_session()
    if session is None:
        return 'Error: install_complete is only callable inside a Bot chat.'
    scope = session_scope.resolve_scope(session)
    if not session_scope.is_bot_scope(scope):
        return 'Error: install_complete is only callable from a Bot session (a bot\'s own chat).'
    agent_id = session_scope.bot_agent_id(scope)
    if not roster.bot_setup_pending(agent_id):
        return 'Error: this bot has no pending guided setup — nothing to complete.'
    if not roster.mark_bot_setup_complete(agent_id, actor='tool'):
        return 'Error: setup was already completed.'

    bot = roster.get_bot(agent_id)
    bot_name = as_str(bot.get('name')) if bot else agent_id
    activated: list[str] = []
    try:
        for job in automations_store.list_jobs():
            job_name = as_str(job.get('name'))
            if bot_name and job_name.startswith(f'[bot:{bot_name}]') and as_bool(job.get('paused'), False):
                await automations_store.pause_job(as_str(job.get('id')), paused=False)
                activated.append(job_name)
    except Exception:  # noqa: BLE001 — setup closes even if activation trips
        logger.warning('routine activation during install_complete failed', exc_info=True)

    suffix = f' Routines activated: {", ".join(activated)}.' if activated else ''
    return (
        f'Guided setup complete for @{bot_name}. This bot is fully configured; the '
        f'setup section and this tool are gone.{suffix}'
    )


def register() -> None:
    from app.services import tool_registry

    tool_registry.register(
        _INSTALL_COMPLETE,
        'Complete this bot\'s guided setup. Callable only while a setup is pending in the '
        'calling Bot\'s chat; closes it and activates the routines that were parked for it.',
        installComplete,
        {
            'type': 'object',
            'properties': {},
        },
        keywords=['setup', 'install', 'guided setup', 'onboarding'],
    )


def filter_setup_tools(tools: list[dict[str, object]], session: object) -> list[dict[str, object]]:
    """Drop ``install_complete`` unless the session's bot has setup pending.

    Called from BOTH tool-definition paths (Anthropic + OpenAI) so the gate
    can't drift — the same contract as ``filter_dm_tools``. The executor
    re-checks, so a forged call still fails closed.
    """
    try:
        from app.services import session_scope
        from app.services.bot_mode import roster

        scope = session_scope.resolve_scope(session)
        if session_scope.is_bot_scope(scope) and roster.bot_setup_pending(session_scope.bot_agent_id(scope)):
            return tools
    except Exception:  # noqa: BLE001 — the safe default hides the tool
        pass

    def _name(t: dict[str, object]) -> str:
        fn = as_dict(t.get('function'), {})
        return as_str(t.get('name') or fn.get('name'), '')

    return [t for t in tools if _name(t) != _INSTALL_COMPLETE]
