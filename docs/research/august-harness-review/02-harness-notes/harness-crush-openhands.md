# Harness reference study: Crush (Go TUI) + OpenHands Agent Canvas (TS)

Read-only study of `repos/crush` (charmbracelet/crush) and `repos/OpenHands`.
VERIFIED = file read; INFERRED = docs/names only. Paths are repo-relative.

## A. CRUSH

Go, Bubble Tea v2 TUI, embedded POSIX shell (`mvdan.cc/sh`), SQLite via goose
migrations, provider abstraction = **Catwalk** (model catalog) + **fantasy**
(provider SDKs). `internal/server` exposes versioned REST + **SSE** (`/v1/...`)
so several TUIs can share one workspace. VERIFIED `internal/app/app.go:1-100`,
`internal/server/endpoints.go`, `internal/db/migrations/`.

**(1) Startup** — `internal/app/app.go` hand-wires every service into one `App`
struct (sessions, messages, history, permissions, questions, filetracker,
pinentry, `AgentCoordinator`, `LSPManager`, `skills.Manager`, config store, hook
runner, update check, herdr). No DI container. Config from `crushrc` (Bash) or
deprecated `crush.json`, both shell-expanded at load — documented as trusted code.
Boots into an update check + Catwalk catalog sync (`CRUSH_DISABLE_PROVIDER_AUTO_UPDATE`).
VERIFIED app.go:59-100, README.md:252-333, 952-1004.

**(2) UI/UX** — per-tool renderers in `internal/ui/chat/` (`bash.go`,
`diagnostics.go`, `lsp_restart.go`, `mcp.go`, `search.go`); dialogs in
`internal/ui/dialog/` (model picker, `permissions.go`, command palette, file
picker, MCP toggles, per-provider OAuth `oauth_{copilot,openai,hyper,grok}.go`,
GPG `pinentry.go`, `plan_handoff_inline.go`). Notifications only when the terminal
is unfocused + supports focus reporting. Error copy names the escape hatch
(`yoloBlockedMessage`, `tools/bash.go:326-329`). VERIFIED dir listings,
README.md:200-201, 701-713.

**(3) Memory** — **no long-term memory store.** `grep -rn "memory|Memory"
internal/` returns only log buffers, token windows, and an in-memory Docker MCP
config. Cross-session context = markdown injected into the system prompt:
`~/.config/crush/CRUSH.md` + `~/.config/AGENTS.md`, `option global-context-path`,
plus repo files (`AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `GEMINI.md`,
`crush.md`…). `initialize-as` generates an `AGENTS.md` by analyzing the codebase.
Skill "usage tracking" is per-session, in-memory, name-based (`skills/tracker.go`).
VERIFIED README.md:540-563, 715-730, config.go:30-50, tracker.go:1-86.

**(4) Skills / extensions** — Agent Skills standard: folder + `SKILL.md`, frontmatter
`name, description, user-invocable, disable-model-invocation, license,
compatibility, metadata`. Discovery = embedded builtins (`go:embed builtin/*`,
synthetic `crush://skills/` prefix) + global dirs + project dirs
(`.agents/skills`, `.crush/skills`, `.claude/skills`, `.cursor/skills`) →
`Deduplicate` → `Filter(DisabledSkills)`. Three slices per workspace: `allSkills`
(pre-filter), `activeSkills` (post-filter), `states` (per-file diagnostics).
Builtins include `crush-config` and `crush-hook` — the agent can reconfigure
Crush and write its own hooks. Extensions = MCP only (`stdio`/`http`/`sse`,
per-tool `--disabled-tools`, OAuth, `sessionless: true`). UI labels skills
`system|user|project`.
VERIFIED skills.go:22-50, manager.go:13-23, 165-195, embed.go:1-79, catalog.go:8-16,
README.md:255-256, 420-499, 616-699.

**(5) Tool + permission** — modes `Normal` / `Yolo` (skip prompts, still refuse
dangerous) / `Sysadmin` (skip prompts, refuse nothing), via two predicates
`SkipsPrompts()` / `RefusesDangerous()`. Grant scope:
`PermissionKey{SessionID, ToolName, Action, Path, Grant}` where **`Grant` holds
the bash command**, because `Path` there is only the cwd — otherwise approving
`ls` for the session silently approves every later `sudo` in that dir.
`GrantPersistent` records the grant only if it *wins the pending race*, so a losing
grant can't flip later denies to allows. Resolutions are recorded against the
request the server issued, not the copy a client echoes back (only the
unguessable single-use ID is trusted). Static allowlist `tool` or `tool:action`;
`bash` auto-runs a parsed inert set (`ls`, `pwd`, `git status`, `git log`), and
redirection / assignment / substitution / pipeline / mutating arg falls back to a
prompt. `PreToolUse decision:allow` stamps `WithHookApproval(ctx, toolCallID)` and
short-circuits the prompt while still publishing an audit notification.
VERIFIED permission.go:15-35, 37-57, 154-167, 217-273, 292-309, 402-417,
README.md:574-604.

**(6) Sandboxing** — **no OS sandbox**; "sandbox" = dangerous-command blocklist +
permission modes + hooks as deterministic policy. Blocklist defaults (configurable)
include `curl`, `wget`, `ssh`, `scp`, `sudo`, `su`, `doas`, `env`, `eval`,
`xargs`, `nohup`, package managers, `crontab`, `at`. 13 hook events:
`PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`,
`PermissionResult`, `SessionStart`, `SessionEnd`, `TurnStart`, `TurnEnd`,
`StopFailure`, `Interrupt`, `PreCompact`, `PostCompact` — tool/turn events fire
**only for the top-level agent** (subagents fire none, so `TurnEnd` speaks once
per prompt). Hooks run in parallel but compose in **config order** (deny > allow >
none; context concatenates; `updated_input` shallow-merges later-wins; denied/
halted patches discarded), and are applied **before** permission checks so a deny
never prompts. `exit 2` blocks a call, `exit 49` halts the turn.
VERIFIED docs/hooks/README.md:90-127, 170-209, 326-336, 415-524, 934-964,
hooks/runner.go:67-97, tools/bash.go:77-140.

**(7) Context / compaction** — auto-summarize when
`remaining = cw - (prompt+completion)` falls under threshold: 20k buffer if
`cw > 200k`, else 20% of `cw`; **`cw == 0` skips summarizing** so custom/local
models aren't truncated instantly. Manual `/compact` and auto share
`summarize(..., trigger)`; a `PreCompact` hook blocks it, and for an auto-compact
that ends the turn (`ErrCompactionBlocked`) rather than looping. Compacted sessions
send only the tail. Loop detection is a stop condition
(`hasRepeatedToolCalls`).
VERIFIED agent.go:56-63, 1215-1236, 1337-1350, 1489-1529, 1869, loop_detection.go.

**(8) Subagents** — tools `agent_dispatch` (create) / `agent_send` (message) /
`agent_stop` (stop). Params: `prompt`, `label` (returned as the addressable name),
`access` = `read`(default)|`write`, plus `model`/`provider`/`reasoning` override
validated against `UsableModels()` with an explicit refusal string when unservable.
Subagents are real child sessions (`CreateTaskSession`), inherit auto-approval from
the parent only, run detached with a `backgroundAfter` threshold, and their report
is written to the transcript **when delivered** (folds into an in-flight turn or
lands as a later turn). Built-in agents: `coder`, `task` (read-only), `plan` (no
writes), each with its own tool resolver.
VERIFIED agent_dispatch.go:21-47, 856-861, agent_dispatch_model.go:26-80,
coordinator.go:178-192, 364-366, permission.go:143-147, config.go:782-793, 1156-1185.

**(9) Model / provider** — Catwalk catalog auto-updates at startup, disableable
(air-gapped) or repointed via `CATWALK_URL`; `crush update-providers embedded`
resets to the build-time copy. Wire format is a first-class provider field:
`responses` (OpenAI routing) / `completions` (OpenAI-compatible) / `messages`
(Anthropic-compatible), legacy values migrate. ~25 env-var providers + OAuth for
Copilot/OpenAI/Hyper/Grok. Bedrock SSO expiry → `aws_auth_refresh` runs on
credential error and **retries in place** (no duplicate messages, no restart).
VERIFIED README.md:204-237, 750-764, 809-848, 952-1004, aws_sso_refresh.go.

**(10) Update** — check-only, not self-updating: `GET api.github.com/.../releases/
latest` → `Available()` (pre-release aware; devel/dirty/`go install` pseudoversions
treated as development). TUI shows a banner; the user runs brew/npm/winget/scoop/apt.
VERIFIED update.go:14-73.

**(11) Config** — `crushrc` is "just Bash with Crush-specific builtins" (`provider
add`, `model add`, `mcp add`, `lsp add`, `permissions allow`, `hook add`, `option`),
priority `./.crushrc` > `./crushrc` > `~/.config/crush/crushrc`, executing in a
full shell. Global JSON config vs project `.crush` data dir are separate; ephemeral
state (`%LOCALAPPDATA%\crush\crush.json`, `~/.local/share/crush/crush.json`) is
explicitly *not* configuration. Atomic writes + Windows file-retry helpers.
VERIFIED README.md:258-333, config/load.go:1380-1398, config/atomicwrite.go.

**(12) Error recovery** — permission `Cancelled` outcome when a turn is cancelled
with a prompt open (and `PermissionResult` always follows `PermissionRequest`);
`settleTurn` answers abandoned tool calls on a **detached** context with a 5s
budget; queued runs carry a caller `RunID` and completion is an authoritative
broker event rather than inferred from message finish parts; `StopFailure` hook on
provider error, `Interrupt` on ctrl-c.
VERIFIED permission.go:380-399, agent.go:84-131, 1245-1259, settle.go,
docs/hooks/README.md:199-204.

**(13) Clever / bad** — Clever: `GrantKey` (command-scoped bash grant); config-order
hook composition over parallel execution; `crush://skills/` synthetic prefix;
`PreCompact` refusal that deliberately ends the turn; provider *wire format*
separated from provider *identity*; three-slice skill model with per-file
diagnostics published as events. Bad: `crushrc`/`crush.json` execute shell at load
(including `$(...)`), so a repo-local `.crushrc` in a directory you `cd` into is
code execution — directly against August's "repo instructions load, repo execution
does not"; no memory record at all; catalog auto-update silently mutates user
config; in-process hooks get only ~1s grace before abandonment, so a broken hook
reports "no opinion" instead of surfacing.

## B. OPENHANDS AGENT CANVAS

React/TS (Vite) + Electron shell, published as `@openhands/agent-canvas`. **This
repo owns only the UI**: agent server (Python SDK, tools, events, REST/WS contract,
typed client) is `OpenHands/software-agent-sdk` (not cloned); skills are
`OpenHands/extensions`; automations are `OpenHands/automation`. Normal dependency
direction: contract → TS client → Canvas; "Do not reimplement Agent Server
endpoints or contracts in Canvas." VERIFIED AGENTS.md ownership table + checklist.

**(1) Startup** — `electron/main.mjs` launches the whole local stack (agent-server +
automation via `uvx`, static frontend, ingress proxy) then opens a BrowserWindow
once ingress is ready, with a loading screen while backends boot; bundled `uv`/`node`
per-arch with a legacy flat-dir fallback. Version handshake: `config/defaults.json`
pins `agentServer 1.62.0`, `agentCanvas 1.40.0`, `automation 1.32.1`, and
`compatibility.minimumAgentServer = 1.59.0`, read by
`src/api/agent-server-compatibility.ts` which raises typed
`AGENT_SERVER_UNSUPPORTED_VERSION` / `AGENT_SERVER_UNKNOWN_VERSION`. Renderer base
URL = `VITE_BACKEND_BASE_URL` → `window.location.origin`; session key from
`VITE_SESSION_API_KEY` or serve-time `window.__AGENT_CANVAS_SESSION_API_KEY__`.
VERIFIED electron/main.mjs:1-40, defaults.json:1-40, agent-server-config.ts:98-139,
agent-server-compatibility.ts:21-62.

**(2) UI/UX** — chat surface `src/components/features/chat/`: `chat-interface.tsx`,
`interactive-chat-box.tsx`, `chat-status-indicator.tsx`, `goal-status-banner.tsx`,
`plan-preview.tsx`, `conversation-startup-progress.tsx` ("show server-reported
progress without guessing completed or skipped steps"), `connection-recovery-
notice.tsx`, `skill-install-restart-banner.tsx`, `git-control-bar*`,
`change-agent-button.tsx`. Event rendering `conversation-events/chat/`
(`event-message.tsx`, `group-events.ts`, `step-disclosure-context.tsx`,
`task-tracking/`, `tool-visualizers/`): a running action is replaced by its
observation and both share the tool-call key, so disclosure state lives *above* the
row and survives the swap. Panes: right panel tabs `files|commits|browser|terminal|
planner|tasklist|usage`, modes `code|plan`, per-tab state in a zustand store
persisted to localStorage. Long-op feedback is staged: `use-connection-error-grace-
period.ts` → "reconnecting" at 30s, "retryable" at 60s, and explicitly *not* "the
agent failed". VERIFIED dir listings, step-disclosure-context.tsx:1-30,
conversation-store.ts:1-45, use-connection-error-grace-period.ts:1-45.

**(3) Memory** — frontend memory is persisted **conversation UI state only**: zustand
stores (`conversation-store`, `event-store`, `metrics-store`, `conversation-state-
store`, `browser-store`, `goal-store`, `error-message-store`), localStorage drafts,
react-query caches. Server-side memory/knowledge is not in this repo. AGENTS.md
mandates "one named owner and one obvious writer" for durable frontend state plus
explicit hydration/migration. Skill/knowledge sources are typed
`"repo" | "knowledge" | "agentskills"` (server distinguishes repo docs from skill
packages). VERIFIED conversation-websocket-context.tsx:1-100, AGENTS.md,
agent-server-conversation-service.types.ts:260-270.

**(4) Skills / plugins** — public skills from `@openhands/extensions`; contributor
guidance from `.agents/skills/` = `custom-codereview-guide.md`, `desktop-electron/`,
`e2e-testing/`, `frontend-api-contracts/`, `frontend-development/`,
`local-stack-runtime/`, `pr-design-doc/`, `release.md`, `telemetry-analytics/`,
`verify-openhands/` — each a dir with `SKILL.md` + `references/guide.md`. Hard
process rule: **verification skills are read-only tools** for implementor agents,
maintained in separate PRs/contexts so a fix never edits its own spec. Skills UI:
`components/features/skills/` (`skill-card.tsx` enable toggle + copy source + pills,
`skill-detail-modal.tsx`, `skill-filters-modal.tsx`, `add-skill-modal.tsx`,
`skill-facet-rail.tsx`) plus `skill-install-restart-banner.tsx` — installing a skill
needs a server restart, and the UX says so rather than half-working. Plugins and
Canvas extensions are separate services from skills (`plugins-service.ts`,
`plugins-management-service.ts`, `canvas-extensions-service.ts`; sources
`github:|https:|git:|file:|user@host:`; `canvas_app_backend_bridge_v1` capability).
Hooks: `hooks-service.ts` → `POST /api/hooks`; server checks
`<project_dir>/.openhands/hooks.json` literally and never walks parents; 5s timeout
on the launch critical path, graceful `null` for cloud/old/unseeded backends.
VERIFIED dir listings, frontend-api-contracts/SKILL.md:1-25, AGENTS.md, hooks-service.ts:1-30.

**(5) Tool + permission** — server-owned; Canvas only surfaces status.
`ExecutionStatus → AgentState`: `IDLE→AWAITING_USER_INPUT`, `RUNNING`,
`PAUSED`, `WAITING_FOR_CONFIRMATION→AWAITING_USER_CONFIRMATION`, `FINISHED`,
`ERROR`, `STUCK→ERROR`. Confirmation mode is a UI concept
(`confirmation-mode-enabled.tsx`, `use-conversation-permissions`); the UI can gate
actions but cannot grant tools. Secret requests are a first-class event
(`isRequestSecretActionEvent` → `handleRequestSecretAction`), so a tool asks for a
credential through an event instead of reading the environment.
VERIFIED use-agent-state.ts:1-40, conversation-websocket-context.tsx:46-58.

**(6) Sandboxing** — runtime is a server capability:
`execution_runtime: "local" | "docker"`, `available_conversation_runtimes`,
threaded into workspace selection. The frontend never executes. Container internals
live in the SDK repo — **not examined**.
VERIFIED agent-server-compatibility.ts:35-40, agent-server-adapter.ts:261-264.

**(7) Context / compaction** — server-side compaction surfaced as a user action:
`useCompactContextAction` (shared by the Usage panel CTA and the composer
context-window popover) → `useCondenseConversation`, disabled while the agent is
busy or already compacting, with before/after token counts and
`useAwaitContextCompaction` to wait. Usage is displayed from server metrics
`per_turn_token` / `context_window` (`<= 0` = unknown, hide the meter). The WS
event stream is render-batched (`createStreamingDeltaBatcher`) with a `seq` cursor
for resume/ordering. VERIFIED use-compact-context-action.ts:1-45,
use-context-window-usage.ts:1-40, conversation-websocket-context.tsx:51-67.

**(8) Subagents** — child conversations are a product feature:
`launch-child-conversation-client-tool.ts`, `use-create-automation-setup-conversation.ts`,
`handleLaunchChildConversationAction`; a parent can spawn a helper conversation
(e.g. a local planner) whose run/idle transitions are tracked separately from the
main conversation's. Agents are pluggable at the server boundary via **ACP**
(OpenHands, Claude Code, Codex, Gemini, Pi, OpenCode) — `src/api/acp-service/`.
Server-side delegation loop — **not examined** (SDK repo).
VERIFIED use-agent-state.ts:44-52, conversation-websocket-context.tsx:53-58,
README.md:1-20.

**(9) Model / provider** — frontend is provider-agnostic; LLM profiles are server
settings (`use-llm-profile*`, `llm-balance-service.ts`, `llm-subscription-service.ts`).
The model can switch itself and the UI renders it as an observation
(`isSwitchLLMObservationEvent`, `isClassifyAndSwitchLLMObservationEvent`). Agent
identity is swappable at runtime (`change-agent-button.tsx`, `use-active-agent-profile.ts`,
`agent-profiles-service/profile-field-support.ts`).
VERIFIED conversation-websocket-context.tsx:40-41, src/hooks/ listing.

**(10) Update** — **check-only, print the command**:
`fetchLatestAgentCanvasVersion()` hits the npm `latest` dist-tag (CORS-open) and the
settings card shows literal commands (`npm install -g @openhands/agent-canvas@latest`
or `docker pull ghcr.io/openhands/agent-canvas:latest`). No in-app updater; Electron
main does **not** use `autoUpdater`. Release automation is release-please with
hand-editing forbidden.
VERIFIED agent-canvas-updates.ts:1-45, AGENTS.md, grep of electron/.

**(11) Config** — `config/defaults.json` is the single source of truth for version
pins, ports, paths, defaults across npm + Docker, read by `scripts/dev-safe.mjs`,
`scripts/dev-with-automation.mjs`, `docker/entrypoint.sh` (via generated
`defaults.env`), and CI. Ports: agentServer 18000, automation 18001, proxy 8000,
vscode 8001. State: `agent-canvas/`, `agent-canvas/conversations`,
`agent-canvas/bash_events`, `automation/automations.db`. Base paths `/canvas`,
`/vscode`. Backends are a registry (`backend-registry/active-store`, `/types`) with
an explicit NO_BACKEND sentinel so an unseeded registry shows onboarding instead of
throwing. VERIFIED defaults.json:1-40, hooks-service.ts:20-25.

**(12) Error recovery** — `AgentServerUnavailableError` carries
`noBackendConfigured` so the UI distinguishes "no backend added" from "backend
down", each with its own copy. Connection loss → grace-period state machine +
`usePageVisibilityReconnect` + `connection-recovery-notice.tsx`. Compatibility is
fail-loud with dedicated error codes. Startup shows server-reported progress with
retry/edit affordances. Two frontend rules prevent whole bug classes: "one named
owner and one obvious writer" for durable state; raise `minimumAgentServer` when
Canvas starts depending on new server behavior.
VERIFIED agent-server-compatibility.ts:45-61, use-connection-error-grace-period.ts,
conversation-startup-progress.tsx:20-30, AGENTS.md.

**(13) Clever / bad** — Clever: `minimumAgentServer` as a *versioned floor* in one
shared JSON with typed errors, instead of runtime feature-probing scattered around;
the **served-origin-only** absolute working dir (`isServedOriginHost`) — the same
registry entry can point at a remote backend, so a baked path must be tied to the
host, not the stable id; read-only verification skills as an enforced *process* rule;
30s/60s staged connection feedback; secrets as a first-class event; skill install →
restart banner. Bad: **no in-app update path at all** for a desktop app (users get
shell commands); the repo split makes memory/tools/compaction/delegation/sandbox
unobservable from this repo, so the ownership table is load-bearing and drift is only
caught in review; `/vscode` prefix-as-isolation is acknowledged as not isolation
(shared origin + localStorage, issue #16492); localStorage-only drafts lose work on
cleared storage or another device; the skills surface grew many small components
(`skill-facet-rail`, `skill-filters-modal`, `extensions-mobile-hub`) for one concept.

## C. Ideas worth stealing for August

1. **Scope a "don't ask again" grant to the command, not the directory.**
   Crush `internal/permission/permission.go:154-167` — `GrantKey` holds the bash
   command because `Path` is only the cwd. One struct field removes the most
   dangerous silent escalation in a guard-mode system. → August guard-mode
   permission store.
2. **Guard levels as named policies with orthogonal booleans, not a scale.**
   `permission.go:37-57` (`SkipsPrompts()` vs `RefusesDangerous()`): "stop asking"
   and "stop allowing" are different questions. → August's 4 guard modes.
3. **Hooks as the deterministic policy layer.** `docs/hooks/README.md:90-127, 507-524`
   — parallel run, config-order composition, `deny > allow > none`, applied *before*
   the prompt so a deny never prompts. → August's tool loop + guard modes.
4. **`exit 49` for "halt the turn", distinct from "block this call" (2).**
   `docs/hooks/README.md:430-444` — placed in an exit-code gap so it can't be hit
   accidentally. Cheap to adopt in any script-hook design.
5. **Show why a skill didn't load.** `internal/skills/manager.go:13-23, 165-195` —
   `allSkills`/`activeSkills`/`states` per file, published as events. → August's
   SKILL.md loader/diagnostics.
6. **Builtin skills through a synthetic path.** `internal/skills/embed.go:1-79`
   (`crush://skills/<name>/SKILL.md`) keeps one discovery path for embedded and disk
   skills. → August skills UX.
7. **A versioned compatibility floor in one JSON.** OpenHands `config/defaults.json:7-9`
   + `src/api/agent-server-compatibility.ts` — `minimumAgentServer` + typed
   `AGENT_SERVER_UNSUPPORTED_VERSION`. → August's 9-version-source desktop↔backend
   handshake.
8. **Distinguish "no backend configured" from "backend unreachable" in copy.**
   `agent-server-compatibility.ts:45-61`. In a local-first app the backend may just
   not have started; one class, two messages.
9. **Stage the "is it stuck?" feedback.** `use-connection-error-grace-period.ts` —
   30s "reconnecting", 60s "retryable", never "the agent failed". August's budgeted
   loop already knows the elapsed time.
10. **Deterministic turn settlement on a detached context.**
    `internal/agent/agent.go:1245-1259` + `settle.go` — answer abandoned tool calls on
    a context that outlives the cancelled run, 5s budget. → August's turn loop after
    an interrupt.

Bonus: **served-origin-only absolute paths** (`agent-server-config.ts:213-258`) — if
August ever accepts an absolute working dir from the UI, tie it to the serving host.

## D. Anti-patterns to avoid

- **Shell at config load.** Crush's `crushrc` (and `$(...)` in `crush.json`) executes
  on load in whatever directory you `cd` into (`README.md:328-333`). August draws the
  line correctly: repo instructions load, repo execution does not. Never let a
  project-local config run code.
- **Silent catalog mutation of user config.** Crush rewrites the provider catalog into
  the user's config unless disabled; treat remote catalogs as read-only with explicit apply.
- **Check-only updates for a desktop app.** OpenHands ships no `autoUpdater` and the
  README's update answer is a shell command. August's auto-download + user-clicks-restart
  is strictly better — don't regress.
- **Path prefix as isolation.** `/vscode` sharing origin + localStorage with the canvas
  is acknowledged as not isolation (`defaults.json:29`). Shared origin = shared storage.
- **A numeric scale where a set of policies belongs** (crush's three modes exist because
  "how much permission" is really two questions).
- **Silent skill drop.** Name-only, override-blind usage tracking
  (`skills/tracker.go:10-14`) is a debugging trap.
- **Ownership drift across a repo split.** OpenHands needs a whole table because frontend
  and backend are separate repos; make August's desktop↔backend contract a generated
  artifact (OpenAPI → typed client) so drift is a build failure, not a review comment.

## E. Not applicable to August (and why)

- **Bubble Tea TUI renderers / `internal/ui/*`** — August is a webview UI. Patterns
  (per-tool rendering, escape-hatch copy) transfer; the code does not.
- **`crushrc` Bash config DSL** — August has typed config + a desktop settings UI; a
  shell DSL in a desktop product is a liability.
- **Multi-client workspace sharing over SSE with first-wins flags** (`README.md:506-538`)
  — single-user local desktop app is a different product shape.
- **Catwalk / fantasy provider SDKs** — August has its own provider layer and the
  "base URL used exactly as pasted, never invent `/v1`" rule. The auto-updating model
  catalog idea is worth a look; the Go libraries aren't portable.
- **Electron main-process stack launching + uvx bundling + in-container VS Code**
  (`electron/main.mjs`) — Tauri; no process/ingress/uvx equivalent.
- **Agent Server / SDK / ACP boundary** — OpenHands assumes a separate Python server over
  HTTP/WS. August's FastAPI backend is in-process, so the compatibility-floor *pattern*
  applies but the split and ACP pluggability don't.
- **PostHog telemetry, DigitalOcean Managed Agents, automation scheduling**
  (`defaults.json:33-40`) — cloud/ops features with no local-first analog.

## F. Coverage ledger

**Read substantially:** crush `README.md`, `docs/hooks/README.md`, `permission/
permission.go`, `app/app.go` (wiring), `agent/agent.go` (stop conditions, summarize,
settle), `agent/coordinator.go` (head), `agent/agent_dispatch{,_model}.go`,
`agent/tools/bash.go`, `skills/{skills,manager,tracker,catalog,embed}.go`,
`update/update.go`, `session/session.go` (head), `db/connect.go` + migrations,
`config/{config,load}.go` (selected), `hooks/runner.go`, `server/endpoints.go`,
`lsp/manager.go` (head), `question/question.go` (head). OpenHands `AGENTS.md`,
`README.md` (head), `config/defaults.json`, `src/api/{agent-server-config,
agent-server-compatibility,agent-canvas-updates,canvas-extensions-service,
hooks-service}.ts` (heads), `src/contexts/conversation-websocket-context.tsx`,
`src/hooks/{use-agent-state,use-compact-context-action,use-context-window-usage,
use-connection-error-grace-period}.ts`, `src/stores/conversation-store.ts` (head),
skills UI listing + `skill-card.tsx`, `step-disclosure-context.tsx`,
`features/chat/` listing, `electron/main.mjs` (head),
`.agents/skills/frontend-api-contracts/SKILL.md`, dir listings of `src/{api,hooks,components}`.

**Skipped, with reason:**
- crush `internal/ui/**` impls, `internal/backend/`, `internal/server/*` beyond
  endpoint names — TUI/transport specific; behavior taken from docs + service interfaces.
- crush `internal/llm/` and `fantasy`/`catwalk` — vendored provider code, not harness design.
- crush `config/{catwalk,hyper,copilot}.go`, oauth pkgs — auth plumbing (README covered the surface).
- OpenHands `software-agent-sdk` (not cloned), `OpenHands/extensions` — outside the
  workspace; memory/tools/delegation internals therefore **not examined**.
- OpenHands `features/{backends,browser,settings,automations,sidebar,home,onboarding}`
  — cloud/automation/settings UI, out of scope.
- OpenHands `tests/`, `playwright.*`, `helm/`, `docker/`, `scripts/` — CI/deploy plumbing;
  claims come from source, not tests.
- crush `*_test.go` — read names only, for the invariants the authors cared about
  (permission race, dispatch race, interrupted/unfinished tool calls, queued run IDs).

**Not examined at all:** crush `internal/{diff,csync,event,history,filetracker,shell,
shellconfig,sshaskpass,workspace,projects,pubsub,pinentry}` internals; OpenHands
`src/extensions/`, `src/api/{cloud,acp-service,conversation-service,event-service}`
internals, `src/services/`, `src/types/`.