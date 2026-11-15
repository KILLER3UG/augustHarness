# August redesign — audit and plan (for approval)

Status: **plan only. No code changed.** HEAD `9804b069`, branch `master`, working tree
clean except the prior review under `docs/research/`. Every claim below is marked
VERIFIED (read the code / ran the tool this session) or INFERRED.

What this document is: the audit of every screen, bot component, setting and background
process the redesign touches, each marked **keep / merge / delete** with one line of
reasoning; the research that informs the design; the target design; the phased migration;
risks; and the decisions I need from you before any code moves.

---

## Part 0 — What already exists (so we don't rebuild it)

Reading the tree against your spec, a surprising amount of the redesign is **already
shipped** and only needs re-pointing, not building. This is the most important finding in
the audit.

| Your requirement | Already in August | Evidence |
|---|---|---|
| One chat surface, agent orchestrates | Chat is the primary route; subagents already exist and report through the orchestrator | `sections/chat/`, `services/subagent_orchestrator.py`, `components/chat/SubagentDelegateRow.tsx` |
| Subagents inline, collapsible, with todos, several at once | `SubagentTimeline.tsx`, `AgentStateDot.tsx`, todo card, fanout budget | `components/chat/SubagentTimeline.tsx`, `SubagentDelegateRow.tsx` |
| Background tasks keep running while you chat, notify on finish | Background command tasks + completion notices + notifications store | `services/background_commands.py`, `store/notifications.ts`, `list_background_tasks` |
| Memory visible/editable, categorized, summaries, timestamps | Settings → Memory list (flat, filter chips, detail pane) + markdown record | `sections/settings/MemorySection.tsx`, `services/memory_store/` |
| Memory recall end to end | Two lanes (profile + delta) + pull-only BM25 + boot index | `memory_store/fact_retrieval.py`, `brain.py` |
| Agent writes memory and skills directly | `remember` writes with no read-before-write gate; skill autonomy on by default | `session_tools.py`, `harness_rails.py` |
| Skills load lazily | `SKILL.md` index + body loads only on `load_skill` | `skill_service.py`, `capabilities_prompt.py` |
| Save point on every file change | Checkpoint service (session checkpoints + restore) | `services/workbench/checkpoint_service.py`, `routers/workbench.py:1641-1649` |
| Tray, background running | Tray icon (Show/Hide/Quit) | `src-tauri/src/tray.rs` |
| MCP connectors | MCP client, server registry, per-server env | `routers/mcp.py`, `services/tools/mcp_client.py` |
| Local models first-class | Loopback keyless, llama.cpp runtime, HF download, ComfyUI | `llama_runtime.py`, `hf_downloads.py`, `providers/api_format.py` |
| Model capability profile | Context-window resolution + per-model settings | `model_service.py:184-365`, `DEFAULT_CONTEXT_WINDOW` |
| Prompt budget by context window | `tier1IndexBudget(window)` | `prompt_build.py:295` |

**Implication:** the redesign is ~60% re-pointing and deleting, ~40% building. The build
work concentrates in three places: the wake-up brief, the Research Paper Studio pack, and
the docx/export + citation-verification plumbing. Everything else is subtractive.

---

## Part 1 — The audit table

Verdict key: **KEEP** = stays as is · **MERGE** = fold into the chat/agent · **DELETE** = remove · **REWORK** = stays but changes shape.

### 1a. Screens and routes

| Screen / route | Files | Verdict | Reasoning |
|---|---|---|---|
| Chat (`/`, `/c/:id`) | `sections/chat/` | **KEEP** | This is the product. |
| Agents (`/agents`) | `sections/agents/Agents.tsx` (183 ln), `routers/agents.py` (598 ln) | **MERGE** | Bot roster is the "manage bots" UI your principle 2 deletes; fold the useful bits (agent = orchestrator identity) into chat, drop the roster. |
| Automations (`/automations`) | `sections/automations/` | **KEEP** | Scheduled work is explicitly in scope ("new workflows … can become scheduled automations"). |
| Runs (`/runs`) | `sections/runs/` | **KEEP (quiet)** | Background-task history; keep but demote — it is a supervisor view, not a daily surface. |
| Board (`/board`) | `sections/board/` | **REWORK** | Kanban-of-agents is bot-management UI; repurpose as a research-project board or fold into Runs. Decide (Q3). |
| Charts (`/charts`) | `sections/charts/` | **MERGE** | Charts are outputs of data analysis; belong in chat artifacts, not a top-level surface. |
| Artifacts (`/artifacts`) | `sections/artifacts/` | **MERGE** | The artifact card in chat already shows deliverables; the standalone page is a second surface for the same thing. |
| Live (`/live`) | `sections/live/` | **DELETE** | Voice/live is out of scope for v1 (your "out of scope" list). |
| History (`/history`) | `sections/history/` | **KEEP** | Session history is memory-adjacent and needed. |
| Learning (`/learning`) | `sections/learning/` | **KEEP** | Skills library + review pass live here; core to "skills handle personalization". |
| Docs (`/docs`) | `sections/docs/` | **REWORK** | The in-app system map; keep but make it opt-in (hidden tier), not a daily surface. |
| Settings (`/settings/*`) | `settings/` | **REWORK** | See 1c — collapse to the few that matter. |
| Terminal dock | `components/shell/BottomTerminalDock.tsx` | **KEEP** | Programmer pack needs it; hidden unless invoked. |
| Right drawer (files/diff/subagents) | `components/shell/RightDrawer*.tsx` | **REWORK** | Subagents section merges into chat; files/diff stay as the workspace inspector. |

### 1b. Bot-related components (the "manage bots" surface)

| Component | Files | Verdict | Reasoning |
|---|---|---|---|
| Bots rail | `components/sidebar/BotsRail.tsx` (~380 ln) | **DELETE** | The roster is the bot-management UI; the orchestrator replaces it. |
| Bot create modal | `components/sidebar/BotCreateModal.tsx` | **DELETE** | The user never configures a bot (your §1). |
| Room view (group chats) | `components/sidebar/RoomView.tsx` | **DELETE** | Multi-bot rooms are out of scope for v1. |
| Agents page | `sections/agents/Agents.tsx` | **DELETE** | Same as the rail — roster management. |
| `bot_mode/` backend (dm, rooms, roster, routines, setup) | `services/bot_mode/*.py` (2,426 ln) | **DELETE** | The entire bot-messaging subsystem; subagents replace it. |
| `routers/agents.py` | 598 ln | **REWORK** | Keep the agent-registry read (the orchestrator needs an identity); drop bot CRUD, DM, rooms. |
| Agent settings (sandbox, automation) | `settings/AgentSandboxSection.tsx`, `AgentsAutomationSection.tsx` | **REWORK** | Sandbox policy stays (security); bot-specific automation config merges into Automations. |

### 1c. Settings sections

| Section | tier now | Verdict | Reasoning |
|---|---|---|---|
| General, Appearance, Model settings, About | basic | **KEEP** | Setup essentials. |
| Memory | basic | **KEEP** | Core to the redesign. |
| Skills | basic | **KEEP** | Core to the redesign. |
| Plugins (MCP) | basic | **KEEP** | Connectors come through MCP (your §4). |
| Automations | basic | **KEEP** | Scheduled workflows. |
| Phone pairing | basic | **MERGE** | Useful but not daily; move to a hidden tier or fold into General. |
| Explore (local models) | basic | **MERGE** | Fold into Model settings — one place to pick a model, local or API (your §7). |
| Goals | basic | **KEEP** | Ties to the wake-up brief (open tasks, deadlines). |
| Computer Use, Browser Use | basic | **HIDDEN** | Power features; keep but not in the first-run surface. |
| System Status, Account, Privacy, ComfyUI, UI Designer, Memory tools, Skill tools, Commands, Model catalog, …(31 hidden) | hidden | **REWORK** | Keep the registry, but the first-run flow shows only: model, workspace, memory on/off, quiet mode. Everything else is progressive. |

### 1d. Background processes and daemons

| Process | Files | Verdict | Reasoning |
|---|---|---|---|
| Health monitor | `health_monitor.py` (205 ln) | **KEEP** | Needed for the tray/wake-up footprint. |
| Environment watcher | `environment_watcher.py` (253 ln) | **KEEP** | Detects workspace changes; feeds save points. |
| Learning scheduler | `learning_scheduler.py` (503 ln) | **KEEP** | The review pass; core to skills. |
| Scheduler (cron) | `scheduler.py` (478 ln) | **KEEP** | Automations and scheduled packs. |
| Daemon manager | `daemon_manager.py` (628 ln) | **REWORK** | The write-only-status bug (from the prior audit) blocks the wake-up brief; fix or simplify. |
| Background commands | `background_commands.py` (662 ln) | **KEEP** | Literature search / long drafting. |
| Host agent / cognitive boot | `host_agent.py`, `cognitive_boot.py` | **REWORK** | Boot path must serve the wake-up brief; audit for idle cost. |
| `bot_mode` routines | `services/bot_mode/routines.py` (358 ln) | **DELETE** | Bot automation; subsumed by Automations. |

### 1e. Tools and doors the redesign leans on

| Tool / door | Verdict | Reasoning |
|---|---|---|
| `remember`, `list_facts`, `forget`, `brain_query` | **KEEP** | Memory CRUD, already gate-free for the agent. |
| `load_skill`, `skill_create`, `skill_patch` | **KEEP** | Lazy skills + direct write. |
| `spawn_subagents` | **KEEP** | Orchestrator + inline subagents. |
| `run_command` / background tasks | **KEEP** | Experiments, builds, literature search. |
| `web_search` / `web_fetch` | **REWORK** | Must carry citation metadata through to the model and the UI (see Part 4). |
| Save-point / checkpoint | **KEEP** | Every file change undoable (your §4). |
| `analyze_media` | **KEEP** | PDF/figure reading for the paper pack. |
| `request_secret` | **KEEP** | Risky-action confirmation (your §4). |

---

## Part 2 — Research (what the references do, and what fits)

Studied for this plan: **Hermes Agent** (the closest sibling), **Claude Desktop** and
**Claude Code** (observed behaviour, no source), **ChatGPT desktop** (observed), the
**DeepSeek harness** (`dsh`), and the research tools **Zotero**, **Elicit**, **Overleaf**.
Evidence for the harnesses is in `docs/research/august-harness-review/02-harness-notes/`
(this tree, from the prior pass). The research tools were read at the product level
(public docs and the live apps), marked INFERRED.

### Chat-first layout

- **Claude Desktop / ChatGPT:** one conversation column, a thin session rail, no mode
  switcher, no bot roster. This is the shape your principles describe. **Take:** the
  single-column transcript with a collapsible rail; the model/persona picker lives in the
  composer, not a separate screen. **Skip:** their cloud account surfaces.
- **DeepSeek `dsh`:** the chat is a *pure renderer of the session log* — every node type
  (user, assistant, tool, todo, plan, subagent, error) has a renderer and unknown types
  render a hidden default. **Take:** this is the robust shape for "subagents report inline
  and scale to several at once" — the log is the source of truth, the UI is a projection.
- **Hermes desktop:** streaming responses, live tool activity, side-by-side preview panes.
  **Take:** the preview pane for files/artifacts (August already has it).

### Background assistant / wake-up

- **No harness studied ships a true "wake the laptop, brief me" assistant** — this is a
  genuine gap, not a copy job. The closest pieces: Hermes's cron scheduler + notification
  delivery, and `dsh`'s jittered update-check polling (10 min ±20%, backoff to 1 h) as a
  *light idle footprint* pattern.
- **Take:** `dsh`'s polling-with-jitter for the idle heartbeat; Hermes's "one message per
  wake, dismissible" delivery discipline. **Design constraint from the references:** keep
  the idle process event-driven, not a busy loop — the footprint measurement (Part 6) is
  the gate.

### Subagent display

- **`dsh`:** subagent runs are first-class log nodes; the parent sees the call and the
  summary, never the child's intermediates.
- **OpenHands Agent Canvas:** a running action is replaced by its observation with a
  shared key so disclosure state survives the swap.
- **Crush:** subagents are real child sessions with their own todo lists and a report that
  folds into the parent's turn.
- **Take:** August's `SubagentTimeline` already does most of this; the redesign adds a
  per-subagent todo list and makes "several at once" legible (fanout receipt, per-lane
  status). The prior audit found `subagentFanout` is emitted but dropped by the frontend —
  rendering it is the first concrete step.

### Research tools (libraries, citations, paper structure)

- **Zotero:** the source library is a first-class object — each item has metadata (title,
  authors, year, DOI/URL), a PDF, notes, and a citation key. **Take:** the library shape
  (one folder per paper, per-item metadata + notes + attachment) maps directly onto your
  §6 "source library". Zotero's "every item must resolve to a real record" is the same
  discipline as your non-negotiable "never fabricate references".
- **Elicit:** search → screen → extract, with each answer tied to a specific paper and a
  "show me the passage". **Take:** the "show me where this came from" affordance — every
  research claim carries a locator (title + section + quote), not just a citation.
- **Overleaf:** one project = one folder with a main document, a bibliography, and a
  compiled output; chapters are `\input` files. **Take:** the project-folder shape and the
  "one draft per chapter, one state file" structure you described; the paper-state file is
  Overleaf's `main.tex` metadata, externalized. **Skip:** the LaTeX toolchain (you said
  docx first, LaTeX optional).

### What we take, in one line each

- **From Claude/ChatGPT:** the single-column, no-mode, rail-collapsed layout.
- **From `dsh`:** the log-as-source-of-truth renderer; the idle polling shape.
- **From OpenHands/Crush:** subagent-as-child-session with its own todos; report folds into
  the parent.
- **From Zotero:** the source-library object model and "every reference resolves".
- **From Elicit:** the "show me where this came from" locator on every claim.
- **From Overleaf:** the per-paper folder with a state file, chapter files, and an export.

### What we explicitly reject (from the references)

- **Multi-bot rooms** (Hermes/ChatGPT) — your §1 removes bots.
- **Cloud account / team collaboration** (ChatGPT, Overleaf) — out of scope.
- **A mode switcher** (DeepSeek's visibility modes, `dsh`'s plan/build) — your principle 1.
- **The skill marketplace** (Claude) — out of scope for v1.
- **LaTeX-first export** (Overleaf) — docx is the default, LaTeX optional.

---

## Part 3 — The design

### 3a. Chat layout (the whole app)

```
┌────────────────────────────────────────────────────────────┐
│ ☰  August            [model ▾]              [quiet] [⚙]     │  ← thin top bar
├──────────┬─────────────────────────────────────────────────┤
│ session  │  transcript (log-as-source-of-truth)            │
│ rail     │  ┌───────────────────────────────────────────┐  │
│ (thin,   │  │ user: Write chapter 2 on X                 │  │
│  col-    │  ├───────────────────────────────────────────┤  │
│  lapsable│  │ assistant: I'll draft Ch2. 3 sources left. │  │
│  )       │  │  ▸ Ran 4 commands, read 2 files     (card) │  │
│          │  │  ▸ Subagent: literature search      ◔    │  │  ← inline, collapsible,
│  ─────── │  │    └ todo: [x] find [ ] screen [ ] cite  │  │     each with own todo
│  + new   │  │  ▸ Artifact: chapter2_draft.docx  ↓       │  │
│          │  └───────────────────────────────────────────┘  │
│          │  ┌───────────────────────────────────────────┐  │
│          │  │ composer                          [ ↑ ]     │  │
│          │  └───────────────────────────────────────────┘  │
└──────────┴─────────────────────────────────────────────────┘
```

- **One column.** No mode switcher, no bot roster, no separate agents screen. The rail is a
  session list, collapsible to zero.
- **The model picker lives in the composer**, not a screen (your §7: first launch = pick a
  model, done).
- **Subagents render inline** as compact rows with their own todo; the fanout receipt
  ("3 running, concurrency 2") is a muted line (this is the `subagentFanout` frame, already
  emitted by the backend and currently dropped — rendering it is the first step).
- **Everything else is an artifact or a settings panel, not a screen:** charts, artifacts,
  and board fold into the transcript or a right drawer.

### 3b. Wake-up brief

```
[ 09:14 ]  Good morning. Chapter 2 is 60% drafted — 3 of 12 sources still unread.
           Your Chapter 1 objectives are all traced. One thing: do you want the
           methodology section to assume a survey or an experiment?
           [Answer]  [Open chapter 2]  [Dismiss]
```

- **Trigger:** app was quit/backgrounded, laptop wakes (or the app is opened after idle).
- **Content:** open tasks, deadlines, recent sessions, one question. Generated by the
  orchestrator from memory + session state, **one message max**, dismissible.
- **Quiet mode:** a switch in the top bar; when on, the brief is queued and shown on next
  open, never as a notification.
- **Idle footprint:** the background process is event-driven (wake signal → brief), not a
  poll loop; the heartbeat reuses `dsh`'s jittered backoff for any periodic check. The
  measurement in Part 6 is the gate, not a guess.
- **Data:** the brief reads all sessions + memory (your §2). This is the one place the
  agent is *proactive*; it stays calm by construction (one prompt per wake).

### 3c. Orchestrator and subagent reporting

- The main agent is always the orchestrator. It spawns subagents via the existing
  `spawn_subagents`; **subagents report only to the orchestrator**, never to the user (your
  §1). The orchestrator folds their reports into one assistant turn.
- Subagents show inline as compact collapsible items, each with its own todo list; several
  run at once (the fanout budget already caps children and concurrency — render the
  receipt).
- Background tasks (literature search, long drafting, experiments) keep running while the
  user chats; completion arrives as a queued notice and a quiet badge (the notifications
  store already exists).

### 3d. Memory UI

- Settings → Memory is already the flat, categorized list with summaries and timestamps.
  **Keep.** The redesign adds: (1) a "what the agent will recall on the next turn" preview
  (the boot index), so recall is auditable end to end; (2) the two lanes named in the UI
  (profile/preference, delta) so the user sees *why* something was recalled.
- The recall end-to-end audit (your §3) is a Phase 2 deliverable: a scripted turn that
  writes a fact, opens a new session, and confirms it is in the boot index and the profile
  lane, with the `recall partial:` receipt when budget drops one.

### 3e. First-run setup

```
Welcome. Two questions, then you're working.
  1. Which model?   [ Local: LFM3-1.7B (detected) ]  [ API: OpenAI ▾ ]  [ Other… ]
  2. Where should I work?   [ ~/Documents/my-paper ]  [ Choose… ]
  [ Start ]
```

- Defaults for everything else (memory on, quiet mode off, sandbox at the safe level).
- Clarifying questions use `ask_user_input` (your §7) — the clarify tool already exists.

### 3f. Research Paper Studio (the pack)

This is a **skill pack**, not a mode (your §6). It is a bundled `SKILL.md` plus a set of
tools, installed like any skill, invoked by name or by intent.

```
my-paper/                         ← one workspace folder per paper
  paper-state.md                  ← title, problem, RQs, objectives, hypotheses,
                                    scope, methodology decisions
  sources/                        ← the source library
    <key>.md                      ← per-item: metadata (title/authors/year/DOI),
                                    summary, notes, locator quotes
    <key>.pdf                     ← imported PDF
  notes/
  drafts/
    chapter1.md … chapter5.md
    abstract.md  references.md  appendix-*.md
  tables/  figures/
  exports/                        ← docx, (optional) LaTeX
  traceability.md                 ← objective → method → result → conclusion matrix
```

- **Chapter workflow:** Ch1 Introduction → Ch2 RRL + framework → Ch3 Methodology → Ch4
  Results & Discussion → Ch5 Summary/Conclusions/Recommendations, plus abstract,
  references, appendices. Structure and citation style (default APA 7) configurable to a
  school/adviser format guide (a per-project config file).
- **Consistency:** `traceability.md` links each Ch1 objective to its Ch3 method, Ch4 result,
  Ch5 conclusion. A consistency-check tool flags an objective with no result, a result with
  no objective, a citation in Ch2 never used, etc.
- **Literature:** search (web_search/web_fetch + arXiv), read, and add to the library with
  summary + notes + a **verified** citation (title/authors/year/DOI resolved, not assumed).
  Comparison tables and synthesis are generated into `notes/`.
- **Methodology & data:** research-design and sampling guidance (advisory text);
  statistical-test selection and execution on **real data the user provides** (a data tool
  that runs the test and emits a table + chart into `tables/`/`figures/`). For programmers:
  system-development methodology, architecture diagrams (the anatomy/mermaid skills), an
  SRD template, and running real experiments/benchmarks with metrics tied to the code.
- **Writing:** draft chapter by chapter, using `paper-state.md` + the library + the prior
  chapters as context. This keeps each drafting turn inside a small model's window (your
  §8) — the agent never loads whole papers, it loads per-section context.
- **Export:** docx (headings, TOC, numbered tables/figures, citations, page numbers) via
  `python-docx`; LaTeX optional. Import adviser comments from a docx and revise against
  them. Defense prep: slides (pptx) + practice Q&A.
- **Integrity rules (non-negotiable, enforced in code not just prompt):**
  - Every citation is resolved against a real record (search/fetch returns the title,
    authors, year, DOI/URL). A source that cannot be resolved is flagged `UNVERIFIED` in
    the library and never silently included.
  - Chapter 4 content comes only from real data the user provides or experiments actually
    run; the data tool refuses to emit statistics without an input file.
  - A provenance record (which text the agent drafted vs the user wrote) is kept per
    chapter, and the export carries a short AI-use note reminding the user of their
    school's policy.

---

## Part 4 — Source-grounded answers (the citation plumbing)

The redesign's research answers need citations and a "show me where this came from"
affordance (your §5). Today `web_search` returns ranked snippets only and the model cites
from them (VERIFIED: `web_tools.py:3` — "returns ranked snippets only (cite-then-fetch)").

The change:
- `web_search` / `web_fetch` results carry a **locator** (title + url + the passage/quote)
  through to the model, so a citation can name *where* it came from, not just *that* it
  exists.
- A `cite` ledger in the session (the `grounded-citations` skill's `sources.py` pattern:
  a url → id mapping the model is handed, so ids come from retrieval, never memory).
- When unsure or no source is found, the agent says so (`[unverified]`, a named gap) rather
  than smoothing over it.
- The UI renders a citation chip that expands to the locator ("show me where this came
  from").

This is the same discipline the Research Paper Studio's "never fabricate references" rule
needs, so it is built once and shared.

---

## Part 5 — Phased migration

Each phase ships and is verifiable without breaking the app. **No feature is deleted until
its replacement works** (your §4). Data, sessions, and memory are preserved throughout (the
memory record is files; the migration never touches `dataDir/memory/`).

### Phase 1 — Chat-only surface + orchestrator (foundation)
- Render `subagentFanout` (already emitted; add the schema literal + dispatch case + a
  `default:` so unknown events can't vanish) — this is also a fix from the prior audit.
- Make the rail collapsible-to-zero; move the model picker into the composer.
- Demote (do not delete) Automations/Runs/History to a supervisor menu; hide Live, Charts,
  Artifacts, Board behind a "more" door (they become artifacts/drawers).
- **Verify:** the app opens to chat; a turn with subagents renders inline with the fanout
  receipt; no route 404s; `gates:static` green; a human run of the desktop app.
- **Deletes in this phase:** none yet (replaced only when the next phase lands).

### Phase 2 — Workspace, memory, save points
- Wire the workspace folder as the agent's default root; the checkpoint service becomes the
  save point for every file change (list/restore surfaced in the diff card).
- Memory UI: add the "what will be recalled" preview + the two named lanes; run the recall
  end-to-end audit and record the result.
- **Verify:** a file edit is undoable from the diff card; a fact written in one session is
  recalled in the next; the recall audit passes.
- **Deletes in this phase:** `bot_mode/` (dm, rooms, roster, routines) — the subagent path
  now covers it; `BotsRail`, `BotCreateModal`, `RoomView`, `Agents.tsx`. Each deletion is
  its own commit with the gates green, and only after the orchestrator is proven in Phase 1.

### Phase 3 — Research Paper Studio
- Ship the pack: project folder, paper-state, source library, chapter drafts, traceability,
  consistency check, docx export, citation verification, adviser-comment import.
- **Verify:** end-to-end on a real sample topic (Part 6) — and **every citation the pack
  produces is checked to exist** (title/authors/year/DOI resolved against a live search),
  with any unresolved source flagged, not included.
- **Deletes:** Live (voice) is removed here if still present — out of scope.

### Phase 4 — Wake-up and background assistant
- Tray autostart (opt-in), event-driven wake brief, quiet mode, one-prompt-per-wake.
- Fix the daemon-status bookkeeping the wake brief depends on (the write-only-status bug
  from the prior audit).
- **Verify:** measured idle footprint (Part 6); the brief fires once per wake, is
  dismissible, and quiet mode suppresses it.

### Phase 5 — Programmer pack + local-model optimization
- Programmer pack: repo awareness, test/terminal running, background coding subagents, diff
  review (most of this already exists — it is a skill pack + surfacing).
- Local-model optimization: audit the system prompt / tool schemas / memory injection for
  small windows and trim; per-model capability profile (context size, tool-calling
  reliability) tested and recorded; model-warmth with release on idle/battery/load.
- **Verify:** a small local model completes a paper-drafting turn inside its window; the
  capability profile is accurate; the prompt-budget measurement (Part 6) shows the trim.

### What is deliberately NOT in v1
Skill marketplace, team collaboration, voice, mode switcher, student/worker packs, LaTeX
(default docx). The `/_design` dev route and the UI Designer settings stay hidden.

---

## Part 6 — Verification and measurement

The brief says "verify everything by running it" and "measure idle CPU/RAM/VRAM instead of
assuming." Concretely, per phase:

- **Functional:** the phase's own `gates:*` group green, plus a human run of the desktop app
  for the surfaces touched (the repo's standing policy — there is no test suite).
- **Research Paper Studio end-to-end:** a scripted run on a real sample topic (e.g. "the
  effect of X on Y") that produces Ch1–Ch5, a docx export, and a citation list; then a
  verification pass that resolves every citation (title/authors/year/DOI) against a live
  search and fails on any that does not exist. **No paid API key** — use a local model or a
  free search backend (DuckDuckGo/SearXNG, already in `web_backends.py`), and say what was
  not live-tested.
- **Idle footprint:** measure the background process at rest (no turn running) for CPU %,
  RSS, and — for a local model held warm — VRAM, on this machine (RTX 4050, 6 GB), and
  report the numbers rather than assuming. The measurement is the gate for the wake-up
  phase.

---

## Part 7 — Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Deleting `bot_mode/` breaks a live path (DMs, rooms, automations that reference bots) | high | Phase 1 proves the subagent path first; deletion is a separate commit per file with gates green; grep every importer before each delete. |
| Wake-up brief becomes an annoying notification loop | med | One prompt per wake, dismissible, quiet mode; event-driven, not polled; measured footprint. |
| Small local models cannot drive the paper workflow reliably | high | Per-section context (never whole papers); simple tool schemas; strict validation + repair; fallback to a stronger model when configured; capability profile per model. |
| Citation verification is itself wrong (a resolver that says "exists" for a hallucination) | high | Resolve title+authors+year+DOI against a real search result; treat "no exact match" as unverified; never trust the model's own claim. |
| docx export formatting drifts from a school's format guide | med | Format guide is a per-project config (APA 7 default); the exporter reads it; a human checks the first export. |
| Data loss during the memory/UI migration | high | The record is files; the migration never writes `dataDir/memory/`; a backup is taken before Phase 2; restore is staged (existing machinery). |
| Idle footprint too heavy on a laptop | med | Measure first (Part 6); release the model on idle/battery; event-driven wake. |

---

## Part 8 — Open questions (need your call before code)

1. **Board:** repurpose the kanban as a research-project board, or fold it into Runs and
   delete it? (Q3)
2. **Bots — hard delete or soft?** `bot_mode/` is 2,426 lines with live importers (DMs,
   rooms, automations). Confirm the hard delete, or keep the agent registry read and delete
   only the messaging/room layer?
3. **Live/voice:** confirm it is a Phase 3 delete, not a keep.
4. **Wake-up trigger:** laptop wake (OS event) only, or also "app opened after idle"? The
   first is cleaner; the second is what a user actually sees.
5. **Citation resolver:** is a free backend (DuckDuckGo/SearXNG) acceptable for the
   verification pass, or do you want a paid scholarly API (Crossref/Semantic Scholar) wired
   in? Crossref is free and returns DOIs — my recommendation.
6. **Docx scope:** is docx the only required export for v1, with LaTeX deferred? (You said
   docx, LaTeX optional — confirming LaTeX is out of v1.)
7. **Provenance record:** the "agent-drafted vs user-written" record — store it as a
   sidecar file per chapter (`drafts/chapter2.provenance.md`), or inline markers? Sidecar
   keeps the draft clean; inline survives copy-paste. My recommendation: sidecar.

---

## Part 8 — Owner decisions (answered, 2026-11-15)

All seven questions were decided. They are recorded here as the binding rulings — the
phases above are unchanged except where a ruling changes them, and each change is noted.

| # | Decision | Ruling | Where it lands |
|---|---|---|---|
| 1 | **Board** | **Fold into Runs and delete.** A manual kanban is agent-management chrome; open tasks are already covered by todos, `paper-state.md`, `traceability.md`, and the wake-up brief. | Phase 2 (delete), after Runs absorbs the useful rows |
| 2 | **Bots** | **Hard delete** — `bot_mode/`, `BotsRail`, `BotCreateModal`, `RoomView`, and the bot CRUD/DM/rooms parts of `routers/agents.py`. **Two conditions:** (a) export all existing bot data (roster, history, routines) to an archive file first, so nothing is lost; (b) convert any routine worth keeping into an automation. Migrate or remove every live importer first, **one file per commit, gates green after each**. | Phase 2 |
| 3 | **Live/voice** | **Hide in Phase 1** (`nav: false`), **delete in Phase 3** after confirming no importers remain. Git history keeps it. | Phase 1 done; Phase 3 delete |
| 4 | **Wake-up trigger** | **All three, behind one cooldown:** resume from sleep, login/unlock, and first app open after a long idle period. Idle threshold and cooldown are **configurable**. **At most one brief per start**; quiet mode queues it. The daemon manager's write-only-status bug is fixed in Phase 4, since the brief depends on it. | Phase 4 |
| 5 | **Citation resolver** | **Free sources only for v1.** **Crossref primary**, with **OpenAlex** and **arXiv** as fallbacks for coverage. No paid scholarly API. Any citation that cannot be resolved to a real record (title, authors, year, DOI or URL) is **flagged unverified and never silently included**. | Phase 3 (paper pack) |
| 6 | **Output format** | **docx only for v1.** LaTeX deferred. | Phase 3 |
| 7 | **Provenance** | **Sidecar file per chapter** (`drafts/chapter2.provenance.md`). Inline markers would clutter the drafts and break exports. | Phase 3 |

### What the rulings change about the phases

- **Phase 1** is complete (see `00-phase1-report.md`): `subagentFanout` is rendered, Live is
  out of the rail and the palette, and the rail's collapse-to-zero and the composer model
  picker turned out to be **already shipped** — so two of the four Phase 1 items needed no
  code.
- **Phase 2** now carries the bot export → archive → importer-migration → delete sequence as
  its first block, in that order, one file per commit. The archive is a **precondition** of
  the delete, not a follow-up.
- **Phase 4** gains the three-trigger cooldown design and the daemon-status fix as a named
  dependency.
- **Phase 3** gains Crossref/OpenAlex/arXiv as the resolver chain and the sidecar provenance
  format.

---

## What I did not do

- **No code changed.** This is a plan for approval, per your instruction.
- **No paid API calls.** All research is from reading the tree and public docs.
- **The reference clones from the prior pass were deleted** after that review; the harness
  evidence lives in `docs/research/august-harness-review/`. The research-tool notes
  (Zotero/Elicit/Overleaf) are product-level (INFERRED from their public docs and apps),
  not source-read.