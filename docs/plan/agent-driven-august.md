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

## Part A progress (all committed on fix/local-model-2026-11-16)

- **A.1 tool surface** — b8a064f6. Measured after (app estimator, scratch dir,
  ctx 8192): full 39 tools / ~10.6k fixed total (was 139 tools / ~26.9k —
  disclosure never activated); bare 18 tools / ~2,655 fixed total = **32% of
  8k ✓**; at 4k the micro set gives 14 tools / ~2,196 = 54% (target 35% at 4k
  NOT met — the bridges + memory CRUD + workspace minimum cost ~1.1k tokens;
  best number, reported honestly). Small-model default: the capability probe
  now suggests `bare` for tool-capable models with ≤8k windows. Per-skill-pack
  behavior comes from the BM25 preload: a research-flavored conversation
  preloads check_paper_citations/check_traceability/web tools (verified).
- **A.3 wake-up brief** — d5c039c0. Verified live: the cerebellum role
  currently resolves to **kilo-auto/free (KiloCode — CLOUD)**, so the brief
  DOES leave the machine today; the receipt + Settings now label it, and the
  new titlesOnly mode produces the digest-only brief (verified both paths).
- **A.4/A.5 release + crash** — 69695e95, verified live
  (docs/evidence/local-model-release-crash.txt): injected battery releases;
  a busy server is deferred with a visible event; a mid-run kill is marked
  FAILED with GPU (RTX 4050 Laptop) + driver (616.64) recorded and restarted
  once; the phantom-ready resurrect race is fixed.
- **A.6 output floor** — a2cc7108. 1024-token floor on all three provider
  paths for loopback models; the thinking-only rescue already existed.
- **A.7 provenance** — 11b1be1e, verified: chapter drafts get the sidecar
  automatically; non-drafts and non-paper workspaces do not.
- **A.9 fallback chain** — verified: `chat_chain` fleet default is blank
  (OFF); when armed, the done event carries usedFallback and the chat
  renders "answered via <model>".
- **d72fb870** — tool-result cap scales with the model window (the A.2 run
  caught a 12.6k-token request on the 8k model from one 64 KiB tool result).

### A.2 reliability run — FINAL (fallback OFF, LFM3-1.7B-Q8_0, bare profile, Vulkan)

**107 tool calls across 20 tasks (3 batches) — 105 ok (98.1%), 4 malformed, 0
unrepaired failures; 20/20 turns completed** (one early context-overflow turn
in batch 1 was the defect d72fb870 fixed; batches 2-3 ran clean). Evidence:
docs/evidence/a2-tool-reliability-tally.json. Malformed were arg-shape errors
on write_file/list_directory/read_file/retire_bot — each followed by a
successful retry (the schema-aware receipts doing their job). Bridge overhead
is the dominant pattern: tool_search 16 + tool_describe 15 + tool_call 17 of
107 calls (45%) — the bare surface pays a discovery round-trip for every
deferred tool. Bridged targets all worked, including the full bot lifecycle
(create_bot → list_bots → update_bot → retire_bot, receipts included) and
verify_citation/locate_claim/search_files.

### A.8 evidence (docs/evidence/)

The sample-paper flow ran on the 1.7B: paper-state + sources seeded → the
agent drafted Chapter 1 (paper-chapter1-draft.md) → the write_file hook wrote
the provenance sidecar automatically (paper-chapter1-provenance.md) → paper
state updated → the agent exported exports/a8-paper-20261116.docx via
export_paper_docx (paper-chapter1.docx) → rendered to paper-chapter1.pdf with
LibreOffice. Page screenshots of the PDF are still owed (needs a PDF
rasterizer or the desktop viewer). First-launch picker on a genuinely clean
profile: not yet run (needs the desktop shell — Part C).

## Spec-conformance changes (owner: "implement those", 2026-11-16)

All six Step-0 discrepancies are now resolved in code (one commit each):
- **Messaging direction** (4be18508): `message_agent` is offered ONLY in the
  orchestrator's main chat; bot chats and subagents get it dropped; the
  protocol text, tool description and prompt block rewritten; the reply wake
  in the main chat runs as the main agent. Verified: gate offers/drops per
  session kind, handler fails closed from a bot chat.
- **Guided setup deleted** (a4880936): setup.py, install_complete, the
  pending prompt section, the roster accessors, create_agent's
  setup_required field (OpenAPI + client regenerated). create_bot is the
  replacement — bots are born fully configured.
- **Rooms deleted** (0b9bd351): bot_mode/rooms.py (−673 lines incl. the dead
  frontend API client calling routes that no longer existed).
- **Board folded into Runs** (172c82ee): the kanban + team launcher + live
  run-sync render as a section of Runs; /board redirects to /runs; the
  separate page and its nav item are gone. Nav items: 9 → **8**.
- **Live deleted** (172c82ee): the hidden route, all seven surface files and
  the lazy import; /live redirects to chat.
- **Cerebellum cloud default**: left as configured — the brief now labels it
  in Settings + the receipt and offers titles-only; switching the install's
  fleet binding is the owner's config choice, not a code change.

## Part B inventory (static pass — live click-through is Part C, not yet run)

Screens (SECTION_ROUTES, `frontend/desktop/src/routes.ts`): Chat `/`+`/c/:id`,
Automations, Runs (with the folded Board section), Charts, Artifacts,
History, Learning, Docs — **8 nav items**. Settings renders as a DIALOG over
the live chat (not a route swap). The Live surface is deleted (its deep link
redirects to chat), and the Board page is folded into Runs.

Settings: 51 registry ids across ~30 section files — far more than the
minimal surface the brief wants; defaults review belongs to Part C/D.

Chat screen visible by default (titlebar + sidebar + composer): show-sidebar,
session menu, search-or-run, canvas, theme toggle, window buttons
(minimize/maximize/close), new chat, session list, Bots rail, composer with
model picker + mentions + attachments + send. ~15 controls before any
conversation content — the audit's minimalism target needs this counted
exactly in the live app (Part C).

Verdicts (major surfaces, static):
- Chat thread + composer: KEEP (the one surface).
- BotsRail + BotDetail + BotReceiptChip: KEEP (minimal bot UI per spec).
- Automations page: KEEP (routines stay), but creation should be agent-driven
  from chat — verify in Part C whether a manual create form exists (GAP/
  AGENT-REPLACEABLE candidate).
- Runs page: KEEP (absorbs Board per spec — the separate Board page is the
  drift).
- History/Archive: KEEP (user owns the work; view/undo).
- Charts/Artifacts: KEEP as views of agent-made artifacts; any manual editor
  forms are AGENT-REPLACEABLE candidates (parity proof required first).
- Learning hub: KEEP (view) — agent writes via skill/memory doors.
- Docs: KEEP (reads the validated docs/state map).
- Live surface: removal candidate (spec says deleted) — owner approval first.
- Right drawer (files/jobs/plan/notes/subagents/diff/browser/canvas/circuit/
  preview sections): these are VIEW surfaces for agent work — KEEP as views;
  each needs the Part C check "does the agent surface this where the user
  needs it".
- Model picker in composer: KEEP (user owns model choice).
- Onboarding (ProviderOnboardingModal): KEEP; default model prefers local
  (already shipped in 9bf5b30d/4a24cd4b).

GAP checks to run live (Part C): start a paper project → sources → draft →
traceability → export by chat alone; create/edit/retire a bot by chat with
the receipt; subagent fan-out with inline progress; teach a recurring
workflow by chat; change a memory fact / setting / skill by chat with undo;
wake-up brief on resume + dismiss + quiet mode; every risky action shows a
card and cannot proceed without it.


- Evidence folder: `docs/evidence/` (screenshots, probe outputs). Scratch
  probes stay in the Hermes scratch dir, never committed.
- Local model for tests: LFM3-1.7B-Q8_0 at
  `C:/Users/rober/.lmstudio/models/LiquidAI/LFM3-1.7B-GGUF/LFM3-1.7B-Q8_0.gguf`,
  served by the repo's Vulkan llama.cpp (`data/runtimes/llama.cpp/vulkan/llama-server.exe`).
- Every commit: small, reversible, gates green, staged by path.

## Live audit (Parts B/C — in progress, dev server + real backend)

Verified live this session:
- **All 8 routes render** (/, /automations, /runs, /charts, /artifacts,
  /history, /learning, /docs, /settings/general) — the first 2.5s reads that
  looked empty were lazy-chunk latency; at 5s every surface renders.
- **Redirects**: /board → /runs ✓; /live → chat ✓.
- **Board fold**: /runs shows `board-section` with the team-launcher toggle ✓.
- **Settings**: the wake-up caption renders verbatim — "Brief written by
  kilo-auto/free · a cloud model (KiloCode) — the digest leaves this machine." ✓
- **Bot lifecycle BY CHAT on the local 1.7B**: "Create a bot named Auditor…"
  → turn ran (the released local model auto-reloaded first — A.4's reload
  path worked in production) → receipt card "Bot ready — Auditor · audit
  checker — Retire this bot" ✓; "Retire the bot named Auditor…" →
  bot_retired receipt + list_bots confirmation (backend transcript verified;
  roster shows Auditor gone) ✓.
- **Memory by chat** → the memory card chip renders ✓.

Audit findings:
- The composer still has a mic button (`composer-voice-button`, "Voice
  input") — that is dictation input, not the deleted Live mode; verify it
  works in the desktop shell (owed).
- At a 762px-wide window the sidebar's bot rows were not reachable for the
  detail-edit check — small-window behavior needs a full-size pass (owed).

Still owed for STOP 1: subagent fanout inline progress, wake-up brief on
resume + dismiss + quiet mode, risky-action confirmation cards, the ×3
repetitions the brief asks for, themes/keyboard/resize/empty-error sweeps,
and the bot detail edit at full window size.

## STOP 1 status (interim — Part A complete, Parts B/C partially done)

Done and verified this session:
- Part A items 1, 3, 4, 5, 6, 7, 9 implemented + verified (commits b8a064f6,
  d5c039c0, 69695e95, a2cc7108, 11b1be1e, d72fb870); A.2 reliability run
  completed (107 calls, 98.1% ok); A.8 paper-flow evidence captured.
- Gates green after every commit (full ladder re-run at the final state).

Open / owed:
- A.8: PDF page screenshots; first-launch picker on a genuinely clean profile
  (desktop shell needed).
- Part B: live agent-driven checks (the GAP list above) — in progress.
- Part C: live click-through of every inventory item, states, themes,
  keyboard, resize — in progress.
- The six spec-vs-code discrepancies are IMPLEMENTED (see the
  spec-conformance section); the cerebellum cloud binding remains a config
  choice for the owner (labeled + titles-only available).

Next: finish the live audit, then STOP 1.
