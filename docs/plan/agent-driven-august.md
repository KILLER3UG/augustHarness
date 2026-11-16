# Agent-driven August — working plan and status

Owner brief: finish the build, then audit and test the UI for "agent-driven"
(Parts A–E, stops 1–3). This file is the running status record required by the
brief: updated at each stop, with open items and file references. Scratch
probes live outside the repo and are never committed.

## Status (Step 0 — orientation, 2026-11-16)

- Branch: `fix/local-model-2026-11-16` (continuing it; created from
  `review/harness-2026-11-16`). HEAD at orientation: `cb928d31`.
- Tree: clean except `docs/research/august-harness-review/` (a previous
  research-only review, untracked, left in place — it is the reviewer's
  material, not mine to delete without a ruling).
- `git rev-list --count origin/review/harness-2026-11-16..HEAD` = 0: origin is
  AT cb928d31. The "34 commits ahead" figure from the earlier report is stale —
  the branch has since been pushed. Nothing to reconcile; I continue on top of
  cb928d31 and do not touch `review/harness-2026-11-16`.
- Gates: `npm run gates:all` — **all four groups passed** at cb928d31
  (static incl. check:skills 14 bundled skills, backend ruff+mypy, frontend
  tsc+eslint+check:design, mobile typecheck).

### Claim re-verification (previous agent's numbers, unconfirmed)

| Claim | Verdict | Evidence |
|---|---|---|
| Full tool surface ≈ 48,541 prompt tokens | **Plausible, structure confirmed** | My offline probe (app's own estimator, empty brain, no workspace): full = 139 tools offered, ~24.2k est tool tokens + ~2.6k est system prompt. The estimator under-counts JSON schemas ~2×, so a real-tokenizer count in the high-30k/40k range is consistent. Exact reproduction of 48,541 not possible (no measurement script was committed). |
| "reduced" 47,125 — barely trims | **Confirmed, and it is a defect** | `reduced` only drops `_HEAVY_TOOL_PREFIXES` (web_/browser/voice/notion/slack/discord/search/fetch): 139 → 125 tools, −7%. A "small model" profile that keeps 125 tools is not doing its job. |
| "bare" 6,623 — fits 8k, not 4k | **Confirmed** | Probe: bare = 16 tools, ~4.1k est tool tokens + ~2.15k est system prompt ≈ 6.3k; matches 6,623 within estimator bias. |
| 31 tool calls, 29 ok on the 1.7B | Not re-verified yet | Historical run; Part A.2 re-measures with ≥100 calls, fallback OFF. |
| CUDA llama.cpp crashed here; Vulkan stable | **Plausible, unproven** | Both `cuda-13.4/` and `vulkan/` exist under the desktop app's `runtimes/llama.cpp/`; the repo data dir has only `vulkan/`. `resolve_variant` still prefers CUDA on NVIDIA (`llama_runtime.py:226`) — A.5 addresses default + crash fallback. |
| Crashed server leaves a stale "ready" row | **Confirmed in code** | `_await_server_ready` marks `failed` only while STARTING (`llama_runtime.py:548-577`). After `ready`, nothing re-checks liveness: `list_servers()` returns persisted rows; `sweep_orphans` runs at boot only. A mid-generation crash leaves `ready`. |
| Reasoning models return empty content under small budgets | To reproduce in A.2/A.6 | LFM3-1.7B is served with `maxOutputTokens: 2048` (providers.json); the floor enforcement is missing (see A.6). |

### Bot spec vs code (Step 0 item 3)

| Spec item | State |
|---|---|
| create/update/list/retire bot tools | **Holds** — `tool_registrations/bot_tools.py`, permission ceiling via `_ceiling_for` (asked ∩ creator surface, drops named). |
| Receipt card with edit and undo | **Holds** — `create_bot` returns a `bot_created` receipt; `BotReceiptChip.tsx` renders it collapsed with dropped-permissions named and a Retire (= undo) button; edit lives in `BotsRail` detail (title save). |
| Minimal bot UI | **Holds** — `BotsRail.tsx` (compact list + detail); create modal, rooms UI, row menus deleted; creation is "ask the agent" composer seed. |
| Orchestrator-to-bot messaging | **Partial** — the UI door exists (`POST /api/agents/bots/{id}/dm`, `routers/agents.py:203`), but the ORCHESTRATOR'S chat has NO messaging tool. |
| Bot-to-bot is OFF | **VIOLATED** — `message_agent` (`bot_mode/dm.py`) is offered exactly in canonical Bot Chats (`filter_dm_tools`, `workbench.py:1758`), i.e. bot→bot. Protocol text (`bot_mode/protocol.py` MESSAGING_PROTOCOL) documents bot-to-bot as the design. This contradicts the owner's decision "orchestrator-to-bot only; bot-to-bot is off". Needs a ruling-flavoured fix: move the DM door to the orchestrator chat (target = bots), remove bot→bot. Flagged, not silently chosen. |
| Rooms deleted | **Mostly** — no room routes in `routers/agents.py`, no room UI. `bot_mode/rooms.py` (597 lines) survives ONLY as the cascade-purge store (`agent_roster.py:421`). Dead weight; deletion candidate at Part D with owner approval. |
| setup.py deleted | **VIOLATED** — `bot_mode/setup.py` is still registered (`tool_registrations/__init__.py:91`) and filtered in (`workbench.py:1765`); `install_complete` is reachable while a bot's setup is pending. Contradicts the decision. Flagged. |
| No dangling imports / prompt text | Rooms: imports remain (purge path) but no routes/UI. Prompt text: MESSAGING_PROTOCOL still teaches bot-to-bot. |

## Part A plan (local-model fixes) — in progress

Measured baseline (offline probe, app estimator, scratch data dir, ctx 8192,
empty memory, no workspace files):

| profile | tools offered | tool tokens (est) | system prompt (est) | fixed total (est) | % of 8k |
|---|---|---|---|---|---|
| full | 139 | 24,240 | 2,638 | 26,878 | 328% |
| reduced | 125 | 22,500 | 2,562 | 25,062 | 306% |
| bare | 16 | 4,129 | 2,150 | 6,279 | 77% |

Target: fixed overhead ≤ ~35% of context at 4k AND 8k with the research
workflow intact → ≤ ~1.4k tokens at 4k, ≤ ~2.9k at 8k.

A.1 findings so far:
- Lazy loading EXISTS (`assembleToolDefs` + `tool_search/describe/call`
  bridges, `HARNESS_CORE_TOOLS` = 36) but **never activates in production**:
  `_sessionToolDefs` (`workbench.py:1683-1724`) resolves the threshold's
  context window from `as_dict(session.provider)` — and `session.provider` is
  a NAME string (`sessions.py:53`), so the window falls back to
  DEFAULT_CONTEXT_WINDOW (1M), the 10% threshold becomes 100k tokens, and the
  full 139-tool surface ships to every model, including a 1.7B at 8k. Probe:
  provider-as-string → 139 tools; provider-as-dict → 39 tools (disclosure
  active). The fix is to resolve the real window from the providers store.
- After disclosure activates, the floor is still ~39 tools / ~8.3k est tokens
  (36 core + 3 bridge + min-3 preloaded; the trim loop stops at 3 preloaded),
  plus a ~2.6k system prompt — still >35% at 8k. So A.1 also needs: a
  small-model system prompt variant and leaner schemas for the small-model
  surface (the bare allowlist + a research-tool subset per skill pack).

Open items for Part A (ordered):
1. A.1 tool surface: activate disclosure for real windows; small-model prompt;
   per-skill-pack tool sets; re-measure at 4k/8k.
2. A.2 reliability run (≥100 calls, fallback OFF, research tools, LFM3-1.7B).
3. A.3 wake-up brief: label the target model (local vs cloud) in setup +
   Settings; add the "titles only" option.
4. A.4 release policy: never release during an active turn; memory-pressure
   wait + visible message; post-release reload shows "loading model".
5. A.5 crash robustness: liveness re-check (mark failed, never stale-ready),
   one restart, auto-fallback to the stable variant, record GPU/driver.
6. A.6 reasoning output floor ≥1024 for reasoning-style local models +
   thinking-only retry/message.
7. A.7 provenance sidecar auto-write in drafting tools (tested on the 1.7B).
8. A.8 evidence: sample paper flow + docx→PDF screenshots in docs/evidence/;
   clean-profile first-launch picker.
9. A.9 fallback chain stays off by default; answer attribution in UI.

## Evidence and conventions

- Evidence folder: `docs/evidence/` (screenshots, probe outputs). Scratch
  probes stay in the Hermes scratch dir, never committed.
- Local model for tests: LFM3-1.7B-Q8_0 at
  `C:/Users/rober/.lmstudio/models/LiquidAI/LFM3-1.7B-GGUF/LFM3-1.7B-Q8_0.gguf`,
  served by the repo's Vulkan llama.cpp (`data/runtimes/llama.cpp/vulkan/llama-server.exe`).
- Every commit: small, reversible, gates green, staged by path.
