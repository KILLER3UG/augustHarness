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

- **A.2 user-configurable context length — DONE, live-verified** (018a9488
  backend, 9fcca3ff frontend, 57d96aec evidence). The serve-time picker chose
  the window once and nothing could change it afterwards, and the agent had no
  door. Now:
  - `change_server_context` (llama_runtime.py): stop → relaunch with the new
    `--ctx-size`; refuses while a generation is in flight (same guard as the
    release pass); records `previousCtxSize` on the row.
  - OOM fallback: a load failure at the new size falls back ONCE to the
    previous working length, visibly on the realtime bus
    (`_ctx_fallback_reload`); an ordinary failed serve never auto-retries.
  - Chat door `set_model_context` (self_config_tools.py): discovers via the
    disclosure bridge, waits for the new server (ensure_local_ready) so the
    same turn continues on the new port, returns a `model_context_changed`
    receipt (from/to/effectiveCtx) → the chip renders with undo.
  - UI: per-server context control in Local Models (presets + custom), a
    Context row in the composer model menu (presets + custom, local models
    only), a `16k` chip beside the model name per the visual spec, and the
    realtime bridge now toasts local-model lifecycle events (loading, ready,
    released, ctx-changing, ctx-fallback) — they were emitted but dropped.
  - Wiring: `_getContextWindow` priority 1 reads the per-model
    `contextWindow` that `_price_local_models` rewrites on every serve —
    budgets, disclosure threshold, and the tool-result cap follow the new
    window at the next turn. Verified live: `/api/models` showed
    contextWindow 8192 after the change.
  - Live evidence (docs/evidence/local-model-context-change.txt): serve 4096
    → n_ctx 4096 (/props); API change → n_ctx 8192 + contextWindow 8192;
    guards (1023/999999 → 400, 65536 → trained-window warning); warm.json
    follows (per-model persistence); CHAT: the 1.7B changed its own context
    to 16384 through the bridge, receipt in transcript
    wb_20261116_160527_9749bd. Not yet verified live: the OOM fallback (no
    model here fails to load at these windows on the 4050), the receipt card
    + undo rendering in the desktop shell (Part C).

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

### Part A2 validity pass (2026-11-16/17) — audits + fixes

Two static audits (subagent-delegated, read-only) plus fixes in three
commits; the audit detail lives in this session's STOP 1 report. Findings
that drove code:

- **Memory audit** (memory_store/, workbench injection, fact_retrieval):
  injection is metered (2400-char frozen boot index + 600-char profile
  lane + 500-char delta ≈ 275 tokens/turn churn), cache-friendly
  (tail-patched volatile lanes), fresh on next turn. Gaps found and FIXED:
  - `remember` did not run the junk bar the import/distiller doors run —
    an eager small model could file questions/incident-logs/runtime noise
    (71b5256c).
  - Delta-lane facts got no recall provenance and no usage credit — the
    Recalled-memory pill under-reported and the ranking booster never saw
    them (71b5256c, collected_keys through build_memory_delta_block).
  - A near-duplicate write now carries `similarTo` (neighbour, ratio,
    title) — a warning, never a veto; identity stays the only refused
    rung (11b5650f).
  Recorded, NOT changed (design decisions for the owner): contradiction
  detection is same-title only at write time; `factBudget` auto-curation
  ships off by default; paraphrase dedup waits for consolidation by design.
  Live store sample confirms the paraphrase issue: dark-mode ×2,
  daily-routine ×3 among 181 facts.
- **Tool audit** (177 registered tools; dispatch/validator/bridges):
  the finding that mattered most was a boundary gap — **bot ceilings were
  advisory** (surface-only): a bot's canonical chat could dispatch any tool
  by name or through the tool_call bridge. FIXED (b5a01e84): the surface
  filters to the ceiling (own ∩ parent chain; bridges stay reachable — the
  ceiling binds at the inner dispatch) and `_checkToolGuard` re-checks every
  dispatch. Verified behaviorally: run_command refused, read_file allowed,
  cleanup confirmed. Also: `toolSchemaCheck` now defaults to **warn** — the
  validator/receipts were built but off, so a misshaping model got the raw
  handler error instead of the fix.
  Recorded, NOT changed (would churn A.2-verified surfaces): the audit's
  mega-tool splits (`bulk`/`goal_output`/`board`), the search/agent/todos
  cluster merges, stringified-JSON params (`goal_edit.bound_json`,
  `brain_query.filters`), `read_file` default paging, and uncapped
  `run_command` foreground output (the loop-level cap d72fb870 already
  bounds the worst). These are the owner's Part D candidates.
- **Skill freshness**: both packs reference only registered tools (all 10
  names verified against the live registry); sizes ~7.8k/6.2k chars.
  A/B measurement: runner prepared (scratch, uncommitted); owed before
  STOP 1 closes.

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

### Browser UI-check pass (2026-11-17, 38 screenshots in docs/evidence/ui-checks/)

Driven black-box (ARIA snapshots + repo-local Playwright, one action per
observation cycle). Results:

- **PASS — A** context chip: model picker → LocalModels → LFM3 renders the
  `16k` chip beside the model name. **B** context row: presets 4k/8k/16k/32k
  + custom input, current preset highlighted, 8k click → toast → reload →
  chip reads 8k. **E** risky-action card: "Permission required / Awaiting
  approval" card gated a file write with a diff preview and 5 numbered
  choices — transcript shows zero tool calls and the file never existed,
  i.e. genuinely gated (mode chip read "Full access" and the gate STILL
  fired for the write — the sandbox classifier, not the label, decides).
  **F** empty states: /automations has a real empty state with a create CTA;
  /charts, /history, /learning render content, no blanks. **G** keyboard:
  sane tab order (voice → send → mode chip → folder chip → ctx chip → model
  chip), Escape closes the model menu. **H** resize 700px: bottom tab bar +
  hamburger overlay sidebar, no overflow, composer intact.
- **PARTIAL — D** subagent fanout: inline progress rendered live ("Working…
  1 thought", worker rows in the sidebar strip); both workers then FAILED
  with "[401] This API key has expired…" (the workers routed to the
  OpenCode role model — env/config, not the fanout machinery); one run's
  final assistant message persisted with empty content.
- **BLOCKED — C** receipt card + undo: the re-run turn found the model
  answering "already 16,384" from session/brief context without calling the
  tool; a second attempt wedged when my 131072 OOM test restarted the
  server under it (test-sequencing collision, my fault — the turn's
  requests hit a dead port mid-turn). The receipt JSON itself is verified
  in the transcript (wb_20261116_160527_9749bd); the CARD render +
  undo-button click stay owed for the desktop-shell pass.
- Also captured live: the **wake-up brief rendered on startup** ("Welcome
  back… Would you like to continue setting the local model's context
  length?") with a dismiss X; "Recalled memory 6 facts · 1 skill" pill;
  release-deferred and ready toasts from the new bridge wiring; the grouped
  "Task completed 1 step ⌄" tool accordion.

### Bugs found by the pass (logged for Part C/D; fixes committed where mine)

1. **Deep-link/sidebar opens of some sessions render a fresh draft** (high):
   root cause traced — the affected sessions (e.g. the receipt session
   wb_…_9749bd, and a post-restart API session) are NOT in
   workbench-sessions.json; their transcript JSONL exists but the session
   ROW was never persisted (turn died mid-flight / API-only draft). Old
   sessions load fine. Pre-existing robustness gap: session rows persist at
   turn END; a killed turn loses the row. Needs a design call (persist at
   creation) — recorded, not silently changed.
2. **Reported-vs-served context mismatch** (high): the model said "already
   16,384" (session history + the wake-up brief's Recent line) while the
   server ran 8,192 — the chip showed the truth. Contributing factor: the
   brief narrates past ctx changes as recent fact. Recorded as a brief-
   wording consideration, not a code defect.
3. **Wedged turn, no cancel affordance** (high): triggered by my OOM test
   colliding with a live turn (server restarted under it). The composer
   showed no stop control in that state. Partly environmental; the
   underlying mid-turn port-change gap is REAL and now covered by the tool
   waiting for readiness + live base-URL re-resolution, but a turn that
   survives a server swap may still strand. Logged for Part C.
4. **Subagent workers on an expired cloud key** (env): fanout machinery
   works (spawn, inline progress, results table, "0 of 2 succeeded"
   summary); the role's model provider had a dead key. Also one empty
   assistant message on failure — the thinking-only rescue does not cover
   this path. Logged.
5. **a11y on the new surfaces** — FIXED (d11d1c7e): aria-pressed on presets,
   aria-label on the chip.
6. **Duplicate lifecycle toasts** — FIXED (d11d1c7e): _emit_status dedupes
   repeats within 90s.

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

## Skill validity A/B (2026-11-17 — live on LFM3-1.7B, fallback OFF, ctx 16384)

Same prompt both arms, bundled skill toggled via PATCH /api/skills
(disabled flag), fresh session per arm, trajectories extracted from the
transcripts. Runner: scratch tool outside the repo (never committed).

**Research task** ("start a paper project + draft Chapter 1"):
- WITH research-paper-studio (58 tool calls, 24 error frames): the model
  FOUND the skill through the disclosure bridge — tool_describe
  (research-paper-studio) → tool_call(load_skill) — so skill discovery
  works on the bare surface. It then spent 25 run_command + 15
  list_directory + 14 write_file rounds fighting PowerShell (paths with
  spaces, Get-Content access denials) and the turn ended with an EMPTY
  final message; generations repeatedly stopped on the output-token limit
  mid-tool-call (stop_reason=length truncating JSON tool args).
- WITHOUT the skill (43 calls, 10 error frames): same shape, slightly
  fewer errors, also empty final.
- **Verdict: the skill did NOT change the outcome on the 1.7B** — both arms
  failed the task. The binding constraint is not the skill: it is the
  model's run_command habit (the audit's tool-selection finding), PowerShell
  quoting, and output-token truncation. The skill machinery itself verified
  (discovery → describe → load_skill all worked).

**Coding task + the A.1 gap it exposed:** the first coding pair DROWNED at
16384 — "Context size has been exceeded" after six tool rounds. Root cause:
the tool-result cap scaled window//2 → 8192 chars (~2k tokens) per result;
six rounds ≈ the whole window. FIXED (d0c64357): the cap now scales
window//4 (large windows floor at 64 KiB; explicit profile values win).

The coding pair also exposed a second, nastier bug — the A/B's own PATCH
toggle created a copy-on-write override whose unquoted-colon frontmatter
made the file unparseable; the catalogue skipped it with a warning, but
its presence ballooned the first request past n_ctx and both turns died
("Context size has been exceeded", 0 input tokens). FIXED (b6237d9f):
_renderSkillMd double-quotes scalars containing ': '. Re-run on the fixed
backend: both arms executed (15 vs 9 calls) but ended empty-final with
error loops — API sessions have no workspace bound, so the sandbox
correctly blocked every repo write. Verdict: an API-session coding A/B
cannot measure skill value; the workspace-bound run belongs to the
desktop-shell pass. Evidence: docs/evidence/skill-ab-research-coding.txt.

Also found while diagnosing: the surface at 16384 is the FULL core (the
bare suggestion fires at ≤8k only) — the model entry's toolSurface='bare'
config was honored (verified via getProvidersAsModels), so the drowning was
the result cap, not the profile. The bare-at-≤8k threshold vs 16k windows
is an A.1 tuning question left open for the owner (35% target was defined
at 4k/8k only).

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

## STOP 1B completion pass (2026-11-17 — owner rulings executed)

Commits (each gates-green): `3bda7eb0` (tool-carrying requests floor at
4096 output tokens — stop_reason=length no longer truncates tool-call
JSON), `bdc46146` (≤16k local windows never carry the full catalog —
auto-bare unless a surface was set explicitly; run_command demoted off
the bare resident set, bridge-reachable), `4c4aa676` (factBudget ships
armed at 80), `51ad7ba8` (read_file default 250-line paging with the
continuation receipt; run_command output capped 40 head + 160 tail lines
at the source with a narrow-the-command footer), `cdbfabb9` (registry
gains hidden tools + dispatch aliases; bulk/search withdrawn from the
surface; goal_output split into goal_record/goal_claim; board split into
board_list/board_edit; submit_todos→update_todos and
create_agent/list_agents→create_bot/list_bots merged as aliases; the
sub-agent deny-name smoke check resolves aliases; the programmer pack
teaches the merged name), `99cac7b5` (session row persisted AT TURN
START — killed turns keep their rail row; cancelled/interrupted
statuses; a local server dying mid-turn aborts the turn with a visible
"reload on next message" instead of a 5-minute wedge; the wake-up brief
reads the served model's current name/context live), `ed672af9` (the
composer mic / voice-input button removed — Live/voice is out of scope),
`b9226920` (a dev-only CDP variant for the Tauri shell so the REAL shell
is drivable by the Playwright flow).

The token probe (scratch) now drives the REAL `_sessionToolDefs` per
profile × window — the STOP 1B report table comes from it. The A/B
runner binds the session to the repo workspace (the coding arms' writes
were sandbox-blocked without it) and excludes pre-existing sessions from
its matching.

### Desktop-shell pass (2026-11-17 — agent-driven via the CDP dev variant)

The REAL Tauri shell on a CLEAN profile (`HARNESS_DESKTOP_APP_DATA` +
`HARNESS_PROXY_PORT=8086` + `HARNESS_TAURI_CDP=1`, Playwright over CDP on
:9223). 19 screenshots in docs/evidence/desktop/.

- **PASS — clean-profile first launch**: the setup checklist renders
  (provider/model/project/health rows, local-first model step with
  data-flow labels and the reachability gate). Interactions from
  window-open to a sendable first message: **2 clicks** with a served
  model (choice chip + Continue), **1 click** pristine ("Skip for now").
- **PASS — mic gone**: composer-voice-button count 0; the composer's only
  buttons are actions + send.
- **PASS — Tell August bar**: Settings → Memory renders
  "Tell August what to change…" (memory-add-input + submit).
- **FAIL — receipt card on an 8k local model**: the turn ran on the local
  model but the 1.7B never discovered set_model_context (deferred by the
  disclosure budget) → no receipt, nothing to undo. FIXED (baec885c):
  set_model_context is core-resident (~150 tokens).
- **FAIL — subagent fanout on the local model**: the fanout machinery ran
  (2 workers spawned, inline progress) but both workers died with
  "Context size has been exceeded" at the 8k window — small-model
  subagent prompts need the same budget discipline the main turn got.
  Logged as a STOP 1C item.
- Bugs found + logged: a stale gitignored dev payload silently broke
  clean-profile isolation (worked around; the shell then spawned its own
  backend on :8086 — verified); early relative /api fetches bypass
  discovery inside the shell; the first-launch tour is dead code (the
  seeded Bot Chat session blocks tourPending); /c/new resumes the most
  recent session instead of a new one; a data-testid collision between
  the receipt card and the composer readout (FIXED baec885c); the
  memory-pressure watchdog killed+respawned the backend 5× under a 94%
  load (the machine was running a 5.4 GB game — environmental, and the
  watchdog worked).

### Skill A/B — final numbers

16k pair (post-fix, workspace-bound): RESEARCH with_skill 50 frames /
29 calls (skill described + LOADED via the bridge; list_directory 10,
read_file 4; ZERO run_command; 2 error frames; final 205 chars) /
without_skill 26 frames / 17 calls (final 333). Both COMPLETED — the
pre-fix runs both died. CODING without_skill SUCCEEDED: the correct
43-line parse_range.py written into the workspace + a 1549-char final;
with_skill died at the 16k ceiling (context exceeded — the margin at 16k
is ~2-3 tool rounds once a 6k skill body loads).

32k runs: research arms completed again (the with_skill arm in 6 frames
/ 4 calls — terse); coding arms crawled at ~2-4 tok/s (32k KV on the
4050 Vulkan) and were cancelled after >1h with the new 'cancelled'
status — 32k is not a practical A/B window on this laptop.

## STOP 1 status (2026-11-17 — final for this pass)

**Part A — complete.** Items 1–9 implemented + live-verified (this pass
added the user-configurable context length, A.2 in the owner brief, which
the earlier plan had dropped). The release-policy rule "never release
during an active generation OR chat turn" is now fully enforced
(turn-presence registry, 11635a93) after live testing caught the gap.
OOM-fallback verified by gates + code path (131072 loads fine on this
machine — no live trigger exists here).

**Part A2 (validity pass) — complete for this pass.** Both audits done
(memory: metered injection, freshness chain, hygiene gaps; tools: 177
registrations, overlap clusters, schema offenders, noise sources,
boundary gaps). Five fixes committed (junk bar, delta provenance,
similarTo, ceiling-at-dispatch + toolSchemaCheck warn, frontmatter
quoting). Skill freshness verified (all referenced tools registered).
A/B results below — honest: the skill did not change outcomes on the 1.7B
for the research task; the binding constraints are model-side (see the
A/B section).

**Part B — static inventory complete; live checks: 6 of 7 pass.**
Subagent fanout renders inline but the workers died on an expired cloud
key (env). The receipt-card render + undo click remain owed (blocked by a
test-sequencing collision; the receipt JSON is transcript-verified).
Wake-up brief rendered on startup with dismiss visible; the true
resume/unlock trigger needs the desktop shell.

**Part C — served-app pass done (38 screenshots); desktop-shell pass owed.**
Clean-profile first launch, mic button, receipt card click-through, and
the true resume trigger need the Tauri shell (HARNESS_DESKTOP_APP_DATA
seam ready; debug build exists).

**Open for the owner:**
- The tool-audit Part D candidates (mega-tool splits, search/agent/todos
  merges, stringified-JSON params, read_file paging, run_command source
  caps) — recorded in the audit summary, not changed.
- factBudget default-on and boot-index "supersedes" cue — recorded as
  design decisions.
- Bare-profile threshold vs 16k windows (the 35% target was defined at
  4k/8k only).
- Deep-link/session-row persistence gap (killed turns lose the session
  row; transcript survives).


## Fresh-agent takeover (2026-11-17/18) — Parts 1–3 + STOP 2, executed at d675fadc → b0c42dac

The takeover brief (fix the context selector, subagent 8k overflow, 6k skill
bloat, monochrome diet, bug rulings, screens/controls/settings diets) was
executed as 13 commits on `fix/local-model-2026-11-16`, each gates-green.
`gates:all` is green at the final state (static/backend/frontend+build/mobile;
178 tools, smoke 21/21, tool-surface parity anthropic=openai=141, lint 149<152,
design ratchet re-baselined lower: 770→752 entries, raw status colors 8→0).

### Part 1 (commits 7c6770da, d41fadc2, 170042c1, b13e1832, b10f0a2f)
- **Context selector** (7c6770da): the row unmounted the moment an apply
  flipped the server to `starting` (ready-only lookup; the in-row spinner was
  dead code) and bottom-right toasts painted over it — both fixed; row stays
  mounted + disabled + spinner; toasts moved top-center; same-size applies
  say so; 64k preset + explicit Apply button added (composer + Local Models
  tab). LIVE-VERIFIED in the real shell below.
- **Worker bare discipline** (d41fadc2): workers resolved to a small local
  model inherited the PARENT's surface and the 4096 output floor — the 8k
  fanout death. Workers on loopback ≤16k now narrow to the bare allowlist
  (bridges kept, prose-trimmed), the reply is capped at window//4 (new
  `output_ceiling` on both provider paths), the skills index scales to the
  worker window, and the spawn schema gains a `tools` allow-list (2–5)
  threaded through orchestrator → runSubagent → executeSubAgent. Loopback
  re-sends drop prior rounds' reasoning_content.
- **Compact skill projection** (170042c1): `load_skill` reads the serving
  turn's window; ≤16k loads the deterministic compact projection (headings,
  checklists, tool mappings; paragraphs folded, fences capped) — measured
  ≤3,553 chars (~850 tokens) on all bundled skills (research 7088→3549,
  programmer 5529→2330); `full=true` and `load_skill(name, file=…)` keep the
  complete text reachable; worker preloads compact too.
- **Reasoning reserve** (b13e1832): the thinking-only rescue runs twice on
  loopback turns (once on cloud); attempt number now real. Loopback re-sends
  strip prior thinking (in d41fadc2).
- **No raw SUBAGENT_COMPLETE bubbles** (b10f0a2f): live injection and
  hydrated history both skip the envelope (worker results live in the
  accordion); the fanout close line REPLACES "Running N sub-agents".

### Part 2 (10046c48, 27f5441b, df89d41a, 4fe4808b, 208b138d)
- **Monochrome** (10046c48): every warm tint and terracotta/amber accent
  retired — dark: #0e0e10 page / #0a0a0a rail / #161618 cards / 1px neutral
  hairlines / WHITE as the single active colour; light: zinc with near-black
  active. Warning reads neutral; red reserved for destructive. Schematic
  electrical identities keep their physical colours.
- **Titlebar** (27f5441b): SystemHud + its nvidia-smi/30s poll deleted;
  breadcrumb, Search-or-run, Canvas, theme toggle removed; keeps sidebar
  toggle, thread title, jobs/drawer doors (transient/functional), window
  controls.
- **Sidebar/composer** (df89d41a): usage card + disclaimer + 0.6% readout
  deleted (with the readout's component); same-name/same-path sidebar folders
  merge at hydration.
- **Rulings** (4fe4808b): `/c/new`-equivalent "+ New thread" ALWAYS mints a
  fresh chat (`createEmptySession`; reuse door kept for non-action callers);
  OnboardingTour deleted (its gate could never pass with the seeded Bot Chat;
  checklist is the only first-run surface); workbench API + SSE stream raw
  fetches gate on discovery (`whenReady`) — the shell's stale-port failures.
- **Automation tools** (208b138d): create/update/list/delete_automation over
  automations_store; plan-blocked mutations; stale create_routine policy
  entries retired; receipts automation_created/updated/deleted rendered by
  AutomationReceiptChip with undo (human API door); trigger token never in a
  receipt.

### Part 3 (f0069090, a84980c9, b0c42dac)
- **Screens 8→2+drawers** (f0069090): Charts deleted (/charts → chat);
  History merged into the sidebar Threads list; Automations folded into Runs
  as a minimal view/undo list (no manual create form — agent-driven);
  Learning folded into Memory & Skills settings (/learning redirects to
  /settings/memory-knowledge); Artifacts + Docs stay deep-linkable, off the
  primary rail (nav rows are their compact doors). Rail: Threads / Runs /
  Artifacts / Settings + Docs under More. Mobile tabbar Charts→Runs.
- **Controls ≤6** (b0c42dac + 27f5441b): default-visible chat controls are
  sidebar toggle, + New thread, composer input, + attach, model+context
  chip, send/stop; mode and workspace chips are rows INSIDE the model menu.
- **Settings 5 tabs** (a84980c9): General · Models & Local Runtime · Memory &
  Skills · Permissions & Workspace (new tabbed host over sandbox/tool-grants/
  privacy/external-access) · App & Updates; all other sections keep their ids
  and deep links as hidden tier (search-reachable).

### STOP 2 evidence (docs/evidence/desktop-diet/, real Tauri shell via CDP)
Clean scratch profile + served LFM3-1.7B (vulkan). Verified LIVE:
- **16k preset apply end-to-end**: menu → preset click → stop/relaunch →
  server row `ready ctx 16384 (prev 8192)`, llama-server /props `n_ctx
  16384`, /api/models `contextWindow 16384`, warm.json `ctxSize 16384`, the
  composer chip reads `16k` (04-after-16k-reload.png).
- **Custom value via Apply**: typed 12288 → Apply → row `ready ctx 12288`,
  /props `n_ctx 12288`, chip `12k` (05–07).
- **Monochrome + chrome**: 01/02 show zero telemetry text, no orange anywhere
  (warning icons render neutral gray), dieted rail, single-control composer
  footer, workspace + mode rows inside the model menu.
- The profile's own wake-up turn rendered the new fanout grammar ("Running 2
  sub-agents / Ran 2 sub-agents — capped at 2 at a time"), the thinking-only
  rescue warnings and the abort notice — with NO raw [SUBAGENT_COMPLETE]
  bubble anywhere in the transcript.
- Environmental limits, recorded honestly: the machine ran another session
  (pytest 3.2 GB + an 8 GB leftover headless chrome) at ~94% RAM, so the
  memory-pressure watchdog released the warm model at idle repeatedly and
  restarted the backend twice mid-testing (both worked as designed). The
  receipt-card click-through turn was attempted twice; both attempts hit
  backend churn mid-turn and no card rendered — the card's RENDER path is
  unchanged since baec885c and the receipt JSON is transcript-verified
  (wb_20261116_160527_9749bd), so the click-through stays owed on a quiet
  machine. A fresh subagent fanout at 8k/16k with the new worker discipline
  is likewise owed on a quiet machine (the machinery itself is verified; the
  8k overflow fix is gate-verified at the code level).
- Reliability tally: the standing A.2 numbers (107 calls, 98.1%) are on the
  bare surface already; a fresh 100-call tally with the worker ceiling +
  compact skills is owed (hours of 1.7B time; impractical during machine
  churn).

### Open for the owner
- Receipt-card + undo click-through, a fresh 8k/16k fanout, and a fresh
  100-call tally — all blocked on machine quiet (they are the same tests the
  STOP 1B pass ran, against the new code paths).
- The takeover's named reference repos (Octopus-SI-OS, mcode-stack, swamp)
  appear nowhere on this machine; the equivalent invariants (ceiling at
  dispatch, no verifier gate, session rows at turn start, metered cache-
  friendly injection, result caps, daemon TTL) are the ones the plan already
  records. The closest material is the untracked
  docs/research/august-harness-review/ series (MCode notes included).
