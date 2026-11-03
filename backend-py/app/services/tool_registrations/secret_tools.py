"""Tool registrations for chat-requested secrets.

``request_secret`` asks the user for a credential through a masked form on
the tool-call card; the value goes straight into the encrypted store and
the model sees only the NAME and a status. ``secret_check`` reports the
status without the value. Neither tool can ever read a secret back.
"""

from __future__ import annotations

import json

from app.services import tool_registry
from app.services.tools import chat_secrets


async def requestSecret(args: dict) -> str:
    name = args.get('name')
    description = args.get('description')
    if not isinstance(name, str):
        return 'Error: name must be a string (UPPER_SNAKE).'
    if not isinstance(description, str):
        description = ''
    from app.services.workbench.context import currentSessionId

    sid = currentSessionId.get()
    try:
        entry = chat_secrets.request(name, description, sid)
    except ValueError as exc:
        return f'Error: {exc}'
    payload = {
        'secretRequest': {
            'name': entry.get('name'),
            'description': entry.get('description'),
            'status': entry.get('status'),
        }
    }
    return json.dumps(
        {
            **payload,
            'note': (
                'A masked form was shown to the user on this card. The value goes '
                f'straight into the encrypted store as {entry.get("name")} and never '
                'enters the conversation — do not ask them to paste it here. When '
                'they fill the form, a message saying so arrives; you can also poll '
                'secret_check. Reference the secret in MCP server env as '
                f'${{{entry.get("name")}}}.'
            ),
        }
    )


async def secretCheck(args: dict) -> str:
    name = args.get('name')
    if not isinstance(name, str):
        return 'Error: name must be a string (UPPER_SNAKE).'
    try:
        status = chat_secrets.check(name)
    except ValueError as exc:
        return f'Error: {exc}'
    return json.dumps({'secretRequest': status})


def register() -> None:
    tool_registry.register(
        'request_secret',
        'Ask the user for a credential (API key, service token) through a masked form shown '
        'on this tool call card. The value goes straight into the encrypted store and NEVER '
        'enters the conversation — you learn only the NAME and the status. Reference the '
        'secret in MCP server env as ${NAME}.',
        requestSecret,
        {
            'type': 'object',
            'properties': {
                'name': {
                    'type': 'string',
                    'description': 'UPPER_SNAKE name the secret is stored and referenced under, e.g. GITHUB_TOKEN.',
                },
                'description': {
                    'type': 'string',
                    'description': 'One line telling the user what the credential is for.',
                },
            },
            'required': ['name'],
        },
        keywords=['secret', 'credential', 'api key', 'token', 'password', 'ask user'],
    )
    tool_registry.register(
        'secret_check',
        'Check whether a chat-requested secret has been filled (status only — never the value).',
        secretCheck,
        {
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'The UPPER_SNAKE secret name.'},
            },
            'required': ['name'],
        },
        keywords=['secret', 'credential', 'status'],
    )
