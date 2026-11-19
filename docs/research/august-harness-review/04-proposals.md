# Proposals — every item, with evidence, effort, impact, risk

Format per the brief: **problem in August** (file:line), **what the reference
does** (file:line or URL), **proposed change**, **effort** (S/M/L), **impact**
(low/med/high), **risk**, and **what it replaces or removes**.

Every proposal is marked VERIFIED or INFERRED, and every proposal was checked
against Phase 0 (the repo's own docs, `docs/STATUS.md`, `docs/ROADMAP.md`,
`docs/GAPS_AND_BUGS.md`, `docs/specs/memory-and-skills.md`) so nothing already
shipped or already decided is re-proposed.

> **Evidence note.** "What the reference does" cites paths into `--depth 1` clones
> of each reference project taken 2026-11-14; the clones were deleted after the pass
> (the brief limits created files to the report set), so those citations are search
> keys into a fresh clone, not live links. Every "problem in August" citation resolves
> in-tree today.

Priority key: **P0** = do first (security / data-loss / correctness),
**P1** = high value, low risk, **P2** = worth doing, **P3** = nice / opportunistic.

---

## P0 — Security and data-loss class

### P0-1 · Rotate the Google OAuth secret and move MCP env secrets into the secret store
- **Problem in August:** `data/mcp-servers.json` holds `GOOGLE_OAUTH_CLIENT_SECRET`
  in plaintext (untracked, gitignored). `scrubOutboundBody`'s collectors cover
  providers.json, the encrypted store, the gateway key and `*_API_KEY` env — the
  per-server `env` map is in **no** collector, so a value that lands in a
  transcript would not be scrubbed on the way to the provider.
  `services/hooks/secret_guard.py:114-140`; `data/mcp-servers.json`. **VERIFIED**
- **What the reference does:** Hermes resolves credentials per operation from an
  encrypted store and scrubs egress unconditionally, failing closed
  (`agent/memory_manager.py:89-119`). DeepSeek's `credential-reference` guard stores
  *names* and resolves values per operation, and the settings UI shows presence
  only (`packages/credentials/README.md`). **VERIFIED**
- **Proposed change:** (a) rotate the exposed secret; (b) store per-server env
  secrets as `${NAME}` references into the existing DPAPI store — the resolution
  door already exists at `services/tools/chat_secrets.py:64-96`; (c) add `_servers`
  env values and `mcpGlobalEnv` to `collectOutboundSecrets`.
- **Effort:** S (b+c are one file each) · **Impact:** high · **Risk:** low
- **Removes:** the plaintext env map as a secret carrier.

### P0-2 · Stop exporting service tokens to `os.environ`; widen the scrub to token-class names
- **Problem in August:** `services/service_connections.py:493` and `:516` do
  `os.environ['GITHUB_TOKEN'] = …` / `os.environ['SLACK_BOT_TOKEN'] = …`. The
  outbound scrub's env collection sweeps only names ending `_API_KEY`
  (`hooks/secret_guard.py:114`), so token-class secrets are invisible to it and are
  inherited by every child process. **VERIFIED**
- **What the reference does:** Hermes's egress scrub is unconditional and covers
  every provider hand-off (`agent/memory_manager.py:89-119`). **VERIFIED**
- **Proposed change:** resolve service tokens at use-time from the secret store
  instead of exporting them; if any export must stay, extend the scrub's env scan
  to a named set (`GITHUB_TOKEN`, `SLACK_BOT_TOKEN`, `*_TOKEN`).
- **Effort:** S · **Impact:** high · **Risk:** low (the tokens already live in the
  encrypted store)
- **Removes:** two process-wide env vars.

### P0-3 · Fix the wrong-binary relaunch in the NSIS POSTINSTALL hook
- **Problem in August:** `hooks.nsh:193` runs `ExecShell "open" "$INSTDIR\August.exe"`;
  `tauri.conf.json:6` sets `mainBinaryName: "AugustHarness"`. The installer's
  relaunch — which its own comments say "must own bringing the app back" — fails
  silently on every install. The app returns only because the Rust waiter uses
  `current_exe()`. The code and `docs/agent/product.md:59-63` also contradict each
  other about which owner is authoritative. **VERIFIED**
- **What the reference does:** every reference that ships a desktop app has exactly
  one relaunch owner. **INFERRED** (no reference has August's two-owner split).
- **Proposed change:** `ExecShell "open" "$INSTDIR\${MAINBINARYNAME}.exe"` and pick
  one owner in the docs. Add a static gate asserting the POSTINSTALL exe name
  equals `${MAINBINARYNAME}` so this cannot regress silently.
- **Effort:** S · **Impact:** high · **Risk:** low
- **Removes:** the triple-name hedging (`August`/`AugustHarness`/`augustharness`)
  once the name is pinned — `hooks.nsh:110-112`, `backend.rs:3360-3365`.

### P0-4 · Make `save_fact`'s CAS write happen inside the lock
- **Problem in August:** the `if_updated_at` compare-and-swap runs inside
  `_record_lock()`, but `record.write_fact_file(row)` at `rest.py:517` is outside
  it, so two threads can both pass the guard and the first update is silently lost.
  The docstring claims the lock closes this. **VERIFIED**
- **What the reference does:** DeepSeek writes session state inside one atomic
  transaction and treats a compaction error as "leave the log untouched"
  (`packages/compaction/compaction-basic/README.md`); Crush writes state with
  temp-file+rename and asserts the winner of a grant race
  (`internal/permission/permission.go:217-273`). **VERIFIED**
- **Proposed change:** move `write_fact_file` and the index invalidation inside the
  `with _record_lock():` block, matching `update_fact_value` (`rest.py:684-696`)
  and `touch_fact_usage` (`rest.py:544-551`). One indent.
- **Effort:** S · **Impact:** high (silent data loss) · **Risk:** low
- **Removes:** nothing.

### P0-5 · Write `providers.json` atomically in migration steps v2 and v3
- **Problem in August:** `data_migrations.py:107` and `:138` use bare `write_text`;
  step 4 does it right with `mkstemp` + `os.replace` and documents that v2/v3 were
  shipped wrong (`:193-204`). A mid-write crash truncates the user's provider
  config, both readers swallow the parse error, and `apply_migrations` then stamps
  the version (`:225`) — the loss is permanent and silent. **VERIFIED**
- **What the reference does:** every reference writes state atomically (goose
  `permission_store.rs`; crush `config/atomicwrite.go`). **VERIFIED**
- **Proposed change:** one `_atomic_write_json` helper used by every step. Because
  the ladder forbids editing shipped steps ("add first, remove later",
  `data_migrations.py:20`), add a v5 repair step that restores a `.bak` when a
  truncation marker is present.
- **Effort:** S · **Impact:** high · **Risk:** low

### P0-6 · Persist terminal daemon status on natural exit
- **Problem in August:** `daemon_manager` hard-codes `status: 'running'` at spawn
  (`:205-206`), the natural-exit path updates only the in-memory `DaemonResult`
  (`:504-508`), and only `kill()` deletes the row (`:236-242`). `rehydrate_from_db`
  rehydrates every row whose stored status is `running` (`:275-277`), so a
  force-killed quit (the packaged quit path) leaves completed daemons resurrecting
  on next boot; only the persisted TTL saves you. **VERIFIED**
- **What the reference does:** opencode models a subagent run as a **job with a
  recovery record** — server shutdown interrupts the job but the child stays
  resumable and the record is explicit (`packages/core/src/session/subagent-job.ts:20-40`).
  **VERIFIED**
- **Proposed change:** persist terminal status on the `_runLoop` exit path; on
  boot, drop rows past their deadline instead of trusting in-memory monotonic math;
  report skipped rows.
- **Effort:** S · **Impact:** med-high · **Risk:** low
- **Alternative to consider:** if the product decision is "daemons do not survive a
  restart", delete the durable store entirely (78 lines) and this defect disappears
  — see `06-open-questions.md` Q4.

### P0-7 · Fail closed in `tool_call`'s no-session fallback
- **Problem in August:** `tool_bridges.py:151-155` runs `_checkToolGuard` only when
  `get_session()` returns a session; the fail-closed branch at `:156-165` covers a
  raised exception, not a `None` return. After the subagent deny check, dispatch
  tries `_executeTool` (`:198-205`) and on any exception falls through to raw
  `dispatch(name, args)` at `:212` — no PRE/POST hooks, no read-before-edit gate, no
  mutation log. This is the only remaining path to the registry that skips
  `_executeTool`. **VERIFIED**
- **What the reference does:** DeepSeek's approval fails closed as `unavailable`
  when no answerer exists (`docs/capability-seams.md:760`). **VERIFIED**
- **Proposed change:** fail closed when no session is bound — mirror the `:156-165`
  pattern: mutating tools blocked, reads at most raw-dispatched — or bind an
  explicit system session so hooks always run.
- **Effort:** S · **Impact:** high · **Risk:** low

### P0-8 · Put the per-install token on the mutating MCP-server routes
- **Problem in August:** `routers/mcp.py` create/update/delete (`:117, 158, 224`)
  carry only the gateway key + origin guard, while start/stop (`:232, 272`) also
  require the per-install token. A non-browser local process (no `Origin`, which the
  trusted-origin guard deliberately lets through) can register an arbitrary stdio
  command that the background discovery refresh later spawns
  (`mcp_client.py:1485-1504`). **VERIFIED**
- **What the reference does:** DeepSeek runs every capability behind one `ctx.approval`
  one-shot with fail-closed semantics (`docs/capability-seams.md:760`). **VERIFIED**
- **Proposed change:** add `require_local_token` to the three mutating MCP-server
  routes; the SPA already holds the token.
- **Effort:** S · **Impact:** med · **Risk:** low

---

## P1 — High value, low risk

### P1-1 · Render the `subagentFanout` receipt, and give the dispatcher a `default:`
- **Problem in August:** the backend emits `subagentFanout`
  (`spawn_subagents_tool.py:537`; `emit_types.py:37`) but the frontend has no schema
  literal, no dispatch case, and the `switch` ends with no `default:`
  (`api/workbench/streamEvents.ts:435-530`) — so the fleet receipt ("why did only 6
  of 8 lanes start") is dropped, and any *future* unknown event vanishes the same
  way. **VERIFIED**
- **What the reference does:** OpenHands renders a running action and its
  observation with a shared tool-call key so disclosure state survives the swap
  (`step-disclosure-context.tsx`); opencode's desktop routes every IPC through typed
  handlers composed in one place (`src/main/ipc.ts`). **VERIFIED**
- **Proposed change:** add the `subagentFanout` literal to the Zod schema; add a
  dispatch case that surfaces it as a muted line in the subagent lane (or at minimum
  a dev-visible log path); add a `default:` that logs unknown event types once in
  dev.
- **Effort:** S · **Impact:** med · **Risk:** low
- **Removes:** `subagentFanout` from the SSE gate's SCHEMA_ONLY excuse list — the
  blindness is currently *by construction*.

### P1-2 · Fix the boot-index omission receipt
- **Problem in August:** `brain_index_snippet` slices `factRows` to 40 *before* the
  per-row filters; the receipt is computed as `total > len(factRows)` where `total`
  re-counts visible∧category∧¬sensitive∧¬person but **not** `sensitiveMemoryReason`
  (`brain.py:813-877, 866-873`). 40 visible facts of which 5 trip the content
  scanner → 35 render, `total=40`, `40 > 40` false → **no receipt at all**, 5 facts
  silently absent from the primary door. **VERIFIED**
- **What the reference does:** DeepSeek's compaction keeps a bounded head/tail and
  names what was dropped; Crush publishes per-file skill diagnostics so a silent
  absence is impossible (`internal/skills/manager.go:13-23`). **VERIFIED**
- **Proposed change:** count the receipt against `len(fact_lines)` (what actually
  rendered) and make the `total` recount apply the same `sensitiveMemoryReason`
  predicate the render loop applies.
- **Effort:** S · **Impact:** high (this is the "August does not remember me"
  failure mode) · **Risk:** low

### P1-3 · Refresh `MEMORY.md` from every write door
- **Problem in August:** four doors edit fact files without rewriting the index:
  `brain_update_row` (`brain.py:1277`), `apply_retire_decision`
  (`consolidation.py:689-691`), `update_fact_value` (`rest.py:696-703`),
  `restore_superseded_losers` (`rest.py:766-778`). A retired row stays listed as
  active; an edited title shows old text; a restored row is invisible — until an
  unrelated write rebuilds. **VERIFIED**
- **What the reference does:** Claude Code's memory is re-injected from disk after
  every compaction, so the mirror is always authoritative (observed,
  `context-window`). **INFERRED**
- **Proposed change:** one rule, one helper — every door that calls
  `record.write_fact_file` also calls `record.note_fact_changed(key)` (best-effort,
  never raises; `mirror.py:495-505` already has that contract).
- **Effort:** S · **Impact:** med · **Risk:** low

### P1-4 · Delete the dead M-7 disuse machinery
- **Problem in August:** the disuse penalty reads `injected_count`
  (`fact_retrieval.py:404-429, 555-583`), which nothing writes any more —
  `_set_injected` (`rest.py:559`) has zero callers since the per-turn ranked tail
  was removed. ~90 lines of scoring logic, 3 config knobs and two wire columns
  simulate a signal that can never exist. **VERIFIED**
- **What the reference does:** n/a — this is August-only dead code. It is exactly
  the "guard nothing can trip" the repo's own audit rules call dead weight.
- **Proposed change:** remove `_disuse_for`, `_disuse_constants`, the penalty block
  and `_set_injected`. Keep the `injected_count` / `last_injected_at` columns for
  wire/UI compatibility but stop claiming they feed ranking.
- **Effort:** S · **Impact:** low-med (clarity) · **Risk:** low
- **Removes:** ~90 lines + 3 config keys.

### P1-5 · Add a model-free prune before any LLM summarization, and a checkpoint-merge rule
- **Problem in August:** August's compaction always reaches for an LLM (or the
  deterministic heuristic summarizer) even when trimming oversized tool results
  would fit the budget. **VERIFIED** (five compaction paths reconciled by census;
  `_compactionCall` is the single site; no pre-pass pruner exists)
- **What the reference does:** DeepSeek runs a **model-free tool-result pruner**
  first and only calls the summarizer if still over threshold, then uses an
  8-section checkpoint prompt and requires the summary to **merge with the prior
  checkpoint** rather than only the visible window
  (`packages/compaction/compaction-basic/README.md`). **VERIFIED**
- **Proposed change:** add a prune-only pre-pass to `_compactionCall`; adopt the
  fixed-section checkpoint + merge-with-prior rule for the LLM note.
- **Effort:** M · **Impact:** med-high (tokens + latency on every compaction) ·
  **Risk:** med (touches the compaction contract; gate with `check:docs`)

### P1-6 · Adopt a persisted, command-scoped "don't ask again" grant
- **Problem in August:** guard modes are per-session and there is no persistent
  approval grant at all — a user who approves `git status` in `ask` mode is asked
  again next turn, and (worse) approving anything for a directory would silently
  approve every later `sudo` in it. **VERIFIED** (no grant store exists; only
  per-turn pending mutations)
- **What the reference does:** Crush's `GrantKey{SessionID, ToolName, Action, Path,
  Grant}` holds **the bash command**, not the cwd — the exact escalation fix
  (`internal/permission/permission.go:154-167`), with `GrantPersistent` committing
  only if it wins the pending race (`:217-273`). Goose persists "tool + blake3(args)
  approved until T" with expiry and atomic writes
  (`crates/goose/src/permission/permission_store.rs`). Claude Code's "don't ask
  again" is durable per repository+command / +domain / +repo and only
  *session-scoped* for file edits (observed, `permissions`). **VERIFIED**
- **Proposed change:** a small JSON grant store keyed on (session, tool, canonical
  args hash, expiry) written atomically through `filestore`; surface it in the
  approval card as "don't ask again for this command".
- **Effort:** M · **Impact:** high (prompt fatigue is the #1 reason users leave
  `ask` mode on) · **Risk:** med (a grant is a security surface — key it on the
  command, never the directory)
- **Removes:** the current all-or-nothing per-session choice.

### P1-7 · Interrupt settlement: never leave an open tool call unanswered
- **Problem in August:** a turn interrupted (or a step that fails) can leave a
  `tool_use` with no result; the stuck-request ladder exists precisely because one
  orphaned `tool_use_id` bricks every later turn. **VERIFIED**
  (`workbench/message_pairing.py` is the repair, applied at three seams; the ladder
  escalates across turns)
- **What the reference does:** Goose synthesizes error tool-responses for every open
  tool call on cancel (`goose-agent/src/machine.rs:74-88 interrupted_tool_responses`);
  Crush's `settleTurn` answers abandoned calls on a **detached** context with a 5 s
  budget (`internal/agent/agent.go:1245-1259`, `settle.go`); DeepSeek records
  synthetic `TOOL_OUTCOME_UNKNOWN` with "retry only if read-only/idempotent"
  (`packages/core/agent-loop/README.md:132`). **VERIFIED**
- **Proposed change:** on interrupt/cancel, synthesize a result for every open call
  before persisting the turn (a detached-context settle with a short budget), rather
  than relying on the next turn's repair pass.
- **Effort:** M · **Impact:** med-high · **Risk:** low
- **Complements:** P1-8 (the pairing repair stays as the second line).

### P1-8 · Publish a compaction survival table and add PreCompact/PostCompact events
- **Problem in August:** five paths compact and the user sees a card, but nothing
  states *what survives* per mechanism, and compaction is not an observable event.
  **VERIFIED** (five paths reconciled by census; the card renders a summary count)
- **What the reference does:** Claude Code publishes a per-mechanism survival table
  (system prompt survives; CLAUDE.md/memory/plan re-injected; skills rebuilt with
  5k/25k caps; files re-read up to five) and fires `PreCompact`/`PostCompact` hooks
  (observed, `context-window`, `hooks`). Codex fires the same pair
  (`core/src/compact.rs`); Crush's `PreCompact` can block the compaction and end
  the turn (`internal/agent/agent.go`). **VERIFIED**
- **Proposed change:** write the survival table into `docs/agent/harness.md`; add
  `pre_compaction` / `post_compaction` to the SSE vocabulary (mirroring `recovery`)
  so compaction is auditable in the transcript and testable without a model.
- **Effort:** S (doc) + S (events) · **Impact:** med · **Risk:** low

### P1-9 · Fix the health signal: distinguish auth failure, and probe concurrently
- **Problem in August:** `probe_provider` sets `success = status < 500`, so 401/403
  (revoked key) and 404 (moved endpoint) report **healthy** with `last_error=None`
  (`health_monitor.py:145-152`); probes also run serially, so at 12 providers one
  unresponsive gateway eats the full minute and the loop never sleeps (`:157-163`).
  The monitor is also started at boot and never stopped (`main.py:193-199`).
  **VERIFIED**
- **What the reference does:** OpenHands distinguishes "no backend configured" from
  "backend unreachable" with typed error codes and separate copy
  (`src/api/agent-server-compatibility.ts:45-61`); opencode stages connection
  feedback (30 s "reconnecting", 60 s "retryable", never "the agent failed").
  **VERIFIED**
- **Proposed change:** `200..300` healthy, `401/403` → `unauthorized`, `404` →
  `misconfigured`, 5xx/network → `unreachable`; `asyncio.gather` the probes; register
  the monitor for teardown.
- **Effort:** S · **Impact:** med · **Risk:** low
- **Note:** three separate audits found this independently (area 1, 5 and 9) — it is
  the most-repeated finding in the whole pass.

### P1-10 · Cap the telemetry ledgers
- **Problem in August:** five per-session telemetry JSONL ledgers
  (`lifecycle`, `usage-events`, `tool-guardrail-log`, `turn-outcomes`,
  `session-topics`) have no size cap although `append_ledger(max_bytes=…)` exists
  and is used elsewhere (`memory_store/sessions.py:567-575`;
  `tables.py:191-197`). `turn_outcomes` is one row per turn and
  `tool_guardrail_log` one row per guarded tool call. **VERIFIED**
- **What the reference does:** every reference rotates or bounds its ledgers
  (DeepSeek's session log is JSONL with a persistence catalog; opencode bounds
  session state). **INFERRED**
- **Proposed change:** pass `max_bytes` for each ledger; the mechanism is already
  there.
- **Effort:** S · **Impact:** low-med · **Risk:** low

### P1-11 · Surface why a skill didn't load
- **Problem in August:** discovery skips a malformed `SKILL.md` with a logged reason
  while its siblings load, but the *user* sees only a missing skill. The spec's own
  UX backlog item #1 says "a silent absence reads as a bug". **VERIFIED**
- **What the reference does:** Crush publishes three slices per workspace —
  `allSkills` (pre-filter), `activeSkills` (post-filter), `states` (per-file
  diagnostics) — as events (`internal/skills/manager.go:13-23, 165-195`).
  **VERIFIED**
- **Proposed change:** return per-skill load state (loaded / skipped-with-reason /
  disabled / platform-gated / missing-tool) from `catalogue()` and render it in the
  skills list.
- **Effort:** M · **Impact:** med (closes a spec backlog item) · **Risk:** low

---

## P2 — Worth doing

### P2-1 · Split the god-files, the way `memory_store/` was split
- **Problem in August:** `workbench.py` 7,683 lines / 111 functions;
  `routers/workbench.py` 2,959; `skill_service.py` 2,828; `skill_distiller.py` 2,184.
  `memory_store/` (9,231 lines across 14 modules) proves the pattern works one
  directory over. **VERIFIED**
- **What the reference does:** DeepSeek decomposes by capability seam (~60 packages,
  each with a README stating its token/KV-cache effect); opencode splits core into
  `session/`, `tool/`, `permission/`, `agent/` (`packages/core/src/`).
  **VERIFIED**
- **Proposed change:** split `workbench.py` into
  `{sessions,turn,budget,tools,subagents,guard}.py` behind a facade `__init__.py`
  that re-exports the old names, so no importer changes. One resource per router
  module for `routers/workbench.py`.
- **Effort:** L · **Impact:** med (reviewability, merge conflicts) · **Risk:** med
  (mechanical but wide)
- **Guard:** `check:loop-imports` only guards `loop/` → workbench; extend it to
  `services/workbench/`.

### P2-2 · Convert the teardown swallows to logged swallows
- **Problem in August:** `main.py`'s teardown has 16 `except Exception:` handlers, 14
  of them a bare `pass` with no log line; `_TeardownClock` exists to name slow steps
  but a step that *throws* is indistinguishable from one that succeeded.
  Repo-wide: 411 bare `except Exception: pass` sites against 10 uses of the
  `best_effort` helper written to replace them. **VERIFIED**
- **What the reference does:** DeepSeek's defensive-patterns doc mandates semantic
  errors with codes over thrown strings (`docs/defensive-patterns/README.md`).
  **VERIFIED**
- **Proposed change:** `logger.debug(..., exc_info=True)` in every teardown `pass`
  first; then extend the `BLE001` ratchet file by file.
- **Effort:** S (teardown) + M (ratchet) · **Impact:** med · **Risk:** low
- **Note:** this will surface failures nobody has seen since writes became durable —
  see `06-open-questions.md` Q5.

### P2-3 · Type-token sweep: kill the arbitrary rem literals
- **Problem in August:** `MemorySection.tsx` uses 11 distinct text sizes including
  five arbitrary rem literals (`0.65625rem`–`1.25rem`); five arbitrary rem literals
  recur across six chat components. The docs count "three". **VERIFIED**
- **What the reference does:** opencode's desktop mandates semantic design tokens by
  role with no raw hues (`packages/desktop/AGENTS.md`); Gemini's settings are a
  single scale. **VERIFIED**
- **Proposed change:** map the literals onto the token ramp (`text-2xs/xs/sm/base`);
  add a `check:design` rule forbidding `text-[N.NNNNrem]` outside the token file.
- **Effort:** S · **Impact:** low-med · **Risk:** low

### P2-4 · Keyboard-navigable `SettingsSelect`
- **Problem in August:** the custom listbox handles only outside-click and Escape —
  no ArrowUp/Down/Home/End, no `aria-activedescendant`, focus stays on the trigger
  when open; inherited by 7+ settings surfaces. **VERIFIED**
- **What the reference does:** Crush's dialogs all own focus (model picker, command
  palette, permissions); OpenHands' popovers are keyboard-complete. **VERIFIED**
- **Proposed change:** on open, move focus to the selected option; handle
  arrows/Home/End/typeahead; keep `aria-activedescendant` in sync.
- **Effort:** S · **Impact:** low-med (WCAG 2.1.1) · **Risk:** low

### P2-5 · Trap focus in the three `aria-modal` dialogs that lack it
- **Problem in August:** `ModelVisibilityModal`, `NotificationsPanel`, `ExamBanner`
  advertise `aria-modal="true"` with no trap and no `useFocusTrap` import — a screen
  reader is told the rest of the app is inert while Tab still escapes. **VERIFIED**
- **What the reference does:** every reference modal owns its focus (Crush dialogs,
  OpenHands modals, Hermes desktop). **VERIFIED**
- **Proposed change:** pass `trapFocus` to `Backdrop` at the three call sites (the
  primitive handles nesting and inner-trap ownership).
- **Effort:** S · **Impact:** low · **Risk:** low

### P2-6 · Delete the retired no-op `deferred_writes` and its misleading comment
- **Problem in August:** `deferred_writes.py` declares itself retired, `defer_commit`
  is a no-op, `flush_thread_pending` returns 0 — yet `main.py:466-475` still calls it
  under a comment claiming it prevents telemetry loss, and it burns one of the 16
  `_TeardownClock` checkpoints. **VERIFIED**
- **What the reference does:** n/a (August-only).
- **Proposed change:** delete the module, its importers, and the `main.py` block; or
  keep the names and delete the call site plus the lying comment.
- **Effort:** S · **Impact:** low · **Risk:** low
- **Removes:** 37 lines + one silent teardown handler + one misleading comment.

### P2-7 · Delete the shadowed `/v1/models`
- **Problem in August:** `routers/models.py:78` is the authenticated implementation;
  `routers/proxy.py:585-591` declares the same path as a pure delegation stub, and
  include order makes it unreachable. The only duplicate path among 448 routes.
  **VERIFIED**
- **Proposed change:** delete `proxy.py:585-591`.
- **Effort:** S · **Impact:** low · **Risk:** low

### P2-8 · A "don't advertise what you can't do" docs pass
- **Problem in August:** `docs/CONFIGURATION.md:29, 944` still call
  `august_brain.sqlite` the SoT; `docs/DEVELOPER_GUIDE.md:40` draws `memory_conn.py`
  and `app/migrations/`, neither of which exists. An operator following the docs
  backs up the retired database — the one file that is no longer authoritative.
  **VERIFIED**
- **What the reference does:** DeepSeek requires every package README to state its
  token/KV-cache effect; Crush/OpenHands keep real in-repo docs rather than stubs.
  **VERIFIED**
- **Proposed change:** rewrite both passages to point at `dataDir/memory/`,
  `dataDir/state/`, `dataDir/telemetry/`, `dataDir/transcripts/`; note the retired
  DB is read once by `retired_sqlite.run_sqlite_retirement()`.
- **Effort:** S · **Impact:** med (this is the "a store that no longer exists is
  still advertised" failure) · **Risk:** low

---

## P3 — Opportunistic

### P3-1 · A desktop dev sandbox (throwaway data dir + separate userData + distinct app name)
- **Problem in August:** the dev loop (`npm run dev:desktop`) contends with the
  installed app's single-instance lock and the real data dir. **VERIFIED**
  (`HARNESS_DESKTOP_APP_DATA` exists for debug builds only; the checkout is still
  named `august-proxy`)
- **What the reference does:** Hermes ships `scripts/dev-sandbox.sh` giving a
  throwaway `HERMES_HOME`, separate Electron userData and a distinct app name
  (`apps/desktop/README.md:96-100`). **VERIFIED**
- **Effort:** S · **Impact:** med (developer experience) · **Risk:** low

### P3-2 · Per-tool timeout with a cooperative deadline
- **Problem in August:** MCP tools share `_TOOL_EXEC_TIMEOUT_S` (default 300 s, floor
  30 s) but there is no per-tool declaration or cooperative cancel. **VERIFIED**
- **What the reference does:** DeepSeek's `timeoutMs` per `ToolDefinition` with a
  cooperative deadline signal plus a hard-kill fallback, producing a structured
  `TOOL_TIMEOUT` result rather than an exception
  (`packages/guard/timeout-policy/README.md`). **VERIFIED**
- **Effort:** S · **Impact:** low-med · **Risk:** low

### P3-3 · Bump the local API token to a CSPRNG
- **Problem in August:** `backend.rs:88-112` derives the per-install token from two
  `RandomState::finish()` outputs plus pid/time. **VERIFIED**
- **What the reference does:** every reference uses the platform CSPRNG. **INFERRED**
- **Effort:** S · **Impact:** low · **Risk:** low (check `Cargo.lock` for `getrandom`)

### P3-4 · Promoted stage lists for startup/teardown order
- **Problem in August:** `lifespan` is 260 lines of prose-ordered boot; startup order
  is load-bearing (migrations before any store; brain restore before any read) yet
  nothing asserts it. **VERIFIED**
- **What the reference does:** opencode composes lifecycle as a state machine
  (`src/main/lifecycle/machine.ts`); OpenHands' Electron main stages the whole local
  stack before opening a window (`electron/main.mjs`). **VERIFIED**
- **Effort:** M · **Impact:** low-med · **Risk:** med (touching boot)

### P3-5 · Agent-manifest schema + a validate gate for project skills
- **Problem in August:** `.aug/skills` is discovered by walking; a malformed
  `SKILL.md` is skipped at runtime with a logged reason, and `check:skills` covers
  only the *bundled* tree. **VERIFIED**
- **What the reference does:** minimax-code ships a plugin manifest JSON Schema with a
  local validator that is the same code CI runs (`schemas/plugins/`,
  `docs/plugins/README.md:18-73`); OpenHands keeps verification skills read-only and in
  separate PRs. **VERIFIED**
- **Effort:** M · **Impact:** low · **Risk:** low

### P3-6 · A lightweight no-tool chat mode
- **Problem in August:** every turn pays the full tool-surface budget (progressive
  disclosure bounds it, but the surface is still built). **VERIFIED**
- **What the reference does:** minimax-code's `lightweight` mode omits tool schemas,
  skills, memory and workspace instructions entirely for a turn (README.md L150-170);
  DeepSeek's chat UI has a "final answer only" visibility mode. **VERIFIED**
- **Effort:** M · **Impact:** low (cost/latency on trivial turns) · **Risk:** low

### P3-7 · A `/restore`-style checkpoint for file-modifying tools
- **Problem in August:** edits are snapshotted per proposal (memory/skills) but there
  is no one-command workspace restore. **VERIFIED**
- **What the reference does:** Gemini snapshots project state into a shadow git repo
  before any file-modifying tool runs, and `/restore` reverts files + history +
  re-proposes the call (`docs/cli/checkpointing.md`). **VERIFIED**
- **Effort:** L · **Impact:** med · **Risk:** med (a second VCS-shaped store risks the
  one-store rule if it becomes authoritative — keep it derived)

---

## Candidate REMOVALS (consolidated)

| # | Removal | Evidence | Effort | Note |
|---|---|---|---|---|
| R1 | Unused MSI bundle target | `tauri.conf.json:32-35`; whole update pipeline is NSIS-only | S | owner call (enterprise?) |
| R2 | Triple binary-name hedging | `hooks.nsh:110-112`; `backend.rs:3360-3365` | S | after P0-3 |
| R3 | `_pollingWatch` (one-shot misnamed as a poller) | `environment_watcher.py:159-176` | S | v2 watcher covers it |
| R4 | M-7 disuse machinery (~90 lines + 3 keys) | `fact_retrieval.py:404-456, 555-583`; `rest.py:559` | S | P1-4 |
| R5 | `deferred_writes.py` + its `main.py` block | `services/deferred_writes.py`; `main.py:466-475` | S | P2-6 |
| R6 | Shadowed `/v1/models` in `proxy.py` | `routers/proxy.py:585-591` | S | P2-7 |
| R7 | `session_status` from `emit_types.py` | no emitter, no handler | S | P1-1 |
| R8 | Dead `wait_for=None` branch, duplicate `break`, `sandbox/runner.py` indirection | `providers/clients/base.py`; `mcp_client.py:1206-1208`; `sandbox/runner.py` | S | P2 |
| R9 | `_deriveFactKey`'s dead `scope` param; legacy usage-sidecar migration | `session_tools.py:159-174`; `skill_service.py:2586-2668` | S | self-documented in code |
| R10 | Do **not** remove `promptCacheLive` / `contextSectionsLive` | they have live readers | — | listed to prevent a wrong removal |

---

## Proposals explicitly REJECTED (with reasons)

| Proposal | Source | Why rejected |
|---|---|---|
| A second model as the permission judge / classifier | Goose `permission_judge.rs`; Claude auto mode | Non-deterministic, prompt-injectable, adds a failure mode per action. August's rule-based doors are cheaper and auditable. |
| Cordis-style "everything is a plugin" with Service/Provider/Consumer per capability | DeepSeek Harness | ~60 packages + generated catalogs + i18n triplets for one harness. August's FastAPI + Tauri shape cannot absorb it, and the maintenance surface is the opposite of the product's minimalism. |
| 5 compaction methods × 5 memory backends | oh-my-pi | A configuration matrix pi deliberately avoids. August's "absent = armed, 0 = off" is the right posture. Adopt *techniques*, never the knob surface. |
| Shell-at-config-load (`crushrc` executes Bash, `$(...)` in JSON) | Crush | Directly violates August's "repo-supplied instructions load; repo-supplied execution does not". |
| Model-authored memory written straight to the store with no review door | Crush (none), Claude auto-memory (writes directly) | August's review-before-write + rollback + human retire gate is the differentiator. |
| Check-only updates (print the command) | Crush, OpenHands | August's auto-download + explicit restart + in-place replace + signature verification is strictly better. Do not regress. |
| Restore a test suite / add a `test` script | (owner decision, 2026-10-17) | Forbidden by the repo contract. The right response is per-blind-spot detection (gates), not a suite. |
| A vector store or graph store for memory | (owner decision, standing) | Violates the one-store rule; BM25 + aliases + FTS5 already cover recall. |
| Subagents writing memory | Claude Code (subagents may have their own auto memory) | August's parent-turn-only door is a deliberate, documented safety boundary. Opening it is an owner decision (`06-open-questions.md` Q6). |
| 9 model roles (default/smol/slow/plan/commit/vision/task/advisor/tiny) | oh-my-pi | A provider matrix August does not have; user-pasted base URLs + per-model overrides are the minimalist answer. |

---

## Recommended implementation order

1. **P0-1 … P0-8** (security + data-loss + the one real relaunch bug). Each is
   small and independently verifiable; run the gate set after each.
2. **P1-2, P1-1, P1-4, P1-7** (correctness of what the user/model already sees).
3. **P1-3, P1-10, P1-9, P1-11** (coherence and observability).
4. **P1-5, P1-6, P1-8** (the three that change behaviour: compaction pre-pass,
   approval grants, compaction observability).
5. **P2** in the order listed; P2-1 (god-file split) last among P2 because it is the
   widest.
6. **P3** opportunistically, alongside whatever else is in flight.

Rationale for that order: everything in step 1 is a defect the repo's own rules
already call out; step 2 makes the surfaces honest; step 3 makes them legible;
step 4 changes behaviour and therefore deserves the most review; step 5 is
refactor-shaped and should not ride with a behaviour change.