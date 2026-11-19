# DeepSeek Harness (`dsh`) — deep-dive note for August

Source: `deepseek-ai/deepseek-harness` @ `C:/Users/rober/AppData/Local/Temp/august-review/repos/deepseek-harness` (~21.6k files, TS monorepo, MIT).
`dsh` = a local-first, multi-profile agent harness on **Cordis** ("Everything is a Plugin"). Evidence paths below are repo-relative; line numbers cite files actually read.

## 1. Startup flow

- Entry is the `dsh` launcher, which picks a **profile** (web, headless, sdk, sdk-minimal, acp) — a named composition of plugins — then mounts the Cordis plugin tree (`docs/architecture.md:15-39`, `docs/cordis-primer.md`).
- First layer bundle `@deepseek-ai/dsh-base` (settings store, config editor, workspace + env, storage providers, app lifecycle) is mounted before capability bundles like `dsh-tools`, `dsh-skills`, `dsh-compaction` (`docs/architecture.md:23-30`).
- Desktop: Electron **RunAsNode** child process starts the profile runner; the packaged web entry loads at `dsh-app://app/`; boot injects env, app metadata, and an IPC bridge into `globalThis.$dsh` before the web app boots (`apps/desktop/README.md:40-44`).
- Config layering at boot: profile bundles → workspace `cordis.patch.yml` → home patch → `--patch` CLI overlays (`packages/settings/settings/README.md`).
- `dsh --profile web --dump-config` prints the effective plugin tree — a boot-time introspection escape hatch (`docs/architecture.md:36-39`).

## 2. UI/UX patterns (web chat + desktop shell)

- The chat UI is a **pure renderer of the session log**: `ConversationNodeDefinition` + keyed renderer registration; every node type (user, assistant, tool call, todo, plan, subagent, task, error) has a renderer; unknown types render a hidden default (`docs/architecture.md:163`, `packages/client/ui-chat/README.md`).
- Work-details visibility modes per turn: Compact / Standard / Detailed / Final answer only / Verbose (`packages/client/ui-chat/README.md:119`).
- "Turn process" folding: intermediate steps collapse under the turn; expandable; scroll ownership rules distinguish user-scroll vs auto-follow (`packages/client/ui-chat/README.md`).
- Desktop is an Electron shell around the same web app: titlebar + sidebar + webview, tray, global shortcut, native dialogs; updates UI is an isolated WebContentsView overlay (`apps/desktop/README.md:99-132`).
- i18n: every user-facing string localized (8 locales; every doc has an English/中文/Japanese triplet).

## 3. Memory model

- **There is no long-term memory feature** (no vector store, no fact store, no profile). "Memory" in the repo means the in-context session state.
- The session log *is* the memory: "The session log is the source of the context the model sees" — a single append-only event stream with monotonic seq, snapshot + JSONL persistence (`docs/architecture.md:124-130`, `packages/session/session-persistence-jsonl/README.md`).
- Cross-session recall exists only as **session references**: `@label` mentions resolve to bounded snapshots of another session, injected as *untrusted* context with a prompt-injection warning and `<`/`>` tag-escaping (`packages/context/session-reference/README.md`).
- Runtime context (todos, task status, team state) is delivered to the model as versioned `<system-reminder>` user messages; newer versions replace older ones, and unchanged facts are not re-printed (`packages/core/agent-loop/README.md:172`).
- Long-horizon continuity is delegated to: compaction checkpoints, the todo list tool, the goal package, and (experimentally) tool-ralph's shared workspace as memory (`docs/tool-catalog.md:1869`).

## 4. Skills / plugins system

- **Cordis plugin model**: every capability is a plugin with Service Definition / Provider / Consumer roles and reversible `start()/stop()` effects; ~60 packages under `packages/` (`docs/cordis-primer.md`, `docs/capability-seams.md`).
- **Skill registry** (`@deepseek-ai/dsh-skill`, `packages/skill/skill/src/index.ts`): merges catalogs from multiple providers, resolves the *winning* skill per name by rank, exposes `modelInvocable`/`userInvocable` policy; skill names are kebab-case (`SKILL_NAME` regex, index.ts:24).
- **Filesystem provider** (`packages/skill/skill-filesystem/README.md:46-56`) scans ranked roots: 100 `project-dsh` (`<project>/.dsh/skills`), 200 `project-agents` (`<project>/.agents/skills`), 300 `custom` dirs, 400 `user-dsh` (`~/.dsh/skills`), 500 `user-agents` (`~/.agents/skills`), 600 bundled.
- Skill = directory bundle `<name>/SKILL.md` or flat `<name>.md`; frontmatter: `name`, `description`, optional `whenToUse`, `metadata`, `disable-model-invocation`, `user-invocable` (bad values drop the whole skill with a warning — fail-closed) (`skill-filesystem/README.md:36-38`).
- Catalog and body have separate lifecycles: discovery parses frontmatter once; **every load re-reads the file**, so edits need no cache invalidation; Chokidar watches roots for catalog changes without restart (`skill-filesystem/README.md:42,74`).
- `plugin-market` package exists for plugin discovery (marketplace seeding), separate from skills.

## 5. Tool + permission model

- `ctx.tools` registry; pipeline in `docs/tool-execution-pipeline.md`: `tool/call` → `tools/pre-execute` (hooks, permission) → monotonic guards → `ctx.approval` one-shot → `tools/execute` (timeout + retry around) → tool body → fs write-intent gates → `tools/post-execute` → `finalizeContent` → `tools/result`.
- **Permission presets**: `workspace-write` (default) and `danger-full-access`; presets are named bundles of per-tool flags (`docs/capability-seams.md:761`).
- **Approval fails closed**: if no approval answerer is available, the action is denied as `unavailable` (`docs/capability-seams.md:760`).
- **Monotonic guards** (no downgrade once granted): `repeat-tool-reminder`, `timeout-policy`, `credential-reference` (`packages/guard/README.md`).
- **repeat-tool-reminder** (`packages/guard/repeat-tool-reminder/README.md`): the runaway-loop backstop is *advisory*, not a kill switch — thresholds [3,5,8] identical calls per agent chain; matches canonicalized exact arguments (sorted keys, stable serialization); on threshold it injects `additionalContexts` telling the model to stop and change approach; **resets on a real user message**; per-agent-chain tracking; never blocks execution.
- **timeout-policy** (`packages/guard/timeout-policy/README.md`): every `ToolDefinition` may declare `timeoutMs`; the guard enforces a **cooperative deadline signal** (tool should abort) plus a fallback hard kill; timeouts produce a structured `TOOL_TIMEOUT` error, not an exception.
- **credential-reference** (`packages/credentials/README.md`): config stores *names* of credentials, not values; the credential provider owns values and resolves them **per operation**, so rotation applies on the next request; settings UI shows presence, never values; unknown reference → structured `CREDENTIAL_MISSING` error.

## 6. Sandboxing

- `ctx.sandbox` seam with modes `read-only` / `workspace-write` / `danger-full-access`; default = workspace-write for mutating ops (`packages/sandbox/README.md:12`).
- Backends per OS: Linux bwrap → Landlock fallback; macOS Seatbelt; Windows restricted-token + ACL. Host env sanitization on every exec (`packages/sandbox/README.md`).
- Per-call policy overrides + one-time user-approved escalation for a single call; **same-world rule**: sandbox is process-local, so fs and exec must run in the same world (`packages/sandbox/README.md:12`).
- `sandbox-policy` is the single home for policy so bash and fs confinement agree on the workspace root (`docs/capability-seams.md:759`).
- `fs-sandbox` fences all fs mutations through the same policy; `fs` records write-intents consumed by the pipeline (`docs/capability-seams.md`).

## 7. Context management / compaction / spill

- `ctx.compaction` seam + `compaction-basic` implementation (`packages/compaction/compaction-basic/README.md`).
- Threshold: floor(min(W×0.8, W−O−B)) with B=65536 headroom; on trigger, summarize **the oldest ~84%**, retain ~16% recent.
- Two-phase: (1) **tool-result pruner** — model-free trimming of oversized tool results first; if that fits, no LLM call; (2) summarization reusing the **warm prefix** (KV cache).
- Checkpoint prompt has **8 fixed sections**: Primary Request; Key Technical Concepts; Files and Code; Errors and Fixes; Pending Jobs; Current Work; Next Step; Critical Context — and a merge rule: when a checkpoint already exists, the summary must **merge with the prior checkpoint**, not just the visible window.
- Region transaction: drop regions **bracket-first** (tool_result before tool_call before assistant), in one atomic transaction; compaction errors leave the log untouched.
- Overflow recovery: on `CONTEXT_WINDOW_EXCEEDED` mid-step, the agent compacts then retries the step once (`packages/core/agent-loop/README.md`).
- **Spill** (`packages/spill/spill-policy/README.md:49-57`): oversized tool results/user images are written to disk under the session dir; the log keeps only head+tail with a **locator** message: "Full result: `<path>` — use read with offset/limit, or grep it". Bounded literal text for images.

## 8. Subagent design

- `ctx.subagents` seam; providers: `spawn-in-process`, `fork-in-process` (seeded from parent history), `acp`, `codex`, `claude-code`, `dsh-sdk` (`packages/subagent/README.md`).
- `tool-subagent` = delegation tool; `tool-subagent-control` adds follow-up, interrupt, list (`docs/tool-catalog.md`).
- `agent-team` = shared task DAG (tasks with deps, claims, results) for parallel agents (`packages/team/README.md`).
- `tool-ralph` (experimental): fresh agent per round in a loop; the **shared workspace is the long-term memory**; each round must end with a bounded structured report that crosses rounds (`docs/tool-catalog.md:1869`).
- Sub-agents inherit model selection, skills catalog, and sandbox policy; they get their own session log.

## 9. Model / provider handling

- `ctx.llm` seam (`packages/llm/README.md`): adapters `llm-deepseek` (official API), `llm-pi-ai` (multi-provider catalogs + gateways), `llm-replay` (deterministic test playback).
- `prepareCall()` validates messages/tools and resolves defaults before any request; `llm-retry` listens to the `agent/request-error` waterfall for backoff retries.
- DeepSeek-specific wire extensions live in a separate `deepseek-llm-api-extensions` package — provider quirks do not leak into the core seam (`packages/llm/README.md`).
- Every request logs structured headers (`request/header`, `request/context`) for debugging.
- **KV-cache-aware prompt admission**: in-history updates (e.g. system reminders) are *appended*, never spliced, to preserve the prompt prefix cache (`packages/core/agent-loop/README.md:172`).

## 10. Update mechanism

- Electron + electron-updater (`apps/desktop/README.md:124-132`, `apps/desktop/publishing/README.md`).
- Fixed **Nightly check at startup**; periodic checks every 10 min ±20% jitter, exponential backoff to 1 h on failure; manual "Check for Updates".
- Flow: download → verify → ready → **user-confirmed restart** (with running-task interruption warning); Host locks new requests, drains, tears down, then installs.
- **Mandatory-update policy** channel (Feishu-driven in production) can force upgrades; **staged rollout** by percentage.
- Publishing: artifacts to COS, EdgeOne CDN purge, GitHub release; **one version identity** for shell + runtime (`apps/desktop/publishing/README.md`).

## 11. Config / settings

- **Profile** = named plugin composition; user patches via `cordis.patch.yml` + CLI `--patch` overlays (`packages/settings/settings/README.md`).
- Volatile `Config` fields are exposed as **settings forms** through `ctx.settings`; changes apply without restart where the plugin allows.
- `config-editor` persists patches under a file lock and queues HMR reloads; settings values are **redacted** for secrets.
- `docs/config-catalog.md` is a **generated** exhaustive catalog (5,427 lines) of every config field across all plugins; `docs/tool-catalog.md` (2,862 lines) and `docs/persistence-catalog.md` (9,447 lines) are generated the same way.

## 12. Error recovery

- `docs/defensive-patterns/README.md` codifies 5 habits: read the current package's code before editing; acquire services through the context, not imports; unmount effects on stop; use `defineProperty` from the consumer side; prefer semantic errors with codes over thrown strings.
- `agent/request-error` waterfall decides per-error retry actions (`packages/core/agent-loop/README.md`).
- Failed steps record **synthetic tool results** `TOOL_OUTCOME_UNKNOWN` / `TOOL_NOT_STARTED` for unanswered calls, with instruction text "retry only if read-only/idempotent" (`packages/core/agent-loop/README.md:132`).
- Compaction overflow recovery (see §7); desktop fatal-recovery dialog offers reload/export logs; updater retry/backoff (§10).
- Session crash repair: on resume, `interruptedTurnClosers` appends closing records for the interrupted turn so the log stays well-formed (`packages/core/agent-loop/README.md:116`).

## 13. Clever / notably bad

**Clever:** reversible plugin effects (start/stop symmetry); KV-cache-aware prompt admission; spill-to-file with locator; advisory repeat-tool guard with exact-arg canonicalization; the "Model experience / Token effect / KV cache effect" section mandated in every package README (`AGENTS.md`); `.agents/notes/` decision records; generated catalogs as the single source of truth.

**Notably bad / overbuilt:** ~60 packages and 21.6k files for one harness; every new capability needs Service Definition + Provider + Consumer + generated docs + i18n triplets; the ui-chat README (244 lines) documents scroll/fold machinery of great intricacy; **no turn budget by design** — runaway protection is only the advisory reminder; no long-term memory across sessions; desktop ships its own Python + Node + pnpm distributions (huge install).

## Ideas worth stealing for August

1. **Ranked skill roots with first-wins resolution** — `packages/skill/skill-filesystem/README.md:46-56`. A small rank table (project → custom → user → bundled) with dedup-by-name and a warning on collision fits August's 5-root discovery directly. Touch: `backend-py/app/services/skill_*.py`, docs/specs/memory-and-skills.md.
2. **Advisory repeat-tool nudge before the hard cap** — `packages/guard/repeat-tool-reminder/README.md`. August's runaway backstop kills; add thresholds (e.g. 3/5/8 identical calls, exact-arg canonicalized, reset on user message) that inject a "stop and change approach" reminder first. Touch: `backend-py/app/services/turn loop + guards`.
3. **Spill oversized tool output to disk + locator** — `packages/spill/spill-policy/README.md:49-57`. Keep head/tail in the transcript, store the full payload in the session dir, and tell the model the path + how to read it. Cheaper than summarizing everything. Touch: `backend-py/app/services/tool-results/compaction`.
4. **Model-free tool-result pruner runs before LLM summarization** — `packages/compaction/compaction-basic/README.md`. Trim oversized tool results first; only call the summarizer if still over threshold. Saves tokens and latency. Touch: August compaction service.
5. **Fixed-section compaction checkpoint + merge-with-prior rule** — same README. August's compaction could adopt the 8-section checkpoint (Primary Request / Concepts / Files / Errors / Pending / Current / Next / Critical Context) and require merging with the previous checkpoint. Touch: compaction prompt template.
6. **Per-tool timeout policy with cooperative deadline** — `packages/guard/timeout-policy/README.md`. `timeoutMs` on the tool definition, structured `TOOL_TIMEOUT` result, graceful cancellation before hard kill. Touch: August tool executor.
7. **Synthetic results for unanswered calls after a failed step** — `packages/core/agent-loop/README.md:132`. Record `TOOL_OUTCOME_UNKNOWN` with "retry only if read-only/idempotent" so the model can recover safely. Touch: August turn-loop error path.
8. **Credential-reference indirection** — `packages/credentials/README.md`. Config stores names; the provider resolves values per operation (rotation-safe); UI shows presence, never values. Matches August's hardline credential doors. Touch: `backend-py/app/services/credentials`, settings UI.
9. **Approval fails closed** — `docs/capability-seams.md:760`. If no approval answerer exists, deny as `unavailable`. One-line rule, big safety win. Touch: August permission service.
10. **Append-only in-history reminders (KV-cache-aware)** — `packages/core/agent-loop/README.md:172`. Deliver runtime context (memory lane, todo state) as versioned `<system-reminder>` appends that replace prior versions rather than rewriting the system prompt. Touch: August system-prompt/memory lane assembly.
11. **"Token effect / KV cache effect" section in feature docs** — `AGENTS.md`. A one-line docs discipline: every model-visible feature states its prompt/cache cost. Touch: `docs/agent/*`, skill template.
12. **Update flow confirmation details** — `apps/desktop/README.md:124-132`. Validates August's auto-download + explicit-restart design; worth stealing the "ask what would be interrupted" check before restart and jittered polling with backoff. Touch: `frontend/desktop/src` updater, `scripts/release`.

## Anti-patterns to avoid

- **Cordis-everything composability** for a minimalist local-first product: Service Definition/Provider/Consumer ceremonies per capability is far too heavy for August's FastAPI + Tauri stack.
- **Generated doc catalogs + i18n triplets** (config-catalog 5,427 lines; tool-catalog 2,862; persistence-catalog 9,447; every doc ×3 languages) — enormous maintenance surface August doesn't need.
- **UI machinery sprawl**: 244-line README just for chat scroll/fold behavior; August's minimalist Claude-Desktop-style UI should not grow per-turn folding modes ×5.
- **No turn budget**: dsh relies on an advisory reminder only; August's 150-round cap + world-delta backstop is the stronger design — keep it.
- **Everything is a session event** (even inbox splices) — durable but heavy; August's simpler transcript model is fine.
- **Shipping language runtimes** (Python + Node + pnpm) inside the desktop app — August already bundles Python; don't add Node.

## Not applicable to August (and why)

- **Cordis plugin framework, profiles, HMR config reload** — different architecture; August has no plugin runtime.
- **ACP / Codex / Claude Code subagent bridges** — third-party CLI dependencies don't fit the local-first bundled product.
- **SSH execution world** (`fs-ssh`, `subprocess-ssh`, `sandbox-ssh`) and **MCP manager** — out of August's current scope.
- **OTel telemetry, product analytics, Feishu mandatory-update policy, staged rollout** — server-fleet concerns; August updates are direct-download.
- **Multi-user web deployment with roles** (`apps/web` as a hosted service) — August is single-user local.
- **browser-use / computer-use providers** — August has no desktop-automation surface today.

## Coverage ledger

**Read (docs):** README.md; AGENTS.md; docs/architecture.md; docs/agent-lifecycle.md; docs/capability-seams.md (full); docs/cordis-primer.md; docs/tool-execution-pipeline.md; docs/defensive-patterns/README.md (+ index of 5 files).
**Read (packages):** core/agent-loop (README + src/agent.ts head); compaction group + compaction-basic README; skill group + skill README + src/index.ts head + skill-filesystem README; guard group + repeat-tool-reminder + timeout-policy; sandbox group README; subagent group README; spill group + README + spill-policy README; llm group README; settings/settings README; credentials group README; context/session-reference README; client/ui-chat README; session/session-persistence-jsonl README.
**Read (apps):** apps/desktop/README.md (full); apps/desktop/publishing/README.md; apps/desktop-publisher/README.md.
**Skipped (with reason):** docs/config-catalog.md, docs/tool-catalog.md, docs/persistence-catalog.md — generated references (thousands of lines); sampled via headers/grep only. docs/subsystems/*, docs/user/*, docs/cookbook/* — usage docs, redundant with package READMEs. All remaining ~55 packages' src, python/, native/, benchmarks/, snapshots/, website/, vendor/ (cordis source), tests, apps/cli, apps/web src (thin entry) — out of scope for the 13 dimensions; the 5 most relevant seams were read at source level instead.
