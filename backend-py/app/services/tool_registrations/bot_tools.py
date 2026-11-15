"""Bot tools: the main agent creates, configures and retires bots itself.

WHY THESE EXIST
---------------
The redesign makes bots a toolable concept: the user no longer builds and manages
them by hand, but the concept stays. These four tools are the agent's door to the
roster — ``create_bot``, ``list_bots``, ``update_bot``, ``retire_bot``.

They are deliberately THIN. Every primitive they need already exists in
``agent_roster`` (registry record + canonical Bot Chat + uiMeta + cascade delete)
and ``agent_registry`` (the permission engine). This module adds exactly one
thing the primitives do not have: the permission ceiling.

THE CEILING — why this is the whole security property
----------------------------------------------------
``agent_registry._effectivePermissions`` already walks a chain and intersects a
child's declaration with its parent's. But ``parentId`` is written **only by the
human door** (``routers/agents.py``, actor='ui'); the model door cannot set it.
A bot created by a turn therefore has no parent, and an unbounded declaration
under an unknown parent resolves to ``None`` — which the ceiling reads as "no
restriction at all". That is the fail-OPEN direction, and it is exactly the
escalation the walk was written to stop.

So a created bot's permissions are SET EXPLICITLY to the intersection of what
was asked for and what the creating session is allowed to use. Asked-for tools
outside the parent's surface are dropped and named in the result, never silently
accepted. ``deriveChildPermissions`` does the arithmetic; this module decides
what to pass it.

Registration is step 2/3 of the plan: ``register_all()`` calls ``register()``,
but these are NOT wired into the main agent's tool loop until step 3 — the same
1a/1b discipline the trading plan uses. Nothing here is advertised until the
owner has seen it.
"""

from __future__ import annotations

import logging

from app.json_narrowing import as_list, as_str
from app.services import tool_registry

logger = logging.getLogger(__name__)


# ── the ceiling ──────────────────────────────────────────────────────────────


def _parent_tool_surface(parentId: str) -> set[str] | None:
    """The tools the creating agent may actually use, or None for unrestricted.

    ``None`` means the chain declares nothing (``_declaredPermissions`` returns
    None) or explicitly grants "all" — in both cases the parent has no bound to
    intersect with, so the child is not clamped by this step. The caller must
    still not invent an "all" for the child: an unbounded child under an
    unbounded parent stays unbounded, which is the parent's choice, not a leak.
    """
    from app.services.tools import agent_registry

    if not parentId:
        return None
    agent = agent_registry.getAgent(parentId)
    if not isinstance(agent, dict):
        # A dangling parent is not a restriction (see _effectivePermissions: an
        # unknown lineage returns None, never an empty set), so the child keeps
        # its own declaration and the caller logs it.
        return None
    perms = agent_registry._declaredPermissions(agent)
    if perms is None:
        return None
    if 'all' in perms:
        return None
    return set(perms)


def _ceiling_for(asked: list[str], parentId: str) -> tuple[list[str], list[str]]:
    """Clamp the requested tool list to the creating agent's own surface.

    Returns ``(granted, dropped)``. An empty ``asked`` means "no restriction
    stated", which is not the same as "all": under a bounded parent it becomes
    the parent's surface verbatim, and under an unbounded parent it stays
    unbounded. Naming the tools is always narrower than inheriting, so an
    explicit list can only ever reduce the ceiling.
    """
    parent = _parent_tool_surface(parentId)
    if not asked:
        return ([] if parent is None else sorted(parent)), []
    if parent is None:
        return list(asked), []
    granted = [t for t in asked if t in parent]
    dropped = [t for t in asked if t not in parent]
    return granted, dropped


# ── handlers ─────────────────────────────────────────────────────────────────


async def _createBot(
    name: str = '',
    role: str = '',
    instructions: str = '',
    tools: list | None = None,
    model: str = '',
    provider: str = '',
) -> str:
    """Create a Bot and its canonical chat. See the create_bot tool schema."""
    import json

    from app.services import agent_roster as roster
    from app.services.workbench import workbench as wb
    from app.services.workbench.context import currentSessionId

    clean = (name or '').strip()
    if not clean:
        return 'Error: name is required.'

    session = wb.getWorkbenchSession(currentSessionId.get())
    if session is None:
        return 'Error: no active session — a bot cannot be created from a turn without one.'

    # Who is asking? The ceiling is computed against the asking agent's own
    # surface, never against "the product's" surface.
    parentId = as_str(getattr(session, '_current_agent', '')) or ''

    asked_tools = [as_str(t).strip() for t in (tools or []) if as_str(t).strip()]
    granted, dropped = _ceiling_for(asked_tools, parentId)

    bot = roster.create_bot(
        name=clean,
        role=as_str(role).strip(),
        description=as_str(instructions).strip(),
        model=as_str(model).strip(),
        provider=as_str(provider).strip(),
        actor='model',
    )

    # create_bot has no permissions argument (it is the human door's job to set
    # those). The ceiling is written here, explicitly, so a created bot can never
    # exceed the agent that made it.
    if granted:
        from app.services.tools import agent_registry

        agent_registry.updateAgent(
            as_str(bot.get('id')),
            {'permissions': granted},
            actor='model',
        )
        bot = roster.get_bot(as_str(bot.get('id'))) or bot

    receipt = {
        'kind': 'bot_created',
        'agentId': as_str(bot.get('id')),
        'name': as_str(bot.get('name')),
        'role': as_str(bot.get('role')) or as_str(role).strip(),
        'permissions': granted,
        'droppedPermissions': dropped,
        'canonicalChatId': as_str(bot.get('canonicalChatId')),
    }
    # The receipt is the tool RESULT: the UI renders it as a compact card with
    # edit and undo, and the orchestrator reads it to report what it made.
    return json.dumps(receipt, default=str)


async def _listBots() -> str:
    """List every Bot with its role, permissions and chat. See the schema."""
    import json

    from app.services import agent_roster as roster
    from app.services.tools import agent_registry

    out = []
    for bot in roster.list_bots():
        agentId = as_str(bot.get('id'))
        record = agent_registry.getAgent(agentId) or {}
        out.append(
            {
                'agentId': agentId,
                'name': as_str(bot.get('name')),
                'role': as_str(record.get('role')) or as_str(bot.get('title')),
                'permissions': as_list(record.get('permissions')),
                'tools': as_list(record.get('tools')),
                'model': as_str(record.get('model')),
                'canonicalChatId': as_str(bot.get('canonicalChatId')),
            }
        )
    if not out:
        return 'No bots yet. Create one with create_bot.'
    return json.dumps({'bots': out}, default=str)


async def _updateBot(agentId: str = '', role: str = '', instructions: str = '', tools: list | None = None) -> str:
    """Change a Bot's role, instructions or tool ceiling. See the schema."""
    import json

    from app.services.tools import agent_registry
    from app.services.workbench import workbench as wb
    from app.services.workbench.context import currentSessionId

    target = (agentId or '').strip()
    if not target:
        return 'Error: agentId is required.'

    session = wb.getWorkbenchSession(currentSessionId.get())
    if session is None:
        return 'Error: no active session.'
    parentId = as_str(getattr(session, '_current_agent', '')) or ''

    record = agent_registry.getAgent(target)
    if not record:
        return f'Error: no bot with id {target}.'

    updates: dict[str, object] = {}
    if as_str(role).strip():
        updates['role'] = as_str(role).strip()
    if as_str(instructions).strip():
        updates['description'] = as_str(instructions).strip()

    if tools is not None:
        asked = [as_str(t).strip() for t in tools if as_str(t).strip()]
        # Re-clamp against the SAME rule as creation: an update can narrow a bot
        # but never widen it past its creator's surface.
        granted, dropped = _ceiling_for(asked, parentId)
        updates['permissions'] = granted
    else:
        granted, dropped = [], []

    if updates:
        agent_registry.updateAgent(target, updates, actor='model')

    return json.dumps(
        {
            'kind': 'bot_updated',
            'agentId': target,
            'updated': sorted(updates.keys()),
            'permissions': granted if tools is not None else as_list(record.get('permissions')),
            'droppedPermissions': dropped,
        },
        default=str,
    )


async def _retireBot(agentId: str = '') -> str:
    """Retire a Bot: remove the record and cascade its private data."""
    import json

    from app.services import agent_roster as roster

    target = (agentId or '').strip()
    if not target:
        return 'Error: agentId is required.'
    bot = roster.get_bot(target)
    if not bot:
        return f'Error: no bot with id {target}.'
    if not roster.delete_bot(target, actor='model'):
        return (
            f'Could not retire {as_str(bot.get("name")) or target} — the default assistant bot cannot be removed.'
        )
    return json.dumps(
        {
            'kind': 'bot_retired',
            'agentId': target,
            'name': as_str(bot.get('name')),
            'note': 'The record, its facts and its canonical chat are gone. Its chat history remains in the session list.',
        },
        default=str,
    )


# ── registration ─────────────────────────────────────────────────────────────


def register() -> None:
    """Register the four bot tools (not wired into the main tool loop yet)."""
    tool_registry.register(
        'create_bot',
        'Create a Bot: a standing collaborator with a name, a role, instructions and its own canonical chat. '
        'Bots are for work you want done repeatedly by a named helper — not for a one-off task, which is what '
        'spawn_subagents is for. The bot cannot be given a tool the creating agent does not already have; any '
        'requested tool outside that surface is dropped and named in the result. No approval is needed to create one.',
        _createBot,
        {
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'Short unique name for the bot.'},
                'role': {
                    'type': 'string',
                    'description': 'One line: what this bot is for (e.g. "literature reviewer").',
                },
                'instructions': {
                    'type': 'string',
                    'description': 'How the bot should work: method, constraints, output shape.',
                },
                'tools': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Tool names the bot may use. Omit to inherit the creating agent\'s surface.',
                },
                'model': {'type': 'string', 'description': 'Model override (optional; default is the session model).'},
                'provider': {'type': 'string', 'description': 'Provider for that model (optional).'},
            },
            'required': ['name'],
        },
    )
    tool_registry.register(
        'list_bots',
        'List every Bot with its role, permissions, tools and canonical chat id.',
        _listBots,
        {'type': 'object', 'properties': {}, 'required': []},
    )
    tool_registry.register(
        'update_bot',
        'Change a Bot\'s role, instructions or tool ceiling. Narrowing its tools is allowed; widening them past the '
        'creating agent\'s own surface is not, and any tool outside it is dropped and named in the result.',
        _updateBot,
        {
            'type': 'object',
            'properties': {
                'agentId': {'type': 'string', 'description': 'Bot id (from list_bots).'},
                'role': {'type': 'string', 'description': 'New one-line role (omit to leave unchanged).'},
                'instructions': {'type': 'string', 'description': 'New instructions (omit to leave unchanged).'},
                'tools': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'New tool list. Omit to leave the ceiling unchanged.',
                },
            },
            'required': ['agentId'],
        },
    )
    tool_registry.register(
        'retire_bot',
        'Retire a Bot: remove its record and cascade its private data (facts, DMs, canonical chat). '
        'The default assistant bot cannot be retired. Chat history stays in the session list.',
        _retireBot,
        {
            'type': 'object',
            'properties': {'agentId': {'type': 'string', 'description': 'Bot id (from list_bots).'}},
            'required': ['agentId'],
        },
    )