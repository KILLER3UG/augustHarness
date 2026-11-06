"""check_tool_schemas.py — the typed-schema allowlist that only shrinks.

E3: every tool whose registered schema declares ``properties`` can be
validated at dispatch (``toolSchemaCheck: off|warn|strict``). A tool whose
schema has none is NOT validated — guessing its shape would invent a
contract the handler does not have — so it belongs in the allowlist.

This check reads the live registry and compares the schema-less set
against ``scripts/tool-schemas-unconverted.txt``. The list may only
SHRINK: converting a tool to a real schema removes its line (a conversion
should also drop it here in the same commit); a NEW schema-less tool
fails the gate, because silent schema debt is how every argument becomes
a guess.

Exit 0: the allowlist matches the live set exactly.
Exit 1: a new schema-less tool, or a stale line for a converted one.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'backend-py'))
LIST_PATH = REPO / 'scripts' / 'tool-schemas-unconverted.txt'


def main() -> int:
    import os

    # Throwaway data dir: the gate must not touch the user's store.
    os.environ.setdefault('HARNESS_DATA_DIR', str(Path(__file__).parent / '.check-schemas-data'))

    from app.services.tool_registrations import register_all
    from app.services.tool_registry import listTools

    register_all()

    schemaless: set[str] = set()
    for tool in listTools():
        parameters = tool.get('parameters')
        properties = parameters.get('properties') if isinstance(parameters, dict) else None
        if not isinstance(properties, dict) or not properties:
            schemaless.add(str(tool.get('name', '')))
    schemaless.discard('')

    listed: set[str] = set()
    if LIST_PATH.exists():
        for line in LIST_PATH.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                listed.add(line)

    grew = sorted(schemaless - listed)
    stale = sorted(listed - schemaless)

    if grew:
        print(f'check-tool-schemas: {len(grew)} NEW schema-less tool(s) — convert them or argue the schema:')
        for name in grew:
            print(f'  + {name}')
    if stale:
        print(f'check-tool-schemas: {len(stale)} stale line(s) — the tool grew a schema; drop the line:')
        for name in stale:
            print(f'  - {name}')

    if not grew and not stale:
        total = len(listTools())
        print(
            f'check-tool-schemas: ok — {total} tools, {len(schemaless)} schema-less '
            f'(allowlist exact, only shrinks)'
        )
        return 0
    print(
        'The allowlist only shrinks. A new line needs a reason written next to the tool '
        'registration; removing one is the win.'
    )
    return 1


if __name__ == '__main__':
    sys.exit(main())
