"""The shared space: one folder every session and bot can read and write.

Rerun's insight, ported: agents that can hand files to each other through a
common surface collaborate without a bespoke message protocol. August's
subagents already share the parent's workspace, but separate chats and bots
have separate workspaces — ``{dataDir}/shared/`` is the bridge. It lives
under the data dir (user state: never installer-staged, kept on update)
and every path is jailed to it through the standard sandbox door, so the
hardline protections apply exactly as elsewhere.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.lib.paths import dataDir
from app.services.sandbox.paths import bind_path

logger = logging.getLogger(__name__)

_SHARED_DIRNAME = 'shared'
_READ_CAP = 20 * 1024 * 1024
_LIST_CAP = 500


def shared_root() -> Path:
    root = dataDir() / _SHARED_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve(path: str, *, for_write: bool) -> Path:
    """Jail a tool-supplied path inside the shared space."""
    root = str(shared_root())
    bound, err = bind_path(path or '.', root, for_write=for_write)
    if err or bound is None:
        raise ValueError(err or f'Invalid shared path: {path}')
    return bound


def shared_read(path: str) -> str:
    """Read a file from the shared space. Text in as text out; a binary file
    comes back as metadata (the model asks for what it can use)."""
    bound = _resolve(path, for_write=False)
    if not bound.is_file():
        raise ValueError(f'Not a file in the shared space: {path}')
    size = bound.stat().st_size
    if size > _READ_CAP:
        raise ValueError(
            f'{bound.name} is {size} bytes — over the {_READ_CAP // (1024 * 1024)} MB shared read cap. '
            'Large handoffs should stay files the recipient copies with its own tools.'
        )
    try:
        return bound.read_text('utf-8')
    except UnicodeDecodeError:
        return f'[binary file: {bound.name}, {size} bytes — read it with run_command from the shared dir if you need its bytes]'


def shared_write(path: str, content: str) -> str:
    bound = _resolve(path, for_write=True)
    if bound.suffix.lower() == '.py':
        # Scratch-code convention from the workspace: keep the shared space
        # for handoffs, not for planted import shadows.
        raise ValueError('shared_write refuses .py files — hand over code as .txt or inside a project folder.')
    bound.parent.mkdir(parents=True, exist_ok=True)
    bound.write_text(content, encoding='utf-8')
    return f'Written to shared/{bound.relative_to(shared_root()).as_posix()} ({len(content)} chars).'


def shared_list(path: str = '.') -> str:
    """List the shared space (bounded; relative to the given subfolder)."""
    bound = _resolve(path or '.', for_write=False)
    if not bound.exists():
        return '(empty)'
    if bound.is_file():
        return f'{bound.name} ({bound.stat().st_size} bytes)'
    rows: list[str] = []
    for entry in sorted(bound.rglob('*')):
        if len(rows) >= _LIST_CAP:
            rows.append(f'… capped at {_LIST_CAP} entries')
            break
        rel = entry.relative_to(shared_root()).as_posix()
        if entry.is_dir():
            rows.append(f'{rel}/')
        else:
            rows.append(f'{rel} ({entry.stat().st_size} bytes)')
    return '\n'.join(rows) if rows else '(empty)'


def shared_search(query: str) -> str:
    """Substring search across the shared space's text files."""
    query = (query or '').strip()
    if not query:
        raise ValueError('query is required')
    hits: list[str] = []
    for entry in shared_root().rglob('*'):
        if not entry.is_file() or entry.stat().st_size > _READ_CAP:
            continue
        try:
            text = entry.read_text('utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if query.lower() in line.lower():
                rel = entry.relative_to(shared_root()).as_posix()
                hits.append(f'{rel}:{line_no}: {line.strip()[:160]}')
                if len(hits) >= 40:
                    return '\n'.join(hits) + '\n… capped at 40 hits'
    return '\n'.join(hits) if hits else f'No hits for {query!r} in the shared space.'
