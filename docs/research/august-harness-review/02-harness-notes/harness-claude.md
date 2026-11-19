# Claude Code / Claude Desktop — observed behaviour (docs only)

**Source class: OBSERVED BEHAVIOUR, NOT CODE.** Anthropic publishes no source for
either product. Every claim below is drawn from the public documentation
(`code.claude.com/docs`, fetched 2026-11-14) and is marked **DOC** where the page
states it. Nothing here is verified against source, and nothing was run.

Pages read (fetched as `.md`, full text stored in
`C:/Users/rober/AppData/Local/Temp/august-review/claude-docs*.json`):
`how-claude-code-works`, `memory`, `context-window`, `prompt-caching`,
`settings`, `hooks`, `skills`, `sub-agents`, `permissions`,
`permission-modes`, `sandboxing`, `desktop`, `tools-reference`.

## 1. Startup flow

- CLI: `claude` in a project directory. Before the user types anything, context
  loads: CLAUDE.md (+ AGENTS.md if the repo uses it), auto memory, MCP tool
  **names**, skill **descriptions**, output style, and `--append-system-prompt`
  text (**DOC** `context-window`).
- The layer around the model — tools + context management — is what the docs
  call "the agentic harness" (**DOC** `how-claude-code-works`).
- Permission mode is chosen per surface (CLI Shift+Tab, VS Code indicator,
  Desktop selector) and can change mid-session (**DOC** `permission-modes`).

## 2. UI/UX patterns

- Terminal-first: the transcript is the interface. Tools render as
  expandable blocks; permission prompts appear inline at the call site with the
  action quoted, then options.
- Desktop app: prompt box, sessions list, suggested prompts, plan approval
  surface, permission-mode selector (**DOC** `desktop`).
- The loop is described in three phases — **gather context, take action,
  verify results** — and the user is explicitly part of the loop (interrupt and
  steer) (**DOC** `how-claude-code-works`).

## 3. Memory model — two systems, both re-injected after compaction

| | CLAUDE.md | Auto memory |
|---|---|---|
| Who writes | the user | Claude itself |
| Contents | instructions and rules | learnings, corrections, preferences |
| Scope | managed / user / project / local | per repository, shared across worktrees |
| Loaded | every session | every session (first 200 lines or 25 KB) |
| After compaction | re-injected from disk | re-injected from disk |

(**DOC** `memory`, `context-window`.) A 5-tier load order runs broad → narrow:
managed policy → user → project → local → nested (**DOC** `memory`).
`AGENTS.md` loads in place of `CLAUDE.md` (**DOC** `memory`).
Path-scoped rules live in `.claude/rules/` and load on demand, not up front
(**DOC** `memory`, `context-window`).
The docs state memory is "context, not enforced configuration" — blocking an
action needs a PreToolUse hook instead (**DOC** `memory`). That sentence is the
single most transferable idea on this page.

## 4. Skills

- A skill is a directory with `SKILL.md`; frontmatter carries the trigger. The
  **body loads only when the skill is used**, so long reference material is
  cheap until needed; the description is always in context (**DOC** `skills`).
- Custom commands were merged into skills: `.claude/commands/deploy.md` and
  `.claude/skills/deploy/SKILL.md` are the same thing (**DOC** `skills`).
- After compaction: skill descriptions are **rebuilt from the current skills
  directory** and re-injected; invoked skill bodies are re-injected, capped at
  **5,000 tokens per skill / 25,000 total, oldest dropped first** (**DOC**
  `context-window`). August already implements this exact budget — it is the
  same numbers, reached independently.

## 5. Tool + permission model

Six modes, each a different convenience/oversight trade (**DOC**
`permission-modes`):

| Mode | Runs without asking |
|---|---|
| `default` (named "Manual") | reads only |
| `acceptEdits` | reads, file edits, common filesystem commands |
| `plan` | reads (+ classifier-approved commands when auto is available) |
| `auto` | everything, with background safety checks |
| `dontAsk` | reads in working dirs + pre-approved tools; **anything that would prompt is denied** |
| `bypassPermissions` | everything (isolated containers/VMs only) |

- Per-tool-type approval table: read-only tools need no approval inside the
  working directory; Bash needs approval except a built-in read-only command
  set; file modification needs approval "until session end"; WebFetch except
  pre-approved doc domains; WebSearch always (**DOC** `permissions`).
- "Yes, and don't ask again" is **permanent per repository and command** for
  Bash, per repository and domain for WebFetch, per repository for WebSearch,
  and **until session end** for file modification (**DOC** `permissions`) — a
  deliberately narrower grant for the most dangerous class.
- **auto mode uses a second model as a classifier** that reviews actions instead
  of the user (**DOC** `permission-modes`). Blocked-by-default list includes
  `curl | bash`, secret exfiltration, production deploys/migrations, force
  push, irreversible destruction of pre-session files, `git reset --hard`
  (**DOC** `permission-modes`).
- The classifier **trusts the working directory and the remotes configured at
  session start**; a remote added or repointed mid-session is *not* trusted
  (**DOC** `permission-modes`). That is a concrete, cheap rule August can copy
  for its own trust decisions.
- `plan` mode is a first-class mode with an explicit approve-the-plan step and
  can be the default (**DOC** `permission-modes`) — August's plan level is
  close but has no approval artifact.

## 6. Sandboxing

- Off by default; `/sandbox` or `sandbox.enabled` (**DOC** `sandboxing`).
- Writes: working directory + per-user temp + added directories; **protected
  paths stay write-denied even when sandboxed**. Reads: most of the machine,
  including credential files. Configured by
  `filesystem.allowWrite` / `filesystem.denyWrite` (**DOC** `sandboxing`).
- The docs name what runs outside the sandbox (**DOC** `sandboxing`) — an
  explicit, documented hole list rather than an implicit one. August's equivalent
  is the `.ssh` public-member allowlist.

## 7. Context management / compaction

- `/compact` replaces history with a structured summary; the docs publish a
  **per-mechanism survival table**: system prompt and output style survive;
  project CLAUDE.md, unscoped rules, auto memory and the plan are re-injected
  from disk; skill descriptions are rebuilt; nested CLAUDE.md and path-scoped
  rules reload on demand; **files Claude read are re-read (up to five, most
  recently modified first)**; invoked skill bodies are re-injected under the
  5k/25k caps; background commands and background subagents keep running and
  Claude is reminded which ones (**DOC** `context-window`).
- Prompt caching is prefix-exact: the whole previous request is the cached
  prefix, a change anywhere recomputes everything after it, and there is no
  per-file or per-segment cache (**DOC** `prompt-caching`). This is the invariant
  August's "frozen boot index" and "no mid-conversation system-prompt rewrite"
  rules protect.
- Subagents exist partly for context: a subagent's large file reads stay out of
  the parent's window; only a summary plus a small metadata trailer return
  (**DOC** `context-window`).

## 8. Subagent design

- Defined as markdown files with frontmatter (name, description, model, effort,
  tools), at user or project scope (**DOC** `sub-agents`).
- Automatic delegation is described, plus explicit invocation; foreground and
  background runs; named subagents; a **concurrent subagent limit**; a section
  on "let subagents spawn their own subagents"; subagent output scanning; and
  "choose between subagents and the main conversation" (**DOC** `sub-agents`).
- Subagents can have their **own auto memory** (**DOC** `memory`) — the opposite
  of August's rule that subagents never write memory. Worth noting as a
  deliberate divergence, not a gap.
- Extended thinking and effort level are per-subagent frontmatter fields (**DOC**
  `sub-agents`).

## 9. Model / provider handling

- Claude-only: Sonnet for most coding, Opus for architectural reasoning,
  switchable with `/model` (**DOC** `how-claude-code-works`). No third-party
  provider story — August's provider-agnostic gateway is strictly broader.

## 10. Update mechanism

Not documented on the pages read; Claude Code ships as an npm package with a
version check and the desktop app has its own updater, but no page read states
the mechanism. **NOT EXAMINED** — no claim made.

## 11. Config / settings

- Layered settings files with a scope comparison table (user / project / local /
  managed) and an explicit "who they affect" section (**DOC** `settings`).
- Settings can be checked into version control so a team shares one policy while
  each developer customizes their own (**DOC** `permissions`).
- `sandbox.enabled`, `defaultMode`, `includeGitInstructions` are the keys seen
  named across pages (**DOC** `settings`, `sandboxing`, `context-window`).

## 12. Error recovery

- Hooks have a **failure cadence of their own**: `PostToolUseFailure`, `StopFailure`
  (**DOC** `hooks`) — a failure is an event with a handler, not a silent branch.
- Subagent API errors have their own section (**DOC** `sub-agents`).
- Hook events run at three cadences: per session (`SessionStart`/`SessionEnd`),
  per turn (`UserPromptSubmit`, `Stop`, `StopFailure`), per tool call
  (`PreToolUse`, `PostToolUse`, plus `PermissionRequest`, `PostToolBatch`,
  `SubagentStart/Stop`, `TaskCreated/Completed`, `PreCompact`, `PostCompact`)
  (**DOC** `hooks`). The PreCompact/PostCompact pair is the notable one for
  August: compaction is an observable event, not an internal step.

## 13. Clever / notably bad

**Clever:** the memory-vs-hook split stated in one sentence ("context, not
enforced configuration" — use a hook to block); the per-tool-type grant
lifetime table (session-end for edits, permanent for commands); the classifier
that trusts only start-of-session remotes; the published compaction survival
table; hook events for failures and for compaction boundaries.

**Notably bad / not applicable:** single-vendor model lock-in; no third-party
provider routing (the thing August exists for); the classifier adds a second
model call and a second failure mode to every action; 6 permission modes plus
per-tool-type rules plus a classifier is a large surface for a user to reason
about; auto memory being "first 200 lines or 25 KB" is a silent truncation the
user must know about.

## Ideas worth stealing for August

1. **State the memory contract in one sentence, and act on it.** August's memory
   is already context, not enforcement — but the write doors are where that
   should be *said* (`backend-py/app/services/memory_store/rest.py`, the
   `remember` tool description). Cheap, removes a class of user confusion.
2. **Per-tool-type grant lifetime.** August's four guard modes are global.
   Claude's table gives a different lifetime per tool class: session-end for file
   edits, permanent for a specific command (**DOC** `permissions`). A per-grant
   scope on the approval record (`sandbox/policy.py` / pending-mutation store)
   is a small change with a large reduction in prompt fatigue.
3. **Trust the start-of-session state, not the live state.** Claude's classifier
   ignores remotes added or repointed mid-session (**DOC** `permission-modes`).
   August's workspace trust (`.aug/skills`, hooks) is evaluated live. Pinning the
   trusted set at session start closes a same-session escalation.
4. **A PreCompact/PostCompact event pair.** August compacts in five places and
   the user sees a card. Emitting a `pre_compaction`/`post_compaction` event on
   the existing SSE vocabulary (mirroring `recovery`) makes compaction auditable
   in the transcript and testable without a model.
5. **Publish a compaction survival table.** August's compaction already carries
   skill bodies and re-injects the profile lane; write down *what survives, per
   mechanism* the way `context-window` does, in `docs/agent/harness.md`. The
   doc is the cheaper half of the feature.
6. **Failure as an event.** `PostToolUseFailure` / `StopFailure` map onto
   August's `turn_end {reason}` and guardrail ledger; the gap is a *hook* for a
   user to observe a failure, which August's `.aug/hooks.json` could carry
   without new machinery.
7. **A plan approval artifact.** August's plan level blocks mutating tools until
   `approveWorkbenchPlan`; Claude renders the plan itself as a reviewable object
   with its own re-injection after compaction (**DOC** `permission-modes`,
   `context-window`). August's plan markdown already exists — promote it to the
   same status.

## Anti-patterns to avoid

- **A second model as a gatekeeper on every action.** The classifier is a
  product decision that trades latency and a new failure mode for prompt
  fatigue. August's deterministic guards are cheaper and auditable; do not
  replace them with a model.
- **Six permission modes.** August's four levels already carry the same
  semantics with less to explain. Do not import the vocabulary.
- **Silent truncation of memory** ("first 200 lines or 25 KB") without a receipt.
  August's boot index already *names* what it drops — that is strictly better and
  should not be traded away for a size cap.

## Not applicable to August

- Claude-only model routing (no provider story to learn from).
- Managed-policy / MDM settings tiers (single-user local product).
- Claude Desktop's account-bound surface (August is local-first, no account).

## Coverage ledger

**Read (docs, full text):** how-claude-code-works, memory, context-window,
prompt-caching, settings, hooks, skills, sub-agents, permissions,
permission-modes, sandboxing, desktop, tools-reference.
**Not fetched / not examined:** update mechanism, `settings-reference` key list,
`hooks-guide` (only the lifecycle page was read), Agent SDK pages, cloud/remote
control pages, IDE integration pages, `llms.txt` index.
**No source exists** for either Claude Code or Claude Desktop; every claim above
is documentation-level. No Anthropic endpoint was called and no paid API was
used.