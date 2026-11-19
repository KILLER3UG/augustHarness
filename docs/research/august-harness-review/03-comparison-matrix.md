# Comparison matrix — August vs the reference harnesses

Read this as: *what does each reference do in area X, and what is August's
position relative to it.* A cell marked **—** means the harness has no such
capability (verified by absence in that repo's source or docs). "Observed" marks
Claude Code/Desktop, where no source exists and the claim is documentation-level.

Evidence for every cell lives in `02-harness-notes/<harness>.md`; August's side
of each row lives in `01-august-audit.md` with file:line citations.

Legend: **AHEAD** = August is stronger/more honest here; **PARITY** = comparable;
**BEHIND** = the reference does something materially better and it fits August;
**N/A** = different product shape.

> **Evidence note.** Reference-repo citations are repo-relative paths (with line
> numbers where the reading agent captured them) into `--depth 1` clones of each
> project's default branch, taken 2026-11-14. The clones were deleted after the pass
> (the brief limits created files to the report set), so those paths are **not
> re-openable from this tree**: to check one, clone the repo and open the cited path
> — the citation is the search key, not a live link. Every August-side claim is
> re-checkable in-tree and carries a `file:line` that resolves today.

---

## 1. Memory model

| | Model | Scope | Recall | Subagent writes |
|---|---|---|---|---|
| **August** | Markdown file per fact + `MEMORY.md`; boot index; profile/preference always-in lane; delta lane; pull-only BM25 | global + `bot:<id>` | two automatic lanes + pull-only search | **no** (hard deny set) |
| Hermes Agent | provider fan-out (builtin + ≤1 external plugin); `<memory-context>` fence; egress scrubbed | per profile | fenced recall + FTS5 session search | no (`memory` tool denied) |
| DeepSeek Harness | **none** — session log *is* the memory; `@label` cross-session refs as untrusted context | per session | session references only | N/A |
| Codex | two-phase: claim idle rollouts → extract `raw_memory` → git-baselined workspace + consolidation sub-agent | global | file scan | **no** (sub-agent sessions excluded) |
| Goose | memory as an MCP extension; session export to MD/HTML | per session | context revision | — |
| Gemini CLI | GEMINI.md hierarchy + auto-memory as **diff patches into a review inbox**, never direct writes | project | inbox approval | — |
| OpenHands | server-side (SDK repo, not examined); Canvas keeps UI state only | per conversation | — | — |
| Crush | **none** — markdown injected into the system prompt (`CRUSH.md`, `AGENTS.md`) | project | static | — |
| pi / oh-my-pi | pi: none. omp: 5 backends (off/local/hindsight/mnemopi/sharpshooter); two-phase extraction → `MEMORY.md` + skills; recall injected **as background at turn 1** and persisted for cache stability | per repo | background recall block | — |
| Claude Code (observed) | CLAUDE.md (user-written) + auto memory (Claude-written), both re-injected after compaction; memory is "context, not enforced configuration" | managed/user/project/local | always in context | **yes** — subagents can have their own auto memory |

**Position: AHEAD on honesty and on the one-store rule; PARITY on recall shape;
BEHIND on nothing material.** August is the only harness whose memory is a
human-auditable file record with a UI mirror, an undo door, and named omission
receipts. DeepSeek and Crush have none. omp's "recall as a background block at
turn 1, persisted for cache stability" is the one idea worth taking (see
`04-proposals.md` P-M1).

---

## 2. Skills / plugins

| | Format | Discovery | Load policy | Notes |
|---|---|---|---|---|
| **August** | `SKILL.md` + frontmatter; canonical sections; optional support files | 5 roots (bot → project → agent → external → bundled), earlier wins | body loads only via `load_skill`; index is name+description only | review-before-create; destructive kinds need a human; trash/restore |
| Hermes Agent | `SKILL.md`, frontmatter incl. `platforms`, `metadata.hermes.*` | `skills/` (builtin) + `optional-skills/` (installed) | **description ≤60 chars, enforced by test**; no `offset`/`limit` on skill tools | skill bundles as YAML aliases with content-digest cache |
| DeepSeek Harness | `SKILL.md` / flat `.md`; kebab-case names | ranked roots (project → custom → user → bundled), first-wins | every load re-reads the file; Chokidar watches roots | bad frontmatter drops the whole skill with a warning |
| Codex | `SKILL.md`; embedded system skills → cache behind a content-fingerprint marker | explicit `@skill` mentions + implicit detection | system cache | skills can require their own approval class |
| Goose | `SKILL.md` per agentskills.io; `metadata` bag | global `~/.agents/skills` + project `.agents/skills` | exposed as a `SkillsClient` MCP extension | — |
| Gemini CLI | `SKILL.md` + `activate_skill` tool | extensions + skills + MCP | activation is a tool call | — |
| Crush | `SKILL.md` per Agent Skills standard | embedded builtins via `crush://skills/` + global + project | 3 slices: `allSkills` / `activeSkills` / `states` per file | **states publish why a skill didn't load** |
| pi / oh-my-pi | `SKILL.md` | extension dirs + npm/git packages | load on demand | omp imports rules from 8 foreign formats (Cursor MDC, Cline, Codex…) |
| Claude Code (observed) | `SKILL.md`; commands merged into skills | user + project | **body loads only when used; description always in context**; rebuilt after compaction, capped 5k/skill, 25k total | `/skill-name` invocation |

**Position: PARITY on format (August's is a superset of the merge); AHEAD on
mutation safety (proposals, trash, rollback, content rails); BEHIND on two small
things** — Crush's per-file "why didn't this load" diagnostics and Hermes's
description length discipline. Neither is a big gap.

---

## 3. Tools + permission model

| | Model | Granularity | Approval persistence |
|---|---|---|---|
| **August** | 4 levels: ask / edit / plan / full; unknown spelling clamps to `plan` | global per session | per-turn only (no persistent rule store) |
| Hermes Agent | facade + leaves: floors → yolo → guardian LLM → human wait | per-command + per-allowlist; **deny floors match deobfuscated variants before every bypass** | permanent allowlist per profile home |
| DeepSeek Harness | presets `workspace-write` / `danger-full-access`; **advisory** repeat-tool reminder (no hard cap) | per-tool flags in a preset | — |
| Codex | `AskForApproval` = `UnlessTrusted | OnRequest | Granular{…} | Never`; `GranularApprovalConfig` splits sandbox-exec, rules, skills, `request_permissions`, MCP elicitations | per category | — |
| Goose | `auto | approve | chat | smart_approve`; **LLM-as-judge** classifier with request IDs treated as untrusted | per tool + blake3 hash of args | **persisted with expiry, atomic writes** |
| Gemini CLI | declarative TOML policy: `allow | deny | ask_user` with tiered priority Default < Extension < Workspace < User < Admin; typed confirmation bus | per tool / per path | settings-based dynamic rules |
| Crush | `Normal / Yolo / Sysadmin` = two predicates (`SkipsPrompts()` / `RefusesDangerous()`); hooks as deterministic policy applied **before** the prompt | **`GrantKey` scopes a grant to the command, not the cwd** | persistent grant with race-safe commit |
| opencode | default-deny baseline for ruleless agents; wildcard rulesets, last-match-wins | per action/resource | persisted decisions |
| Claude Code (observed) | 6 modes (`default`/`acceptEdits`/`plan`/`auto`/`dontAsk`/`bypassPermissions`); auto mode uses a **second model as classifier** | per tool type, with **per-type grant lifetime** (session-end for edits, permanent per command/domain/repo) | "don't ask again" is durable |

**Position: BEHIND on three concrete things, all of which fit August.**
(1) August's "don't ask again" has no persisted grant at all — Crush's
command-scoped `GrantKey`, Goose's expiring context-hash store and Claude's
per-type lifetimes all solve the same problem better. (2) August has no granular
per-category approval — Codex's `Granular{…}` is the minimal version. (3) August's
guard levels are a 1–4 scale; Crush's two orthogonal predicates are a cleaner
model. August is **AHEAD** on determinism: every other harness except Codex/Gemini
leans on an LLM judge or a model classifier for approvals; August's doors are
rule-based.

---

## 4. Sandboxing

| | Model |
|---|---|
| **August** | Soft Windows sandbox (AppContainer opt-in); hardline credential doors; path doors with junction/symlink resolution; **no OS kernel sandbox** |
| Hermes Agent | per-backend terminal isolation (local/Docker/SSH/Modal/Daytona/Vercel); shared path validator with Unicode-separator rejection |
| DeepSeek Harness | `read-only | workspace-write | danger-full-access`; bwrap → Landlock (Linux), Seatbelt (macOS), restricted-token + ACL (Windows); per-call escalation |
| Codex | Landlock + PID namespace, Seatbelt + daemon, Windows restricted-token + MXC, bwrap fallback; `sandbox_integrity`; network proxy |
| Goose | **none** — permission gating + optional Docker |
| Gemini CLI | Seatbelt profiles (7), Docker/Podman, runsc, LXC |
| Crush | **none** — dangerous-command blocklist + modes + hooks |
| pi / oh-my-pi | pi: none (containers by convention). omp: bwrap/reflink/overlayfs/projfs copy-on-write per bash call |
| Claude Code (observed) | off by default; writes limited to cwd + temp + added dirs, **protected paths stay write-denied even sandboxed**; documented list of what runs outside |

**Position: BEHIND on real OS confinement, AHEAD on credential doors.** August's
credential doors (seven listing doors, one tokenizer, hardline push lock, DPAPI
store) are more thorough than any harness's. But every serious harness has a
kernel-level sandbox for model-written shell and August does not (AppContainer is
opt-in, and `workspace-write` doesn't even confine to the workspace on POSIX).
The honest framing: August's threat model is "the model can read my credentials",
not "the model can write outside my workspace" — and the latter is the one the
references all close.

---

## 5. Context management / compaction

| | Trigger | Method |
|---|---|---|
| **August** | five paths (pre-turn, budget rung, overflow rescue, manual, subagent) | compact-in-place with verbatim head vs rotate; precomputed summary; skill bodies survive compaction; per-route argument parity |
| Hermes Agent | pre-LLM + proactive prune | middle-turn summarization, head/tail protected, tool-output prune first, ContextVar route pinning, **deterministic fallback summary** instead of a second stalled call |
| DeepSeek Harness | `min(0.8W, W − O − 64k)` | **model-free tool-result pruner first**; 8-section checkpoint prompt; **merge with prior checkpoint**; bracket-first region drop |
| Codex | token cap 20k user message | PreCompact/PostCompact hooks; remote compaction v2; retained tool outputs |
| Goose | `min(0.8 × window, 225k)` | standalone compaction crate, pluggable model + token estimator |
| Gemini CLI | graph-based manager with **hysteresis** to prevent churn | shadow-git checkpoint; PreCompress hook |
| Crush | remaining < 20k buffer (or 20% of window); **`cw == 0` skips summarization** | manual + auto share one `summarize()`; `PreCompact` can block and end the turn |
| opencode | buffer / keep / precompute | **precomputed in the background as context nears the threshold, then swapped in** |
| pi / oh-my-pi | pi: `window − 16384 reserve`, cut only at user-message boundaries | omp: 5 methods in order (remote, snapcompact, handoff, shake, soft); **speculative/async compaction**; **superseded-read pruning**; **useless-result elision**; idle compaction timed to prompt-cache TTL |
| Claude Code (observed) | `/compact` or auto | published **per-mechanism survival table** (CLAUDE.md, memory, skills, plan re-injected; files re-read; skill bodies capped 5k/25k) |

**Position: PARITY on core, BEHIND on three specific techniques.** August's
five-path reconciliation and precomputed compaction are as good as anything here.
What August lacks: the model-free prune *before* any LLM call (DeepSeek), the
8-section checkpoint + merge-with-prior (DeepSeek), superseded-read pruning and
useless-result elision (omp), and the published survival table (Claude — free,
and August's compaction already behaves that way).

---

## 6. Subagent design

| | Shape | Restrictions |
|---|---|---|
| **August** | `spawn_subagents` with children cap, concurrency cap, shared round budget; deny set published as `currentSubagentDenied` | no memory writes; `update_agent`/`delete_agent`/`create_agent` denied; lineage ceiling from parent |
| Hermes Agent | child `AIAgent` + own `task_id` + focused prompt; single & batch; registry owns interrupt/steer/list | hard denylist: `delegate_task`, `clarify`, `memory`, `send_message`, `cronjob_manage`; children never gain tools the parent lacks; default toolsets = terminal/file/web |
| DeepSeek Harness | in-process / fork / acp / codex / claude-code / sdk providers; `agent-team` task DAG; tool-ralph (shared workspace as memory) | — |
| Codex | `thread_spawn` + agent graph store (parent→child edges); message board; roles from disk | — |
| Goose | `SubagentRunner` trait; parent scans for requested IDs; `CancellationToken`; streaming updates to parent | read-only or write access per subagent |
| Gemini CLI | subagents **exposed as tools**, own prompt, restricted toolset, independent context loop; `@name` forces delegation | per-subagent model / maxTurns overrides |
| Crush | `agent_dispatch`/`agent_send`/`agent_stop`; real child sessions; `access: read | write`; report folds into the in-flight turn | model override validated against usable models |
| opencode | background **jobs with recovery records**; server shutdown interrupts but leaves the child resumable | only an explicit caller cancel releases the claim |
| pi / oh-my-pi | pi: out of core. omp: `task` tool, W(n) placement, speculative launch, semaphore 32, **idle-TTL parking + revival**, artifact URLs | depth limit 2; soft request budget 200 |
| Claude Code (observed) | markdown-defined; automatic + explicit; foreground/background; concurrency limit; subagents can spawn their own | own memory allowed; per-subagent model/effort/thinking |

**Position: AHEAD on enforcement (the deny set is a real boundary, not a
suggestion), PARITY on shape, BEHIND on recovery.** Nobody but opencode treats a
subagent run as a resumable job, and nobody but omp lets a finished worker park
and be revived. August's subagents die with their turn.

---

## 7. Provider / model handling

| | Model |
|---|---|
| **August** | provider-agnostic gateway; base URL used exactly as pasted; per-model `apiFormat` override; loopback keyless; single pricing source; **no auto-routing** |
| Hermes Agent | single `model.provider` enum, ~20 providers, `auto` credential detection; auxiliary model route class |
| DeepSeek Harness | `llm-deepseek` / `llm-pi-ai` / `llm-replay`; DeepSeek wire extensions in a separate package |
| Codex | `model-provider` + `models-manager`; ChatGPT-plan auth vs API key; Bedrock/ollama/lmstudio |
| Goose | ~10 providers + ACP agents as providers + tool shim for models without native tool calling |
| Gemini CLI | Gemini/Code Assist/Vertex; **model routing with fallback chain**; experimental on-device Gemma router |
| Crush | provider *identity* separated from *wire format* (`responses`/`completions`/`messages`); ~25 env providers + OAuth; Bedrock SSO refresh-and-retry-in-place |
| opencode / kilocode | models.dev catalog; OAuth + credential services; usage limits |
| pi / oh-my-pi | pi-ai unified client, models.json catalog. omp: 60+ providers, 9 roles (default/smol/slow/plan/commit/vision/task/advisor/tiny) |
| Claude Code (observed) | Claude-only; Sonnet/Opus switching |

**Position: AHEAD on provider-agnosticism and on the pasted-base-URL rule** (only
Crush shares the "wire format is a first-class provider field" idea, and August's
per-model override is the same insight). Crush's SSO refresh-and-retry-in-place is
worth stealing. omp's 9 model roles are the opposite of August's minimalist
posture — a rejection, not a gap.

---

## 8. Update mechanism

| | Model |
|---|---|
| **August** | automatic download ~60 s after launch; **restart is the user's click**; in-place silent NSIS replace; detached waiter owns the relaunch; minisign at both ends; CI-only releases |
| Hermes Agent | delegated to artifact owner (MSIX / Store / electron-updater); `hermes update` for source checkouts |
| DeepSeek Harness | electron-updater; nightly check + 10 min ±20% jitter, backoff to 1 h; user-confirmed restart with an **interruption warning**; staged rollout by percentage; mandatory-update channel |
| Codex | install scripts/npm/brew; `doctor updates` diagnoses channel with bounded probes and **never runs a package manager** |
| Goose | `goose update` CLI with `--canary`; Electron auto-update |
| Gemini CLI | npm channels: preview (Tue) → stable (weekly promotion) → nightly |
| Crush | **check-only** — prints the command |
| OpenHands | **check-only** — prints npm/docker commands; no `autoUpdater` |
| opencode | Cloudflare Worker with **IP-staged gradual rollout**, SHA-256 eligibility, minimum-version floor, checksums |
| pi / oh-my-pi | `pi update` self-update + install-lock pinning, `--ignore-scripts`, `minimumReleaseAge: 2d` |
| Claude Code (observed) | not examined (no claim) |

**Position: AHEAD on the update contract** — automatic download + explicit restart
+ in-place replace + signature verification is stricter than every reference
except DeepSeek's staged rollout. BEHIND on two small things: the "what would be
interrupted" check before restart (DeepSeek) and a doctor-style "am I current?"
diagnostic (Codex).

---

## 9. Context / UX surface for a long turn

| | Model |
|---|---|
| **August** | one activity line per tool run, opens on failure, no time on settled lines; thinking card = sentence list; artifact cards with result-side collection; context % from backend measurement only |
| Hermes Agent | live tool activity + structured tool summaries + side-by-side preview panes; streaming responses |
| DeepSeek Harness | chat is a pure renderer of the session log; per-turn visibility modes (Compact/Standard/Detailed/Final/Verbose) |
| Codex | Ratatui chat widget + bottom pane; approval prompts as protocol events so every UI shares one model |
| Goose | "Action required" confirmations; recipes as reusable workflows |
| Gemini CLI | compact tool output; footer with sandbox + model + context %; window-title status icons |
| Crush | per-tool renderers; notifications only when the terminal is unfocused; error copy names the escape hatch |
| OpenHands | running action replaced by its observation with **shared tool-call key** so disclosure state survives the swap; staged "reconnecting → retryable, never 'the agent failed'" |
| opencode | semantic design tokens; **spinner reserved for AI activity** |
| pi / oh-my-pi | differential-render TUI; tool cards; mid-turn `ask` tool (HTML form with a preselected recommendation) |
| Claude Code (observed) | terminal transcript; permission prompts inline at the call site |

**Position: AHEAD.** August's activity-line grammar is the reference's own and is
implemented more faithfully than most. The one worth taking is OpenHands's
"running action replaced by its observation, sharing a key" — it is the same
insight as August's disclosure-owns-its-body, applied to the row identity.

---

## 10. Error recovery

| | Model |
|---|---|
| **August** | `turn_end {reason}`; stall counter with argument-novelty reset; runaway world-delta backstop; thinking-only rescue; stuck-request ladder; per-family error taxonomy |
| Hermes Agent | ordered one-shot recovery handlers (cheap-and-certain first); bot loop guard with hop semantics; denial circuit breaker that rewrites only the tool result |
| DeepSeek Harness | `agent/request-error` waterfall; synthetic `TOOL_OUTCOME_UNKNOWN` results with "retry only if read-only/idempotent"; interrupted-turn closers on resume |
| Codex | rollout resume via ordinals; job leases with backoff; `doctor`; compaction model fallback |
| Goose | errors returned to the model **as tool responses**; on interrupt, synthesize error tool-responses for open calls |
| Gemini CLI | shadow-git checkpoint + `/restore`; retry with `maxAttempts ≤ 10`; model-routing fallback |
| Crush | `settleTurn` answers abandoned tool calls on a detached context (5 s); `StopFailure`/`Interrupt` hooks; `exit 49` halts the turn |
| opencode | runner retry layers; job recovery kinds |
| Claude Code (observed) | `PostToolUseFailure` / `StopFailure` hook events — a failure is an event with a handler |

**Position: AHEAD on loop bounds** (August is the only harness with a hard round
cap + a world-delta backstop; DeepSeek explicitly has none). BEHIND on two: the
synthetic "outcome unknown" tool result for unanswered calls (DeepSeek/Goose/Crush
all do some version of this) and interrupt settlement (Crush).

---

## 11. Developer experience / gates

| | Model |
|---|---|
| **August** | no test suite (owner decision); ~20 static gates + `e2e:smoke`; mypy + ruff; tsc + eslint; one gate list for CI/release/dev |
| Hermes Agent | full test suite + evals + `scripts/dev-sandbox.sh` (throwaway HERMES_HOME, separate userData, distinct app name to dodge the single-instance lock) |
| DeepSeek Harness | ~10 vitest configs (unit/e2e/bench/web/snapshot/stress); generated catalogs; every package README states its **Token effect / KV cache effect** |
| Codex | bazel + cargo dual build; `doctor` command |
| Goose | `Justfile`; docusaurus docs site |
| Crush | `Taskfile`; `sqlc`; test files named for the invariants they pin |
| OpenHands | playwright matrix (bind-policy/live/mock-llm/docker/windows), stryker mutation testing, release-please with hand-editing forbidden |
| opencode | storybook-style fixture TUI (`OPENCODE_STORY`); strict i18n governance |
| pi / oh-my-pi | install-lock + `--ignore-scripts` + `minimumReleaseAge: 2d`; bazel; N-API natives |
| Claude Code (observed) | not applicable (closed) |

**Position: PARITY on philosophy, BEHIND on tooling.** August's static-gate
approach is a legitimate response to the no-test-suite ruling, and the
"three gates that exist because the defect is invisible" reasoning is better than
most repos have. But August has no dev sandbox for the desktop app (Hermes's
one-liner), no mutation testing, and no storybook-style fixture harness for the
TUI-equivalent surfaces. The audit's area-11 pass will sharpen this.

---

## Summary: where August leads, and where it doesn't

**August leads on:** memory honesty and the one-store rule; credential doors;
enforcement (deny sets, lineage ceilings, no verifier gate but real loop bounds);
the update contract; the chat-surface grammar; provider-agnosticism with pasted
base URLs; safety decisions being deterministic rather than model-judged.

**August is behind on:** real OS sandboxing; persisted/scoped approval grants;
subagent recovery and resumability; compaction techniques (model-free prune,
checkpoint merge, superseded-read pruning); a "why didn't this load" diagnostic
for skills; and desktop developer tooling.

**August should reject:** model-as-permission-judge (Goose, Claude auto mode);
Cordis-style plugin ceremony (DeepSeek); 5 compaction methods × 5 memory backends
(omp); shell-at-config-load (Crush); LLM-authored memory with no review door;
check-only updates (Crush/OpenHands) — August's is already better.