#!/usr/bin/env python3
"""End-to-end: the orchestrator creates a bot from a chat request.

WHY THIS EXISTS
---------------
The plan's acceptance is behavioural, not structural: "the orchestrator creates
a bot from a chat request, the bot appears in the minimal list with a receipt
card, and the user can open it, edit it, and retire it from the UI."

This script drives the REAL handlers in order — create → list → update → retire —
against a scratch data dir, and prints the receipts the UI would render. It does
not start a server and does not need a model: the handlers are pure functions of
the registry, so the path is exercisable without burning a key.

The session is stubbed because the handlers read the asking agent from it; the
stub is the one piece of scaffolding, and it is named as such.

USAGE
-----
    backend-py/.venv/Scripts/python.exe scripts/check-bot-flow.py
Exit 0 = the whole path works; 1 = a step failed and is named.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend-py"))
os.environ.setdefault("HARNESS_DATA_DIR", tempfile.mkdtemp(prefix="bot-flow-"))

from app.services.tool_registrations import bot_tools  # noqa: E402


class StubSession:
    """The minimum the handlers read: who is asking, and an id."""

    id = "wb_stub"
    _current_agent = "agent_limited"


def main() -> int:
    from app.services import agent_roster as roster
    from app.services import tool_registry
    from app.services.tools import agent_registry
    from app.services.workbench import workbench as wb

    results: list[bool] = []

    def step(label: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print(f"  {'ok  ' if ok else 'FAIL'} {label}{(' — ' + detail) if detail else ''}")

    # The asking agent is deliberately limited: read_file only. Everything the
    # flow creates must be clamped to that.
    parent = agent_registry.createAgent("agent_limited", permissions=["read_file"], actor="flow")
    parent_id = str(parent["id"])

    # Point the stub's agent id at the real parent.
    StubSession._current_agent = parent_id
    wb.getWorkbenchSession = lambda _sid: StubSession()  # type: ignore[assignment]

    print("orchestrator creates a bot from a chat request")

    # 1. create_bot, asking for a tool the parent does not have.
    raw = asyncio.run(
        bot_tools._createBot(
            name="lit-reviewer",
            role="literature reviewer",
            instructions="Read papers, extract claims, flag unsourced ones.",
            tools=["read_file", "run_command"],
        )
    )
    created = json.loads(raw)
    step("create_bot returns a receipt", created.get("kind") == "bot_created", created.get("name", ""))
    step(
        "ceiling applied — run_command dropped",
        created.get("droppedPermissions") == ["run_command"] and created.get("permissions") == ["read_file"],
        f"granted={created.get('permissions')} dropped={created.get('droppedPermissions')}",
    )

    agent_id = str(created.get("agentId") or "")
    step("a canonical chat was created", bool(created.get("canonicalChatId")))

    stored = agent_registry.getAgent(agent_id) or {}
    step(
        "stored permissions are the clamped set",
        sorted(stored.get("permissions") or []) == ["read_file"],
        str(stored.get("permissions")),
    )

    # 2. list_bots — the minimal list the UI renders.
    listed = json.loads(asyncio.run(bot_tools._listBots()))
    names = [b.get("name") for b in listed.get("bots", [])]
    step("list_bots includes the new bot", "lit-reviewer" in names, str(names))

    # 3. update_bot — narrow the role, and try to widen the ceiling.
    upd = json.loads(
        asyncio.run(
            bot_tools._updateBot(
                agentId=agent_id,
                role="literature reviewer (drafts)",
                tools=["read_file", "web_search"],
            )
        )
    )
    step("update_bot narrows role", "role" in (upd.get("updated") or []), str(upd.get("updated")))
    step(
        "update cannot widen past the creator — web_search dropped",
        upd.get("droppedPermissions") == ["web_search"],
        str(upd.get("droppedPermissions")),
    )

    # 4. retire_bot — the UI's undo.
    ret = json.loads(asyncio.run(bot_tools._retireBot(agentId=agent_id)))
    step("retire_bot removes the record", ret.get("kind") == "bot_retired")
    step("the record is gone", agent_registry.getAgent(agent_id) is None)

    # 5. The default assistant is not retirable (guard survives the flow).
    # The default is a boot backfill, so ask for it the way boot does.
    default = roster.ensure_default_bot(actor="flow")
    if not default:
        step("default assistant exists", False)
    else:
        out = asyncio.run(bot_tools._retireBot(agentId=str(default["id"])))
        # A refusal is a plain sentence, not a receipt — the handler returns a
        # human-readable string for every error path (same as the rest of
        # agent_tools). Accept either shape so the check tests the guard, not
        # the serialisation.
        try:
            d = json.loads(out)
            text = json.dumps(d).lower()
        except (json.JSONDecodeError, TypeError):
            text = str(out).lower()
        step("default assistant refused", "cannot be removed" in text or "could not" in text, str(out)[:70])
        step("default still present", roster.get_default_bot() is not None)

    # Clean up the stub's parent so a rerun starts clean.
    agent_registry.deleteAgent(parent_id, actor="flow")

    passed = sum(1 for r in results if r)
    print(f"\n{passed}/{len(results)} flow check(s) passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())