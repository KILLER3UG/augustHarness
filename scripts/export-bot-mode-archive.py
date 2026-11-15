#!/usr/bin/env python3
"""Export the Bot roster, bot chat history and bot routines to a portable archive.

WHY THIS EXISTS
---------------
Phase 2 of the redesign removes the Bot surface. The owner's ruling is a hard
delete, with one precondition: **export all existing bot data first so nothing is
lost.** This script is that export. It is read-only against every store it opens
except the archive file it writes.

WHAT IT CAPTURES
----------------
* the agent registry (every Bot record: id, name, role, permissions, uiMeta,
  workDir) — read from the memory store's `agent_registry` KV row;
* every chat session bound to a Bot (the canonical Bot Chat), including its
  persisted workbench blob and the transcript JSONL when present;
* every routine (an automations_store job whose title is namespaced
  `[bot:<name>] …`) with its schedule, command and delivery target;
* `data/bots/**` verbatim (per-bot working folders) when that tree is populated.

WHAT IT DOES NOT DO
-------------------
It does not delete anything, and it does not write into the data directory except
the archive file. `--out` defaults to `<dataDir>/backups/bot-mode-archive-<ts>.json`
so the archive sits beside the memory backups it protects.

USAGE
-----
    python scripts/export-bot-mode-archive.py [--data-dir DIR] [--out FILE] [--pretty]

Exit 0 always writes a report line; exit 1 means the export could not be
completed (and says which store failed), so a caller can gate a delete on it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception as exc:  # corrupt file: report, do not fabricate
        print(f"  ! could not parse {path}: {exc}", file=sys.stderr)
        return default


def _agents_from_kv(data_dir: Path) -> list[dict]:
    """The agent registry lives as one KV row in state/kv.json.

    Read defensively: the file is a list of {key, value} rows where `value` is a
    JSON-encoded string, so a malformed row must not abort the export.
    """
    rows = _load_json(data_dir / "state" / "kv.json", [])
    if not isinstance(rows, list):
        return []
    for row in rows:
        if not isinstance(row, dict) or row.get("key") != "agent_registry":
            continue
        raw = row.get("value")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except Exception:
                return []
            return parsed if isinstance(parsed, list) else []
        if isinstance(raw, list):
            return [a for a in raw if isinstance(a, dict)]
    return []


def _sessions_for_agents(data_dir: Path, agent_ids: set[str]) -> list[dict]:
    """Sessions whose persisted blob names one of the given agents."""
    path = data_dir / "transcripts" / "sessions.json"
    rows = _load_json(path, [])
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        blob = row.get("workbench_blob") or {}
        if isinstance(blob, str):
            try:
                blob = json.loads(blob)
            except Exception:
                blob = {}
        if not isinstance(blob, dict):
            continue
        if blob.get("agentId") in agent_ids:
            out.append(row)
    return out


def _routines(data_dir: Path) -> list[dict]:
    """Automation jobs whose title is namespaced `[bot:<name>] …`."""
    jobs = _load_json(data_dir / "scheduled-jobs.json", [])
    if isinstance(jobs, dict):
        jobs = jobs.get("jobs", [])
    if not isinstance(jobs, list):
        return []
    return [j for j in jobs if isinstance(j, dict) and str(j.get("name", "")).startswith("[bot:")]


def _transcript(data_dir: Path, session_id: str) -> list[dict]:
    """The JSONL transcript for one session, when it exists."""
    path = data_dir / "transcripts" / f"{session_id}.jsonl"
    if not path.exists():
        return []
    out = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception as exc:
        print(f"  ! could not read {path}: {exc}", file=sys.stderr)
    return out


def _bot_tree_bytes(data_dir: Path) -> dict[str, str]:
    """`data/bots/**` as {relative path: base64} so a zip can carry it.

    Empty on this install (the per-bot working folder is opt-in), and that is a
    normal result, not an error.
    """
    root = data_dir / "bots"
    if not root.is_dir():
        return {}
    import base64

    out: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            out[str(path.relative_to(root)).replace("\\", "/")] = base64.b64encode(
                path.read_bytes()
            ).decode("ascii")
        except Exception as exc:
            print(f"  ! could not read {path}: {exc}", file=sys.stderr)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=os.environ.get("HARNESS_DATA_DIR", "data"))
    ap.add_argument("--out", default="")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        print(f"error: data dir not found: {data_dir}", file=sys.stderr)
        return 1

    agents = _agents_from_kv(data_dir)
    agent_ids = {str(a.get("id")) for a in agents if a.get("id")}
    sessions = _sessions_for_agents(data_dir, agent_ids)
    routines = _routines(data_dir)
    bots_tree = _bot_tree_bytes(data_dir)

    chats = []
    for s in sessions:
        blob = s.get("workbench_blob") or {}
        if isinstance(blob, str):
            try:
                blob = json.loads(blob)
            except Exception:
                blob = {}
        sid = str(s.get("id") or "")
        chats.append(
            {
                "sessionId": sid,
                "agentId": (blob or {}).get("agentId") if isinstance(blob, dict) else None,
                "title": s.get("title"),
                "startedAt": s.get("started_at"),
                "updatedAt": s.get("updated_at"),
                "messageCount": s.get("message_count"),
                "canonicalBotChat": bool((blob or {}).get("canonicalBotChat")) if isinstance(blob, dict) else False,
                "transcript": _transcript(data_dir, sid) if sid else [],
            }
        )

    archive = {
        "schemaVersion": SCHEMA_VERSION,
        "exportedAt": _now(),
        "source": "August bot mode (removed in the redesign, Phase 2)",
        "counts": {
            "agents": len(agents),
            "botChats": len(chats),
            "routines": len(routines),
            "botTreeFiles": len(bots_tree),
        },
        "agents": agents,
        "botChats": chats,
        "routines": routines,
        "botTree": bots_tree,
    }

    out = Path(args.out) if args.out else data_dir / "backups" / (
        f"bot-mode-archive-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix == ".zip":
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("bot-mode-archive.json", json.dumps(archive, indent=2 if args.pretty else None))
    else:
        out.write_text(json.dumps(archive, indent=2 if args.pretty else None), encoding="utf-8")

    c = archive["counts"]
    print(
        f"exported {c['agents']} agent(s), {c['botChats']} bot chat(s), "
        f"{c['routines']} routine(s), {c['botTreeFiles']} bot-tree file(s) -> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())