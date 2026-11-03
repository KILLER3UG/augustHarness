"""Tool registrations for the shared space.

Four tools, one surface: every session and bot can hand files to every
other through ``{dataDir}/shared/`` — the collaboration surface rerun
proves out. Paths are jailed to the shared root by the standard sandbox
door (see services/tools/shared_space.py).
"""

from __future__ import annotations

from app.services import tool_registry
from app.services.tools import shared_space


def _jstr(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f'{field} must be a string (got {type(value).__name__})')
    return value


async def _read(args: dict) -> str:
    try:
        return shared_space.shared_read(_jstr(args.get('path'), 'path'))
    except Exception as exc:  # noqa: BLE001 — tool receipts are strings
        return f'Error: {exc}'


async def _write(args: dict) -> str:
    try:
        return shared_space.shared_write(_jstr(args.get('path'), 'path'), _jstr(args.get('content'), 'content'))
    except Exception as exc:  # noqa: BLE001
        return f'Error: {exc}'


async def _list(args: dict) -> str:
    try:
        raw = args.get('path') or '.'
        return shared_space.shared_list(_jstr(raw, 'path'))
    except Exception as exc:  # noqa: BLE001
        return f'Error: {exc}'


async def _search(args: dict) -> str:
    try:
        return shared_space.shared_search(_jstr(args.get('query'), 'query'))
    except Exception as exc:  # noqa: BLE001
        return f'Error: {exc}'


_SHARED_NOTE = (
    'One folder EVERY session and bot can read and write — pass files to another '
    'chat or bot here instead of repeating content. Paths are relative to the '
    'shared root; no traversal outside it.'
)


def register() -> None:
    tool_registry.register(
        'shared_list',
        'List files in the shared space every agent can read and write. ' + _SHARED_NOTE,
        _list,
        {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': 'Subfolder to list, "." for the root'},
            },
        },
        keywords=['shared', 'handoff', 'collaborate', 'share files', 'other bot', 'other chat'],
    )
    tool_registry.register(
        'shared_read',
        'Read a file from the shared space. ' + _SHARED_NOTE,
        _read,
        {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': 'Path relative to the shared root'},
            },
            'required': ['path'],
        },
        keywords=['shared', 'handoff', 'collaborate', 'share files', 'other bot', 'other chat'],
    )
    tool_registry.register(
        'shared_write',
        'Write a file into the shared space so another chat or bot can pick it up. ' + _SHARED_NOTE,
        _write,
        {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': 'Path relative to the shared root'},
                'content': {'type': 'string', 'description': 'Full text to write'},
            },
            'required': ['path', 'content'],
        },
        keywords=['shared', 'handoff', 'collaborate', 'share files', 'other bot', 'other chat'],
    )
    tool_registry.register(
        'shared_search',
        'Substring-search across every text file in the shared space. ' + _SHARED_NOTE,
        _search,
        {
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': 'Case-insensitive substring to find'},
            },
            'required': ['query'],
        },
        keywords=['shared', 'handoff', 'collaborate', 'share files', 'other bot', 'other chat'],
    )
