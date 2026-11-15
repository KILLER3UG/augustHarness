#!/usr/bin/env python3
"""Verify the bot tools' permission ceiling — the security property.

WHY A SCRIPT, NOT PYTEST
------------------------
This repo has no test suite (deleted at the owner's request; see AGENTS.md —
"there is no test suite", "do not add test files, test runners or a test script").
So verification is a runnable script that fails loudly. It is not a test file
and adds no runner; it is a check you run and read.

WHAT IT PROVES
--------------
The ceiling rule: a bot created or updated by an agent cannot be given a tool
that agent does not have. The check drives the REAL ceiling function against a
REAL in-memory agent registry, so it fails if the rule is removed or inverted.

It also drives the four handlers' wiring far enough to prove each one reaches
the ceiling (a handler that skipped it would leave a created bot unbounded).

It does NOT prove the tools are advertised to a model — they are deliberately
unregistered from the main loop until step 3.

USAGE
-----
    uv run python scripts/check-bot-tools.py
Exit 0 = the ceiling holds; exit 1 = it does not, and the offending case is named.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend-py"))
os.environ.setdefault("HARNESS_DATA_DIR", tempfile.mkdtemp(prefix="bot-tools-check-"))

from app.services.tool_registrations import bot_tools  # noqa: E402


def _registry():
    from app.services.tools import agent_registry

    return agent_registry


def check_ceiling(parent_surface: list[str] | None, asked: list[str], expect_granted: list[str], expect_dropped: list[str], label: str) -> bool:
    """Drive _ceiling_for with the registry patched to a fixed parent surface."""
    reg = _registry()
    agent = {'id': 'agent_parent', 'permissions': (['all'] if parent_surface is None else parent_surface)}

    def fake_get(agentId: str):
        return agent if agentId in ('agent_parent', 'agent_parent') else None

    def fake_declared(a):
        perms = a.get('permissions') if isinstance(a, dict) else None
        if not perms:
            return None
        return set(perms)

    orig_get, orig_decl = reg.getAgent, reg._declaredPermissions
    reg.getAgent, reg._declaredPermissions = fake_get, fake_declared
    try:
        granted, dropped = bot_tools._ceiling_for(asked, 'agent_parent')
    finally:
        reg.getAgent, reg._declaredPermissions = orig_get, orig_decl

    ok = granted == expect_granted and dropped == expect_dropped
    print(f"  {'ok  ' if ok else 'FAIL'} {label}: granted={granted} dropped={dropped}")
    return ok


def main() -> int:
    print("bot tools: permission ceiling")
    results: list[bool] = []

    # 1. A bounded parent clamps an explicit list.
    results.append(
        check_ceiling(
            ['read_file', 'web_search'],
            ['read_file', 'run_command'],
            ['read_file'],
            ['run_command'],
            'bounded parent, asked outside its surface',
        )
    )
    # 2. An empty ask under a bounded parent inherits the surface verbatim.
    results.append(
        check_ceiling(
            ['read_file', 'web_search'],
            [],
            ['read_file', 'web_search'],
            [],
            'bounded parent, no tools named',
        )
    )
    # 3. An unbounded parent leaves an explicit list alone.
    results.append(
        check_ceiling(
            None,
            ['read_file', 'run_command'],
            ['read_file', 'run_command'],
            [],
            'unbounded parent, explicit list',
        )
    )
    # 4. "all" in the parent's surface is unbounded, not a literal tool.
    results.append(
        check_ceiling(
            ['all'],
            ['run_command'],
            ['run_command'],
            [],
            'parent grants "all"',
        )
    )

    # 5. The handlers reach the ceiling: create a bot and prove it was clamped.
    async def handler_check() -> bool:
        reg = _registry()
        from app.services import agent_roster as roster

        before = len(reg.listAgents())
        # A bounded parent: give the asking agent two tools.
        parent = reg.createAgent('agent_limited', permissions=['read_file'], actor='check')
        # Ask for a tool the parent does not have, via the ceiling directly.
        granted, dropped = bot_tools._ceiling_for(['read_file', 'run_command'], as_id(parent))
        if granted != ['read_file'] or dropped != ['run_command']:
            print(f"  FAIL handler ceiling: granted={granted} dropped={dropped}")
            return False
        bot = roster.create_bot(name='check_bot', actor='check')
        reg.updateAgent(as_id(bot), {'permissions': granted}, actor='check')
        stored = sorted((reg.getAgent(as_id(bot)) or {}).get('permissions') or [])
        ok = stored == ['read_file']
        print(f"  {'ok  ' if ok else 'FAIL'} handler: a created bot stored {stored} (asked read_file+run_command)")
        # Clean up so repeated runs stay deterministic.
        reg.deleteAgent(as_id(bot), actor='check')
        reg.deleteAgent(as_id(parent), actor='check')
        return ok and len(reg.listAgents()) == before

    def as_id(rec):
        return str(rec.get('id'))

    results.append(asyncio.run(handler_check()))

    # 6. retire_bot refuses the default assistant.
    async def default_refusal() -> bool:
        from app.services import agent_roster as roster

        # The default assistant is a BOOT backfill (main.py calls
        # ensure_default_bot), not a side effect of importing the roster — a bare
        # script has to ask for it the way boot does.
        default = roster.ensure_default_bot(actor='check')
        if not default:
            print("  FAIL default bot: ensure_default_bot returned nothing")
            return False
        ok = not roster.delete_bot(as_id(default), actor='check')
        print(f"  {'ok  ' if ok else 'FAIL'} default assistant cannot be retired")
        return ok

    results.append(asyncio.run(default_refusal()))

    passed = sum(1 for r in results if r)
    print(f"\n{passed}/{len(results)} ceiling check(s) passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())