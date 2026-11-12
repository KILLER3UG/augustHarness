# Status — the current round

Last updated: 2026-11-13 · App version: **0.24.1**

This is the committed handoff. Read it before starting work; update it at the
end of a round. Keep it to one page: what shipped, what is next, what is
waiting on the owner, and how to resume.

## This round — the brain record leaves SQLite, and the shell goes quiet

- **The record is files.** `dataDir/memory/` — one markdown file per fact plus
  `MEMORY.md` — is the system of record; transcripts are
  `transcripts/<session>.jsonl`; ledgers are self-capping JSONL under
  `telemetry/`. `memory_conn.py`, `memory_schema.py`, 53 SQL migrations and 18
  one-off migration scripts are deleted (−12.6k lines); the
  `memory_store` package's public signatures are unchanged, so ~90 call-site
  importers needed nothing. A one-time importer
  (`app/services/retired_sqlite.py`, read-only) exports every table, recovers
  the owner's lost memories from `config.json`'s rollback log (19 facts; the
  7 pytest-probe watermarks are skipped), writes the marker, and renames the
  database to `august_brain.sqlite.retired` with its sidecars PAIRED
  (`.retired-wal` — a stranded WAL is unrecoverable). Ran on the live data
  2026-11-12: 21 sessions / 161 messages / 507,602 ledger rows exported.
- **`remember` works.** The read-before-write preflight is gone and
  auto-review vetoes only when it actually answers (unavailable/prose/timeout
  = a missing opinion, the write lands; update/merge/reject naming a real
  target still refuse). Measured before: 21 turns, `facts_injected` NULL in
  all 21, zero writes through the door. After one day on the new door: 181
  facts, all described.
- **Recall is pull-only.** The BM25 auto-tail is gone; a turn gets the frozen
  boot index (which now NAMES every fact a budget omits), the always-in
  profile/preference lane, and a delta of facts written since the session
  opened. BM25 survives as explicit `retrieve_relevant_facts` /
  `find_similar_facts`. Aliases answer the model's wording.
- **Transcript search is a derived index that cannot lie.**
  `state/transcript-index.sqlite` (FTS5, trigram) is rebuilt on boot,
  re-synced BEFORE every query, and hands back to the Python scan whenever it
  cannot answer — absent, corrupt, or a term shorter than the trigram window.
  The scan's own tie-break was backwards (oldest won); both paths now agree.
- **The shell opens instantly.** The full-screen bootstrap gate is gone:
  the workspace renders from frame one, a quiet `BackendStatusPill` reports
  the real install phases (and keeps the recovery actions), and sends made
  before readiness park in the offline queue and flush when `/api/health`
  answers. `BackendBootstrapGate.tsx` and `LaunchConversation.tsx` are
  deleted, not commented.
- **Updates are download-automatic, restart-explicit.** Detection starts the
  download in the background (no button); Settings shows progress, then
  "Restart to apply". "Not now" is durable per version — the startup path
  that cleared it is removed, so a declined update stays declined across
  restarts and the staged installer sits inert; `launch_installer_and_exit`
  is the only installer spawn, reachable only from the restart-now click.
  The rail's Update button opens Settings instead of installing.
- **The UI takes the Claude Desktop reference.** Warm near-black token family
  with a sparing terracotta accent (`#d97757`), centered title with dropdown
  chevron, a mono system HUD in the titlebar (fed by the existing health
  poll — no new timers; CPU/°C/FPS print N/A rather than invent a feed),
  composer pill, 70% user bubbles, collapsible rail sections, and the memory
  grammar: "Recalled memory ▾" / "Added to memory ▾" cards, "Thought for 3s"
  thinking headers.
- **The backup's narrowed scope is a recorded owner decision** (record only;
  transcripts/telemetry excluded — see `brain_backup.py` and
  [memory.md](agent/memory.md)).

## The 0.24.1 round: precomputed compaction, the Explore reference, no hostile env

- **A hostile `NO_PROXY` can no longer take a feature offline.** A bracketed
  IPv6 (`[::1]`, which Windows bypass lists write) makes httpx refuse to
  CONSTRUCT a client. `http_env` already existed for this; adoption was partial.
  The provider client, `web_fetch`, and 27 constructions across 14 more files
  (ComfyUI, Telegram, health probe, host agent, live speech, local-model probe,
  model discovery, quota fetch, service connections, skill packs, MCP, web
  search backends, providers discovery, What's New) now build through
  `http_env.client()` / the new `client_sync()`. Zero raw constructions remain.
  Verified with `NO_PROXY="... [::1] ..."`: the raw client raises, both doors
  build with `trust_env=False`, all 14 modules import.
- **A reasoning model's answer is not "empty".** `analyze_media` read only
  `content`; a reasoning model writes `reasoning` first, so a cap that ran out
  mid-chain left `content: null` and the tool said "(empty vision response)".
  It now falls back to `reasoning` and asks for 2048 tokens (measured: a
  1280×800 image used 933 completion tokens before any content). The refusal
  path already hands the provider's body back verbatim.
- **Memory and skills are told once per turn.** The relevant-skills block and the
  always-in memory lane dedup against each other inside a single build, and
  nothing is carried across turns — the transcript is the durable record, so a
  set keyed on "what this session was told" could only suppress bytes that are
  still in context. Both compaction paths clear the frozen indexes so the model
  is re-told exactly when its context was rebuilt. The boot index is scoped by
  `memoryIndexCategories` (default
  user,feedback,project,reference); `general` stays reachable through
  `list_facts(category=…)` and `brain_query`.
- **Compaction is precomputed (OpenCode's precompute shape).** When an LLM compactor is on and the session crosses 70% of its window, a background task learns the exact head chunk the compressor will summarise and writes the summary ahead of time, stored against the chunk's fingerprint. At the trigger a matching chunk is an instant swap (0 extra model calls, probed); any change misses and the real summarizer runs. Config:  (default on).
- **Compaction is precomputed (OpenCode's precompute shape).** When an LLM
  compactor is on and the session crosses 70% of its window, a background task
  learns the exact head chunk the compressor will summarise and writes the
  summary ahead of time, stored against the chunk's fingerprint. At the trigger
  a matching chunk is an instant swap — probed: 0 extra model calls — and any
  change misses and runs the real summarizer. Config: `compactionPrecompute`
  (default on).
- **A namespaced model id no longer disables the compactor.** The compactor
  client passed the resolved model id as a review-model hint, which is split at
  the first `/` and looked up as a provider ("nvidia/model" → provider
  "nvidia"). Every gateway id failed closed and `llm_compactor` silently fell
  back to the heuristic; naming the main provider explicitly keeps the id a
  model. Probed with the real compactor: precompute wrote the summary (one
  call), the following compaction used the cache (0.00 s, 0 extra calls).
- **Explore matches the reference capture.** The model list and the detail pane
  scroll independently (the section host had no bounded height, so the page
  scrolled as one); Download Options is a quant table (radio / GGUF / quant /
  Recommended / size) under a "Download to This device" header with the
  reference's loading copy; Details is a chip layout; rows carry an icon tile;
  the rail gains a labelled "Back to app" pill and a Local Models group. The
  reference's Billing / LM Link / Devices / Library / API / Defaults / Runtime
  rows are deliberately not invented — August does not ship those surfaces.
- **Verified:** full tool/skill sweep (170 registered, 85 real successes, 12/12
  skill bodies, real ngspice circuit loop driven by the model: skill → netlist
  → simulation → read-back), the ablation arms, and every gate group.

## The 0.23.0 round — a backend that heals, 1M context, the rail

- **August now recovers from its own dead backend.** Three defects made
  "Cannot reach August" unrecoverable: the update holdoff was a latch that
  suppressed every watchdog respawn (recovery was restarting August or the
  machine), the SPA cached the backend port across a respawn on 8086–8095, and
  the one command that clears the holdoff (`restart_proxy`) had no UI. The
  holdoff now expires 180 s after it is raised once no installer is running; a
  transport failure drops the cached base so the next request re-discovers; and
  the error card carries **Restart backend** (kill + relaunch over IPC, then
  retry), escalating to restarting August and only then the computer.
- **The context default is 1M, not 128k.** One `DEFAULT_CONTEXT_WINDOW` feeds
  the model config, provider create/update, config load, alias list, resolver,
  workbench budget, model editor and the frontend estimate. A stored
  `source='fetched'` 128k on an id no family heuristic recognises now falls
  through to the default — that number was August's old default, not the
  provider's word. Manual values, real non-default fetched values and every
  family (gpt-4o/deepseek 128k, claude 200k, gemini 1M) still win. Intended
  side effect: `assembleToolDefs` budgets the tool surface by the model window,
  so an unknown model now discloses 131 tools instead of 45 (prompt 8841 →
  10127 chars).
- **The rail matches the reference at this display's scaling.** The reference
  rail is 315 DEVICE px; this machine runs at 125% (AppliedDPI 120), so the
  CSS width is 315 / 1.25 = **252px**. The first attempt used 316 (device px
  read as CSS) and rendered 395 — wider than the image it was meant to match.
  13px nav/brand labels and 42px rows on a 46px pitch were already right. The
  width storage key is v4 so a saved width cannot pin an older default.
- **One thinking block is one thought.** The thought card made a row per
  paragraph, so a single continuous reasoning stream arrived as three rows with
  three headers and the block's one summary line never appeared. A block
  boundary is the semantic boundary (a new block follows a tool call or a new
  stream); one block now renders one row that expands onto its own text.
- **`analyze_media` hands the provider's refusal to the model verbatim.** The
  old result carried only an HTTP status plus `error.message` when the provider
  wrapped it in a dict — so a gateway's `{"detail": …}`, a bare `message`, or an
  HTML page reached the model as "(vision request failed: HTTP 400)" with no
  reason, and a 2xx with no text as "(empty vision response)". The turn then
  retried the same image, listed directories and tried a `file://` URL. The
  result now names the provider/model, states the status, and appends the body
  verbatim (capped at 4000 chars with the cut counted).
- **Landed from the parallel Perch session** (reviewed here; green on every
  gate): `session_search` (read-only transcript search, discovery/scroll/
  browse, parameterized SQL, in the planner and subagent read-only tiers);
  **skill support files** (`references/`, `templates/`, `scripts/`, `assets/`
  with relative-only + traversal + symlink containment, 25-file / 200k caps,
  the body's content scan, and rollback on a failed SKILL.md write); and a
  **verified skills `tar.gz` inside every brain backup**. The skills archive is
  an offline copy — restore still swaps only the database, and the UI now says
  so.
- **Verified:** all four gate groups locally (mypy 387 files, smoke 20/20 with
  170 tools and parity), CI green on the push. **Owed:** the human desktop run
  of the orca round and of this one — annotate→send, chord, degraded toast,
  quit copy, PNG export, theme repaint, Restart backend, and the new rail.

## The orca round — the review loop, honest glyphs, one tool pipeline

Six commits on `orca-ui-ux` implementing the cross-repo program from
`C:/Dev/_research/orca-deep-dive-2026-11-07.md` (hygiene → feedback loop →
structure → tool surface → display slice). A read-only merge review then fixed
the defects below; the branch was **not** merged until those landed.

- **The review loop exists.** Diff rows grow a hover "+" (the DiffView anchor
  system); notes live per-session device-local, one deterministic quote-safe
  formatter packages every unsent note into ONE queued message, and editing a
  note clears its sent flag. The drawer diff section carries the notes panel
  with jump-to-line and Send. Three defects the merge review caught are fixed:
  a draft is dropped when the session changes (it used to be filed under
  whichever session was live at save time), the notes prop has a stable empty
  identity (a parent re-render used to erase the first note mid-typing), and
  jump-to-line expands past `maxLines` before scrolling (it used to no-op past
  line 240).
- **Agent status is epistemic.** `AgentStateDot` maps every existing status
  vocabulary (tool calls, subagents, runs) onto one glyph set where `stalled`
  and `partial` render as the dashed missing-evidence ring — never as a check.
  Adopted on delegate rows, nested workers, board pills, queue receipts.
- **Mutating tools can carry `_expect`.** The executor measures the tool's own
  result and appends an `[receipt]` block — evidence, never a gate; the
  per-turn tally lands in `turn_outcomes` (`receipts_total/failed`, NULL when
  none ran). Nothing READS those columns yet: `learning_scheduler.py` never
  mentions receipts, so the "outcome signal" is persisted, not consumed. Wire
  a reader before calling it a signal.
- **One tool pipeline.** The two wire-format builders mirrored ~90 lines of
  session logic each; both now delegate to `_sessionToolDefs` (parity 45=45,
  prompt byte-stable). Dispatch learns `toolSchemaCheck: off|warn|strict`, and
  the key is in the brain-config allowlist, so `warn`/`strict` are actually
  reachable. `check:tool-schemas` reads `listTools()`'s wrapped
  `{type, function:{parameters}}` shape (it used to read the entry itself and
  always saw zero), has a registry-size floor, and reports an EMPTY allowlist:
  all 169 tools declare a `properties` object (21 are no-arg, `{}`).
- **Skills cannot name ghosts.** `check:skills` fails on an unregistered tool
  named in a skill body (9 initial hits were all prose + the deliberately
  unregistered `fpga_program`; the glossary documents each) and on an agent
  guide orphaned from AGENTS.md. Shadowing skill copies flag
  `bundledUpdateAvailable` — the flag compares frontmatter `version:`, which
  `_parseSkill` now surfaces (it previously dropped it, so the compare saw
  `None` on both sides and never fired) — and `POST
  /api/skills/{name}/adopt-bundled` catches them up (snapshotted — the adopt is
  an Undo). The API reports the flag; the desktop skills list does not render
  an affordance for it yet.
- **Structure and lifecycle.** The queue-vs-steer chord (Enter steers,
  ⌘/Ctrl+Enter queues-after-stop, the control previews while the modifier is
  held — scoped to the composer row, not window-global); `check:render-ratchet`
  budgets window keydowns and interval timers shrink-only (the keydown rule now
  matches global receivers only and the scan fails when it measures nothing);
  drawer tab panes crash alone (compact SectionBoundary; the single-file view
  and chooser are still unwrapped); `/api/health/detailed` gained a degraded
  toast on the same poll clock (the settings health pane already polled it);
  quit states the real consequence and focuses Cancel; backend teardown logs
  each step by name; the styleguide family lives in `docs/styleguide/` with two
  new `check:design` rules (copy-filler, copy-baked-keys, both skipping
  comment-only lines); oversized text previews gain Load-all-lines; the circuit
  drawer shows one display mode at a time with a real PNG export of the
  on-screen scene; docs diagrams follow the app theme and repaint on a theme
  flip.
- **Hygiene landed too:** the four diagnostics ledgers are wipeable and swept
  (`subagent_runs` reaches erase-memory), three past-window shims and the
  `_expect`-less stub definitions are gone, the `loop/` one-way import rule
  has a gate, and every CHANGELOG heading is dated (What's New no longer
  titles five rounds "Unreleased").
- **Deliberately skipped:** compact tool output (E6) — the spill-file previews
  + 64 KB transcript cap + BM25 tool-list disclosure already provide it.
- **Not yet verified in the product:** the human desktop run (annotate→send,
  chord preview, degraded toast, quit copy, PNG export, theme-flip repaint) is
  the remaining check; the gate set and smoke (20/20) are green on the branch.
- **Still open from the review:** nothing reads `receipts_total/failed`; the
  desktop skills list has no affordance for `bundledUpdateAvailable`; the
  drawer's single-file view and chooser are outside SectionBoundary.

## The round before — the model you picked answers, one plan surface, thought rows

- **The composer's model choice is honoured everywhere.** A chat that attached
  an image used to be handed to the `chat_vision` fleet role without asking
  whether the selected model can already see one, so the answer came from a
  model nobody chose while the chip showed another. Routing now claims only a
  turn that arrives with no model of its own — every Bot, automation and bench
  turn. `analyze_media` had the same disease from the other side: it
  re-resolved from the session, and a sub-agent runs against a stub session with
  no model, so the image was read on a provider default. The turn publishes its
  own `(model, gateway)` pair (`context.currentChatModel`) and the analyzer reads
  it first. Probed in-process: `pick+image → no role`, `no-pick+image →
  chat_vision`, empty fleet → inert.
- **No local capability guessing.** The modality predicate and its
  `visionUnsupported` notice were deleted: 0 of the 380 fetched model entries on
  this machine declare input types, so the guard could never fire. Every model
  is assumed to accept an image; a gateway that refuses answers with its own
  error and `onError` renders it in the thread like any other provider failure.
- **A settled thought is a stack of activity rows.** One bordered container,
  hairline-divided, each row a one-line summary with a chevron that opens that
  row's OWN reasoning — `distillThoughtSteps` pairs each summary with the exact
  segment it came from, so nothing is orphaned or repeated across rows. Nothing
  auto-expands, and a live thought keeps the single rotating line because a
  chevron over unsettled text is a lie. Measured at 1754px in an isolated mount:
  4 rows, full-width heads, opening row 0 left rows 1–3 with zero reasoning
  bytes in the DOM.
- **A turn that thinks without answering spends one round answering.** After a
  tool round, reasoning-only output closed the turn on "Stopped after tools with
  reasoning but no final answer". It now appends the assistant row, nudges for
  the text answer, and continues; the notice survives only when the second round
  also ends thinking-only. Proven both ways on the scripted provider under a
  throwaway `HARNESS_DATA_DIR`: rescue run ends `finished` with the answer and no
  notice; the two-thinking control emits exactly one `recovery {thinking-only}`
  and then the notice.
- **One plan surface, not three.** The right drawer's blue Tasks pane and the
  composer's blue progress pill both rendered the list the thread's Todo card
  renders. Both are gone, and everything only the pane carried moved into the
  card: A1..An step labels, per-step commit SHA and review state, status groups
  with counts, and the manual check-off (it was the only caller of
  `PATCH /api/workbench/todos`). The card reads the session snapshot because
  `commit_step`/`review_step` never emit `todosUpdated`, and a check-off cannot
  erase a step's proof — verified in `submitTodos → step_commits.enrich_todos`.
- **A shell-written figure reaches the side panel, repo or not.** The anatomy
  skill promises a live preview and builds it with `node build.mjs`, which no
  collector could see. A turn now baselines its workspace at turn start and
  opens what appeared — `git status` in a repository, and the new
  `GET /api/workbench/figures/recent` scan (server-side workspace, no client
  path, depth/skip/scan ceilings) when it is not, which is what a chat workspace
  normally is. Probed on the real folder: both perfboard HTML files found;
  controls exclude a 3-day-old SVG, a `.txt`, a `node_modules` page and a
  depth-4 page; a future `since` returns nothing; a bad timestamp raises 400.
- **The composer strip lost a duplicate.** The `+` and the chevron beside the
  mic were two controls wired to the same popover. The permission level moved out
  of the `+` menu onto the footer line at the left, under the `+`, and its menu
  now reads like the reference: icon, bold title, one muted line, check on the
  selected row, danger colour on the unrestricted level. Each line was written
  from the branch that enforces it, and the whole contract is now in
  [`agent/security.md`](agent/security.md) §The four composer permission levels.
- **Shipped:** `v0.22.0` — CI gates green, bundle green, live (not draft) with
  `AugustHarness_0.22.0_x64-setup.exe` + `.sig`, the MSI pair, `latest.json` and
  the manifest. `v0.21.0` is a dead tag: its build died at Tauri's 180-second
  NSIS download cap and published nothing, so it names no shipped version.
- **Not yet verified in the product:** every UI item above was measured in an
  isolated Vite mount or a scripted turn, not inside a streamed answer in the
  desktop app. The owner's own run of 0.22.0 is the remaining check.

## The round before — local models, GGUFs, Hugging Face, ComfyUI

- **Local servers are detected and one click from usable.** A keyless
  provider whose base URL is loopback now resolves everywhere a key was
  required (six gates were fixed: credentials resolve, list_available,
  activeProvider, workbench model hints, provider details, and model
  discovery). **Settings → Local Models** scans loopback (Ollama, LM Studio,
  llama.cpp, Jan, GPT4All, ComfyUI — by response shape, not port) and adds
  servers through the ordinary provider-create path.
- **GGUFs run inside August.** An on-demand llama.cpp runtime downloads into
  `{dataDir}/runtimes` (never the installer payload), every asset verified
  against its pinned SHA256, with CUDA 12.4 / CUDA 13.4 / Vulkan / CPU
  variants picked from `nvidia-smi` and a manual override. Serving a GGUF
  spawns `llama-server`, registers a 0.0-priced provider, and its models
  appear in the composer; servers are killed on shutdown and orphan-swept at
  boot.
- **Models come from Hugging Face.** GGUF search, per-quant file lists with
  exact sizes, resumable downloads into `{dataDir}/models/hf` preserving
  quant subfolders, free-disk gating, and serve-on-finish — progress polled,
  no new SSE frames.
- **ComfyUI is the image engine.** Connection + checkpoints + custom
  API-format workflows in **Settings → ComfyUI**; the `generate_image` tool
  is registered only while the connection is enabled (honest surfaces) and
  writes into the workspace through the artifact-tools door.
- **Verified on the real machine**: the runtime installed (Vulkan
  auto-selected after the 616.64 nvidia-smi banner fix; CUDA 13.4 variant
  downloading), the user's own `LFM3-1.7B-Q8_0.gguf` served, auto-registered
  with a clean model id, and answered August's Test probe — `Connected!`
  in 889 ms through the ordinary OpenAI-compatible chat path.
- **Follow-ups in the same round**: thinking blocks collapse to ONE line
  and never auto-expand; SimulIDE `.sim/.sim1/.sim2` files parse, describe
  (analyze_media), and render as schematics in the Circuit pane; chat
  attachments are uncapped and carry original file bytes (binary + from-path
  doors); Hugging Face search browses ALL models with a GGUF-only filter;
  the loopback keyless exemption now reaches the chat callers and proxy
  adapters (the Test button on a local model works).
- **SimulIDE circuits simulate in August, not just render.** A digital
  engine (nets from wires/tunnels/Node junctions, a component library for
  gates, sources, 7-segment displays, and the common 74-series subcircuits)
  reproduced the user's calculator deck exactly — idle 0, key-5 pressed 5,
  5−3=2 in subtract mode, 4-bit wrap on overflow — and the Circuit pane
  gained source toggles plus a live render (wires colored by logic level,
  DIP boxes on their real pin positions, real gate silhouettes). Analog
  parts and MCUs stay honestly unsimulated in the notes.
- **The shared space ships (from the rerun study).** One folder every
  session and bot can read and write — `{dataDir}/shared/` with
  `shared_list/read/write/search`, jailed through the sandbox door — is the
  handoff surface between chats and bots that separate workspaces lacked.
- **The Foreman and guided setup ship (also rerun patterns).** A
  boot-reconciled `Foreman` bot (persona rewritten from code each start, so
  every update ships today's Foreman) holds the one non-human authority: pin
  model/provider on teammates it builds through `create_agent`. A bot born
  with `setup_required` carries a pending walkthrough in its chat and an
  `install_complete` tool — advertised only while pending, fail-closed on
  re-call, plan-blocked and subagent-blocked — that closes the setup and
  activates the routines parked for it (`create_routine paused: true`).
- **The last two rerun patterns shipped.** `request_secret` puts a masked
  form on the tool-call card — the value goes straight into the encrypted
  store (`chatSecrets.<NAME>`), the model learns only the NAME and status,
  and MCP server env resolves `${NAME}` against it at launch. And the
  data-dir migration ladder is live: numbered, re-runnable steps applied
  before any store opens, version marker at `{dataDir}/data-version.json`,
  a failed step refuses the boot.
- Still on the human list: a real ComfyUI generation, the CUDA 12.4 variant
  on a 12-series driver, and the desktop-app pass (the dev-backend tests ran
  on :8091 against the real data dir).
- Previous round below; the self-edit loop and the rename shipped there.

## The round before that — the app edits its own source, and is renamed

- **The self-edit loop ships.** The model edits August's source only inside a
  user-local clone (`{appData}/clone`), and with self-edit on that clone *is*
  the runtime — a restart onto pre-edit source would make "apply" a lie. Every
  clone write is journaled with its pre-edit bytes and undone at boot if it was
  never applied; unattended runs cannot write the clone at all; the model can
  never push to a remote in any mode
  ([agent/security.md](agent/security.md)).
- **The apply door is judged by a checklist the clone cannot edit.** The gate
  harness is embedded in the shell (`include_str!`), not read from the tree
  under test, and it exits with its own verdict. Settings → Self-edit is the
  only way an edit becomes live, over a Tauri IPC command that is deliberately
  not a model tool.
- **0.18.26 shipped a door that could not open**: the smoke gate was spawned
  without the clone root its containment check needs, so *every* apply failed on
  `root=None`. Fixed and re-verified in the running app; **0.18.27 is the release
  that carries that fix under the old identity**, so an installed 0.18.26 can
  self-update onto a working apply door.
- **An update is deferred while self-edit is on** — re-staging
  `backend-runtime` under a clone-preferring pin would report the new version
  and execute the clone's older HEAD. Three doors refuse and each names the way
  out.
- **Renamed to augustHarness**, in four stages: env prefix and code-side
  identity, the repository and every URL that reads it, the shipped product name
  and artifact prefix, then the bundle identifier `com.august.harness`. Case is
  not style: human-facing `augustHarness`, toolchain-parsed `augustharness`, and
  the skill identifier `august-harness` (a directory name the model types).
  The old updater endpoint answers `301 → augustHarness`, verified, so installs
  already out there keep updating through the rename.
- **The identifier change moves the user's state exactly once, and proves it.**
  `migrateAppDataRoot` renames the data directory before the instance lock is
  taken, leaves a junction at the old path so anything still spelling it resolves
  to the same bytes, and compares a (file count, bytes) fingerprint before and
  after — a mismatch moves it back. It refuses rather than merges. Rehearsed
  against fabricated stores both directions, never the live one.
- `HARNESS_DESKTOP_APP_DATA` redirects the data root for **debug builds only**
  (an absolute path, ignored with a log if relative), which is what made the
  loop testable end to end without touching the real brain database.
- Not yet done, and owned by nobody: writes made through `run_command` or a
  warm kernel are still not journaled (only the file-tool door is armed); a
  committed marker has no revert path after boot; and `remote.origin.pushurl`
  locking does not stop `git push <explicit-url>`, so the push guarantee is a
  pattern guard plus a lock, not either alone.

## Two rounds before — the harness stops asking

- **Learned skills apply on their own.** `skillAutonomy` ships on for
  `skill_create`/`skill_patch`, the burn-in rail no longer deadlocks a fresh
  install, and the reviewer runs in the same pass instead of on the six-hour
  tick. Destructive kinds (delete, retire, archive, config) still wait for a
  human.
- **A Claude export moves in whole.** `POST /api/august/memory/import-claude`
  reads the folder or `.zip`, maps `profile.md`/`preferences.md` onto the
  always-in-context lanes and merges each bullet against `facts`
  ([memory.md](agent/memory.md)).
- **Repo instructions load without a consent click.** `.aug/skills` follows the
  `projectSkills` toggle like the repo's own `AGENTS.md`; the per-workspace
  skill-trust store, its routes and its banner are gone. Hook trust stays — a
  hook is unsandboxed shell. `list_skills` no longer cuts the trigger at 60
  chars.
- **Settings → Memory is the Claude-shaped list now.** The rail's Memory door
  mounts the grouped page (You / Topics / Areas / Notes / Rules) instead of the
  Library table, a shared hook line prints once per group rather than on every
  row, and `memoryReview` + `memorySensitiveTopics` moved to the top of the page
  (out of the behavior group, so neither is edited from two surfaces). The table
  stays behind the hidden `memory-facts` door, and the Skills door is unchanged.
- Not yet done: memory **retirement** proposals still need a human, repo hooks
  still have no Settings door, and `august-bench --memory-state on|off` exists
  but has no recorded two-arm result for what the loop now writes by itself.

## Previous round — repository orientation

- `AGENTS.md` is a short contract (~120 lines) with a read-before-touching
  table; the detail moved to [`docs/agent/`](agent/README.md).
- Closed plans, dated audits, deep dives, benchmark captures, release notes and
  archive trees were deleted. [`docs/DOCUMENTATION.md`](DOCUMENTATION.md) is the
  one index; git history keeps the removed documents.
- Added [`docs/PRODUCT.md`](PRODUCT.md), this file, and
  [`docs/specs/memory-and-skills.md`](specs/memory-and-skills.md).
- CI is one reusable workflow; the release job calls it; one gate command set
  is shared by CI, release and developers. See
  [`docs/agent/workflow.md`](agent/workflow.md).
- Branch, commit and staging rules are in `CONTRIBUTING.md`. Two stale branches
  were deleted: `fix/user-row-attachments` (one unmerged commit) and the
  fully-merged `harness-skill-autonomy`.

## Recently shipped before this round

- **0.18.25** — commands that cannot run are no longer offered; failures that
  change state say so; a new chat no longer asks permission for something that
  loses nothing.
- **0.18.24–0.18.21** — the installer always brings the app back; a locked
  payload aborts instead of copying over it; one teardown per click; More opens
  right, reasoning reveals in full.
- **Memory and skills** — the 18-item memory/skills spec is ~95% built. The
  current state and the remaining items are in
  [`docs/specs/memory-and-skills.md`](specs/memory-and-skills.md).

## Next, in order

1. **Memory and skills UX.** The backlog is in the spec: make recall legible,
   show a skill's trigger/usage/history, surface why a skill is hidden, and
   finish the remaining inspection surfaces. This is the owner's priority.
2. **Verify the update flow on a packaged NSIS install — including the rename.**
   The matrix in [`UPDATE_VERIFICATION.md`](UPDATE_VERIFICATION.md) has never
   been run, and `0.18.26 → 0.19.0` is the transition it was never tested for:
   the bundle identifier, the binary name and the data root all change at once.
   That transition is now history rather than a pending check — 0.19.0 shipped,
   and the live release is **0.22.0** — so what remains is running the matrix
   against a real installed machine, where an old install must show the update
   row, download, and restart itself without losing memory.
3. **The mobile companion.** The decision below is unresolved, and it changes
   the size of the work by an order of magnitude. The Expo `package` is now
   `com.august.harness`, which makes it a separate install on a phone rather
   than an upgrade of the old one.
4. **Active plans** — the Signal Desk UI port, goal environments, and
   self-modification autonomy (see [`DOCUMENTATION.md`](DOCUMENTATION.md)
   §Active plans).
5. **The orca-program remainder** (the deep dive lives outside the repo at
   `C:/Dev/_research/orca-deep-dive-2026-11-07.md`; rounds 0–4 shipped the
   feedback loop, structure, tool surface and the circuit mode slice).
   Still open, in value order: fleet-board acknowledge semantics (N1),
   browser element-grab into agent prompts (N2 — spike the backend browser
   service first), automations precheck/grace/refusal-folding + template
   picker (N4+N10), the display substrate Waves 0–1 full pass (spec.ts,
   registry census gate, scene extraction from `CircuitBreadboard3D` — the
   mode switch already ships), palette Files group (N8), settings row deep
   links (N9), local dictation via faster-whisper (N6). Provider quota bars
   (N5) stays deferred — each provider is its own integration.

## Open owner decisions

| Decision | The question | Where it lands |
|---|---|---|
| Mobile: second client or second August | Does the phone drive the desktop install over the network, or run its own backend and brain? | `ROADMAP.md` §NEXT, mobile |
| Group across a turn boundary | Should a read, an answer, and another read be one activity line? | `ROADMAP.md`, chat surface |
| Apply an update without a restart | The download is automatic; should an idle app restart itself? | `ROADMAP.md`, update flow |
| `/mcp/harness` by default | It is closed unless external access is enabled; an outer agent needs the key | `ROADMAP.md` |
| Subagent memory reads | Subagents currently read and recall but never write; opening reads wider is a product ruling | `docs/specs/memory-and-skills.md` |

## Resume

```bash
npm run dev:desktop                 # the product
cd backend-py && uv run ruff check . && uv run mypy app/
npm run check -w frontend/desktop
npm run gates:all                   # the static gate set
npm run e2e:smoke                   # starts the real app against a throwaway data dir
```

Two machine facts that are not in the tree. This checkout still lives at
`C:\Dev\august-proxy` — the directory rename to `augustHarness` is blocked while
an editor or a session holds the workspace root, and after it the backend venv
needs `uv sync` (its editable `.pth` records the absolute path). And
`origin` here carries a locked `pushurl`: a push needs the lock lifted by the
owner, which is the point of it.

The product goal is in [`PRODUCT.md`](PRODUCT.md); the contract is
[`AGENTS.md`](../AGENTS.md); the area guides are [`docs/agent/`](agent/README.md).
