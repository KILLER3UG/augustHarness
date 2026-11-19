# Hermes Agent (NousResearch/hermes-agent) — harness dive for August

Repo: `C:/Users/rober/AppData/Local/Temp/august-review/repos/hermes-agent` (read-only).
Time-boxed: 20 tool calls. Claims below are VERIFIED (file read) unless marked INFERRED.

## 1. Startup flow — VERIFIED (partial)
- CLI is the entry point: `hermes`, `hermes chat`, `hermes desktop`, `hermes serve`; installers are curl/PowerShell one-liners that delegate Python 3.14, Node, pnpm, ripgrep, FFmpeg to a package manager (`README.md:37-61`). INFERRED: boot order is CLI → profile/config (`~/.hermes`, `%LOCALAPPDATA%\hermes`) → agent_init family → provider/auxiliary resolution → gateway or TUI.
- Desktop: `pnpm run dev` from `apps/desktop` runs "Vite renderer + Electron, which boots the Python backend" (`apps/desktop/README.md:84-92`) — the Electron shell spawns the Python backend as a child; `apps/desktop/electron/backend-child.ts`, `backend-health.ts`, `backend-claim.ts`, `backend-probes.ts`, `backend-discovery.ts` (337 non-test electron files) own discovery, claim, health, and attach. INFERRED: multi-instance arbitration via a claim/lease file (`gateway/active_lease_io.py` also exists).
- Six terminal backends (local, Docker, SSH, Modal, Daytona, Vercel Sandbox) chosen per session (`README.md:29`).
- Profile-scoped homes: `_permanent_approved_by_home` in `tools/approval.py:62` proves one approval set per profile home; `plugins/memory/byterover/README.md:33` confirms `$HERMES_HOME/byterover/` is profile-scoped.

## 2. UI/UX patterns — VERIFIED (docs-level)
- Desktop features: streaming responses, live tool activity, structured tool summaries, side-by-side previews (web page/file/tool output in right pane), file browser, voice, settings/onboarding, built-in updates (`apps/desktop/README.md:10-25`).
- macOS screenshot shortcut: capture frontmost window of *any* app and attach to the composer; off by default, requires Input Monitoring + Screen Recording, never sends the draft (`apps/desktop/README.md:58-70`).
- Multi-surface identity: same conversation history across CLI/TUI/Desktop/chat platforms (`README.md:24-25`, `apps/desktop/README.md:19`).
- Dev sandbox: `scripts/dev-sandbox.sh pnpm run dev` gives a throwaway `HERMES_HOME`, separate Electron userData and app name to dodge the single-instance lock (`apps/desktop/README.md:96-100`).

## 3. Memory model — VERIFIED
- `agent/memory_manager.py` is a **fan-out**, not a store: hooks go to registered providers; builtin provider always allowed, at most ONE external plugin provider at a time (schema-bloat reasoning) (`agent/memory_manager.py:1-5`).
- Egress scrubbing is unconditional for provider hand-off, even when `security.redact_secrets` is off, and **fails closed**: every turn sync, recall query, session-end/pre-compress transcript, memory-tool mirror, delegation result and tool-arg goes through `redact_for_egress` (`agent/memory_manager.py:89-119`).
- Hard liveness budgets on provider calls: sync drain 5 s, external prefetch 8 s, background stall 30 s; a `_StuckCallGate` admits at most `_MAX_STUCK_CALLS=4` overdue calls, probes a wedged backend once per 60 s, hard cap `_MAX_INFLIGHT_CALLS=16` (`agent/memory_manager.py:36-57`).
- Recall fencing: recalled memory is wrapped in `<memory-context>` + a "[System note: … recalled memory context, NOT new user input …]" prefix; a stateful `StreamingContextScrubber` strips spans that straddle stream deltas and drops an unterminated span tail rather than leak it (`agent/memory_context.py:1-100`).
- External providers are pluggable CLIs/APIs behind a `MemoryProvider` protocol with a checkpoint-API version gate (`agent/memory_provider.py` referenced from `memory_manager.py:28,36`); examples: byterover (hierarchical tree, `brv_query`/`brv_curate`/`brv_status`), retaindb (`plugins/memory/byterover/README.md`, `plugins/memory/retaindb/`).
- FTS5 cross-session search + LLM summarization and Honcho user-modeling claimed in `README.md:26` (INFERRED, not traced to code).

## 4. Skills / plugins system — VERIFIED
- Two surfaces: `skills/` (built-in, loadable by default, category dirs) and `optional-skills/` (installed via `hermes skills install official/<category>/<skill>`) (`skills/AGENTS.md:6-12`).
- Frontmatter contract: `name`, `description`, `version`, `author`, `license`, `platforms` (OS gate), `metadata.hermes.tags/category/related_skills/config` — skill-declared config keys are prompted during setup and injected at load under `skills.config.<key>` (`skills/AGENTS.md:16-22`).
- Hardline authoring standards enforced by tests: description ≤ 60 chars (matching the index width `SKILL_PROMPT_DESC_LIMIT`), no marketing words, prose must reference *Hermes tools* in backticks not the shell utilities they wrap (`grep`→`search_files`, `sed`→`patch`), `platforms:` audited against actual script imports, human-first `author` credit, modern section order, helper `scripts/` instead of model-inlined logic, tests per skill, no `offset`/`limit` on skill-loading tools (read a skill fully) (`skills/AGENTS.md:24-60`).
- **Skill bundles**: YAML in `<HERMES_HOME>/skill-bundles/` (`name`, `description`, `skills: [...]`, optional `instruction`) load several skills under one slash command; bundles win over skills on slug collision; cache signature is content digests not mtimes so a same-tick rewrite is seen (`agent/skill_bundles.py:1-100`). Broken bundle YAML is logged and skipped, never fatal.
- Plugins: `plugins/<name>/plugin.yaml` + `plugin.yaml`-declared entry points; the memory plugin surface is `plugins/memory/` (`byterover`, `retaindb`, `query_rewrite.py`, `config_schema.py`) — i.e. plugins can also supply *only* a config schema + rewrite hook.

## 5. Tool + permission model (approval machinery) — VERIFIED
- `tools/approval.py` is a **facade + session state**: pending approvals, per-session allowlist, yolo set, permanent allowlist (per profile home), denial breaker, gateway queues, all under one `RLock` (`tools/approval.py:51-62`).
- Composition split into leaves, each with one job: `approval_detection` (hardline/dangerous patterns), `approval_context` (contextvars/config/session platform), `approval_floors` (pre-gate blocks + allowlist match), `approval_prompt` (CLI prompt, plugin transports, MCP elicitation), `approval_gateway_wait` (blocking round-trip), `approval_smart` (guardian LLM), `approval_human_wait`, `approval_yolo`, `approval_provenance`, `approval_notify_lease` (`tools/approval.py:1-42`).
- **Floors run before any bypass**: hardline patterns, `sudo -S` password piping, and the user's own `approvals.deny` globs match before yolo/`mode: off`/cron approve-mode; deny rules are fnmatch over deobfuscated/normalized command variants so `r\m` or `git st""atus` can't sidestep (`tools/approval_floors.py:1-42`). The permanent allowlist is the only floor that runs *after* yolo.
- YOLO is frozen at import: reading `os.environ` per call would let an in-process skill set it and bypass every check — named as a prompt-injection escalation path (`tools/approval.py:46-48`).
- **Denial circuit breaker**: after `approvals.denial_breaker_threshold` (default 3) consecutive guardian DENYs in a session the deny text escalates to a hard-stop; only TOOL RESULT text changes, so prompt cache is untouched; tally capped at 256 sessions with LRU eviction (`tools/approval.py:64-96`).
- Parser-limit blocks persist the offending command as a runnable script so recovery is one turn (`bash <file>`) instead of two — and it still goes through the normal execution pipeline (`tools/approval_floors.py:54-60`).
- Tool surface itself: 315 files in `tools/`, `toolsets` module defines core toolsets and per-session platform gating (`tools/tool_search.py:1-31`).

## 6. Sandboxing — VERIFIED (partial)
- Shared path validator `validate_within_dir` resolves symlinks and `..` before comparing (`tools/path_security.py:9-16`), plus a cheap `has_traversal_component` pre-check and an `_UNSAFE_PATH_CHARS` regex that rejects control chars and Unicode line separators (NEL/LS/PS) because legal POSIX filenames can forge log lines and break line-delimited protocols (`tools/path_security.py:20-25`).
- Six terminal backends give container/remote isolation as a session choice rather than a single sandbox (`README.md:29`).
- `gateway/cgroup_cleanup.py`, `gateway/authz_mixin.py`, `gateway/host_attach.py` exist (names only — not read).
- INFERRED: sandboxing is per-backend and per-path, not a global seccomp layer; not examined in detail.

## 7. Context management / compaction — VERIFIED (structure)
- `agent/context_compressor.py` is 5,679 lines and a **mixin aggregator**: `PreLlmSkipMixin`, `CompactionWindowMixin`, `ProactivePruneMixin`, `SummaryDispatchMixin`, `CompressionTelemetryMixin`, plus `MicroCompactionMixin` (`agent/context_compressor.py:32-45`).
- Design summary in the module docstring: cheap auxiliary model summarizes the **middle** turns; head and tail protected; iterative summaries; token-budget tail; tool-output pruning first; scaled budgets (`agent/context_compressor.py:1-2`). Helpers confirm it: `elide_middle`, `_elision_marker`, `demote_stale_tail_tools`, `lean_tail_budget`, `tail_cut_pricer`, `outbound_image_retire_count` (`:16-42`).
- **Summary route pinning** is a ContextVar, not compressor state, because the compressor is shared with a detached stalled worker; a single-use pin lets exactly one retry use a fallback chain entry, then a `DETERMINISTIC_SUMMARY_ROUTE` sentinel degrades to a no-LLM deterministic summary instead of re-entering the same silent stream (`agent/context_compressor.py:59-115`).
- Skill safety rule is re-injected across elision so a compacted skill excerpt keeps its safety clause (`context_compressor_tail.py` via `:39-42`).
- Separate family: `micro_compaction.py`, `native_compaction.py`, `prepared_compaction.py` (listed in task; only micro_compaction imported here).

## 8. Subagent design — VERIFIED
- `tools/delegate_tool.py` spawns child `AIAgent`s with a fresh conversation, their own `task_id` (terminal session + file-ops cache), parent toolsets minus child-blocked tools, focused system prompt with goal/context as the first user turn; single-task and batch modes; the parent sees only the call and the summary, never the child's intermediate tool calls or reasoning (`tools/delegate_tool.py:1-12`).
- Child restrictions are a hard denylist, not a copy: `delegate_task` (no recursion), `clarify` (no user interaction), `memory` (no writes to shared MEMORY.md), `send_message` (no cross-platform side effects), `cronjob_manage` (no scheduling in the parent's name); default child toolsets are `terminal`, `file`, `web` (`tools/delegate_tool_toolsets.py:14-24`). "Children never gain tools the parent lacks" and toolsets whose tools are all blocked are dropped wholesale (`:39-80`).
- Registry owns control actions: `interrupt_subagent`, `steer_subagent`, `list_active_subagents`, `is_spawn_paused`, descendant checks, attribution (`tools/delegate_tool_registry.py` via `delegate_tool.py:44-49`).
- Budgets: max concurrent children, max async children, oneshot cap, spawn depth cap, per-child timeout, credential-pool leasing, optional worktree isolation (`delegate_tool_config` via `:31-37`).
- Subagent approval callbacks are wired into the terminal tool (`_set_subagent_approval_cb`, `tools/delegate_tool.py:20`).

## 9. Model / provider handling — VERIFIED (config surface)
- `cli-config.yaml.example` is 2,427 lines and a **migrated schema** (`_config_version: 53`), seeded by installers and used by `hermes update` to know which one-time migrations already ran (`cli-config.yaml.example:7-10`).
- Provider selection is a single `model.provider` enum with `"auto"` credential autodetection plus ~20 named providers (openrouter, nous OAuth, nous-api, anthropic, openai-codex, gemini, zai, kimi-coding, minimax/minimax-cn, huggingface, nvidia, xiaomi, arcee, ollama-cloud, deepinfra, …), each with its documented env var (`:76-100`).
- Runtime knobs documented where users hit them: `nofile_soft_limit`, `malloc_arena_max`, SQLite `journal_mode` WAL→DELETE fallback for virtiofs/NFS/SMB with no live downgrade, `synchronous` floor on macOS (`:20-49`).
- Auxiliary models are a separate route class (`auxiliary.compression`, `agent/auxiliary_client.py` via `context_compressor.py:23-31,64`) — cheap model for summaries, main model for turns.

## 10. Update mechanism — VERIFIED (docs + electron)
- Update ownership is delegated to whoever owns the installed artifact: Windows App Installer for sideload MSIX, Microsoft Store for Store packages, `electron-updater` for macOS bundles, checkout handoff for source builds; `hermes update` updates managed source checkouts only and never rewrites a bundled payload (`apps/desktop/README.md:47-54`).
- Desktop claims "built-in updates pull the latest agent and rebuild the app in place" (`apps/desktop/README.md:24`); electron side has `app-updater.ts`, `app-relaunch.ts`, `appinstaller-checker.ts`, `app-installer-file.ts`, `app-version.ts`, `active-runtime-state.ts` — i.e. a check → stage → relaunch pipeline plus an MSIX-specific checker path.
- Android/Termux ships a signed APT repo with `stable`/`canary` channels (`README.md:59`).

## 11. Config / settings — VERIFIED
- `~/.hermes/config.yaml` (or `%LOCALAPPDATA%\hermes`), editable with `hermes config set <section.key> <value>`; documented secret env vars in `.env` take precedence over their corresponding settings (`cli-config.yaml.example:1-5`).
- Sections seen: `database`, `runtime`, `attachments` (hermes-home vs workspace staging), `plugins` (clone timeout), `model` (provider + model + per-provider keys), plus referenced-but-unread `gateway.bot_loop_guard`, `approvals.*`, `tools.tool_search.*`, `skills.config.*`, `memory.provider`, `security.redact_secrets`.
- Settings are read per profile, so a session bound to profile X follows X's own config (`cli-config.yaml.example:55-56`).

## 12. Error recovery / loop guards — VERIFIED
- `gateway/bot_loop_guard.py`: bot-authored inbound messages get a sliding-window budget (20 events / 300 s → 600 s cooldown) **on top of** an admission flag, because `{PLATFORM}_ALLOW_BOTS` alone never stops two profiles replying to each other. Chain rules are tighter: a human message starts a new chain, each bot message is one hop, `max_hops` caps them, a repeated identical text is one hop not a new ask, and with `bot_replies_expect_reply` false (default) a bot answering the reply this bot delivered is not answered again (`gateway/bot_loop_guard.py:1-35`).
- `agent/turn_recovery.py` is a chain of one-shot recovery handlers inside the turn retry loop: each returns True (request repaired in place, loop continues with the *same* retry_count) or False (fall through), guarded by `TurnRetryState` (`agent/turn_recovery.py:1-8`). Handlers cover Unicode/ASCII encoding fallback, corrupt-image and non-ASCII/surrogate sanitization, interrupted tool-sequence closing, credential refresh after 401, format-error repair, welcome-tier, budget clamping, output-cap parsing, z.ai overload backoff ceiling, thinking-timeout guidance (`:26-40,46-60,118-654`).
- Recovery handlers are ordered so the *cheap and certain* repairs run before generic backoff; classification (`agent/error_classifier.py`) drives failover reasons.

## 13. Clever / notably bad
Clever:
- Egress scrub that **fails closed** on every provider hand-off, independent of the local-logging redaction setting (`agent/memory_manager.py:89-119`).
- Import-frozen YOLO flag to kill the prompt-injection escalation path (`tools/approval.py:46-48`).
- Deny rules matched over deobfuscated command variants, before every bypass (`tools/approval_floors.py:23-42`).
- Denial breaker that only edits TOOL RESULT text — safety without breaking prompt cache (`tools/approval.py:64-70`).
- Deterministic summary sentinel instead of a second stalled LLM call (`agent/context_compressor.py:102-115`).
- Content-digest bundle cache instead of mtimes (`agent/skill_bundles.py:40-50`).
- Streaming-aware memory fence scrubber that drops rather than leaks an unterminated span (`agent/memory_context.py:94-100`).
- Bot loop guard with hop semantics, not just a rate limit (`gateway/bot_loop_guard.py`).
- Authoring standard that bans the *wrapped* tool names from prose so the model keeps using the real surface (`skills/AGENTS.md:34-39`).

Notably bad / smells:
- `agent/context_compressor.py` at 5,679 lines with five mixins, ContextVar-pinned routes, and comments citing issue numbers (#112420, #96603) — a single file nobody can hold in their head; the pin exists *because* the compressor is shared with a detached worker.
- `tools/approval.py` is a facade whose leaves must read facade-owned state back through `tools.approval` at call time (`tools/approval.py:10-11`) — deliberate, but it makes the leaves non-obvious and order-dependent.
- `cli-config.yaml.example` at 2,427 lines with `_config_version: 53` is a config surface where migrations are load-bearing; a typo in `defer` is only warned about, never fatal (`tools/tool_search.py:66-75`).
- 315 files in `tools/` and 337 non-test electron files is a lot of surface for a "local-first" product.

## Ideas worth stealing for August
- **Hard-frozen security flags at import** (`tools/approval.py:46-48`): read yolo-mode once at process start so no in-process skill can flip it. August's path doors / secrets live in `backend-py/app/services/`; a module-level frozen read is a one-line change with a real escalation story.
- **Pre-bypass deny floors matched over deobfuscated variants** (`tools/approval_floors.py:23-42`): August's command guard should normalize `r\m` / `git st""atus` before matching, and run deny rules *before* any yolo/approve-all path.
- **Bounded external-provider liveness gate** (`agent/memory_manager.py:36-57`): August's memory and any plugin provider calls should have per-call timeouts plus a stuck-call admission cap so a wedged backend can't pin unbounded threads. Fits the one-memory-store rule without adding a second store.
- **Fenced recall with a streaming scrubber** (`agent/memory_context.py:15-100`): August already has a parent-turn-only memory write door; adding `<memory-context>` fencing plus a delta-safe stripper keeps recalled facts from being mistaken for user input and keeps the fence out of the UI.
- **Denial circuit breaker that only rewrites the tool result** (`tools/approval.py:64-96`): August's approval prompts for terminal/file ops could escalate after N consecutive model-side denials without any history surgery — prompt-cache friendly and cheap.
- **Subagent denylist as a first-class constant** (`tools/delegate_tool_toolsets.py:14-24`): August's subagents already cannot write memory; make that a single `DELEGATE_BLOCKED_TOOLS` set that includes `delegate_task` (no recursion), `send_message`, and any cron/schedule tool, and derive the dropped toolsets from it so the two can't drift.
- **Child toolsets default to a minimal trio** (`terminal`, `file`, `web`) with "children never gain tools the parent lacks" (`tools/delegate_tool_toolsets.py:24-80`): matches August's subagent read-only posture and shrinks the child's tool schema.
- **Bot/agent loop guard with hop semantics** (`gateway/bot_loop_guard.py:1-35`): if August ever gains a chat-platform or bot surface, cap hops per human message rather than just rate-limiting; repeat-identical-text counts as one hop.
- **Deterministic fallback summary route** (`agent/context_compressor.py:102-115`): when August's auxiliary/compaction model stalls, degrade once to a deterministic summary instead of re-entering the same silent stream — directly relevant to `backend-py/app/services/` compaction.
- **Compact-first pruning order** (head/tail protected, middle summarized, tool outputs pruned first) (`agent/context_compressor.py:1-2,39-42`): a cheap ordering rule for August's context builder; also re-inject skill safety rules across elision.
- **Skill bundles as YAML aliases with content-digest caching** (`agent/skill_bundles.py:1-100`): August's `.aug/skills` could gain multi-skill slash bundles; content-digest signatures avoid mtime-skew misses, and a broken bundle must log-and-skip, never break discovery.
- **Per-skill `config` frontmatter prompted at setup and injected at load** (`skills/AGENTS.md:16-22`): a clean way for August's skills to declare required settings without a global settings explosion.
- **Desktop dev sandbox script** (`apps/desktop/README.md:96-100`): throwaway `HERMES_HOME` + separate userData + distinct app name to dodge the single-instance lock. August's Tauri dev loop would benefit from the same one-liner.

## Anti-patterns to avoid
- **A 5.6k-line compressor with five mixins** (`agent/context_compressor.py`): the file exists because compaction was never decomposed; its ContextVar route-pin workaround is a symptom. August's compaction should be small, single-purpose modules with explicit inputs.
- **Security state reached back through a facade** (`tools/approval.py:10-11`): leaves that import the facade to read its lock/queues make ordering invisible and testing awkward. Pass state explicitly.
- **Mtime-based cache signatures** (`agent/skill_bundles.py:40-50` is the *fix*): timestamp caches miss same-tick rewrites and clock skew; use content digests for small files.
- **Config surface as a migration ledger** (`cli-config.yaml.example:10`, 2,427 lines): `_config_version: 53` means upgrades are gated on migration bookkeeping; a smaller documented surface plus defaults-in-code ages better.
- **Silent config fallbacks** (`tools/tool_search.py:66-75` only warns): a scalar where a list is expected should be loud and, ideally, fail the specific setting — silently using the default is how users think they shrank a surface and didn't.
- **Unbounded tool file count** (315 in `tools/`, 337 electron): broad surfaces are hard to audit for a local-first product; August's smaller tool set is a feature.

## Not applicable to August (and why)
- **Multi-platform chat gateway** (Telegram/Discord/Slack/WhatsApp/Signal) and the bot loop guard's platform admission flags (`README.md:25`): August is a desktop workbench, no bot-to-bot traffic.
- **Six terminal backends / serverless persistence** (Modal, Daytona, Vercel Sandbox, Docker, SSH) (`README.md:29`): August is local-first; a container escape hatch is optional, not core.
- **Electron + electron-updater / MSIX / Microsoft Store update ownership** (`apps/desktop/README.md:47-54`): August ships Tauri with its own in-place updater and two install guards; the *idea* (delegate to artifact owner) transfers, the machinery does not.
- **Android/Termux APT channels** (`README.md:59`): out of scope.
- **Honcho user-modeling / optional memory backends (byterover, retaindb)** (`README.md:26`, `plugins/memory/`): August's rule is one memory store; a second provider protocol would violate it.
- **Research/trajectory tooling** (`README.md:30`): not a product surface for August.

## Coverage ledger
Read (head windows):
- `README.md:1-80`
- `agent/memory_manager.py:1-120` (of 962)
- `agent/memory_context.py:1-100` (of 199)
- `agent/skill_bundles.py:1-100` (of 213)
- `agent/context_compressor.py:1-120` (of 5,679)
- `tools/approval.py:1-100` (of 1,437)
- `tools/approval_floors.py:1-60` (of 209)
- `tools/tool_search.py:1-80` (of 652)
- `tools/delegate_tool.py:1-80` (of 1,006)
- `tools/delegate_tool_toolsets.py:14-24` (grep window)
- `tools/path_security.py:1-25`
- `apps/desktop/README.md:1-100` (of 274)
- `gateway/bot_loop_guard.py:1-60` (of 220)
- `agent/turn_recovery.py:1-60` (of 1,969) + `grep -n "def "` symbol list
- `cli-config.yaml.example:1-100` (of 2,427)
- `plugins/memory/byterover/README.md:1-41`
- `skills/AGENTS.md:1-60` (of 81)

Skipped, with reason:
- `agent/agent_init*.py`, `agent/turn_recovery.py` body beyond symbol list, `agent/micro_compaction.py`, `native_compaction.py`, `prepared_compaction.py`: budget; structure inferred from imports and docstrings.
- `tools/approval_smart.py`, `approval_prompt.py`, `approval_gateway_wait.py`, `approval_yolo.py`: composition described by `tools/approval.py:1-42` facade docstring; bodies not read.
- `plugins/context_engine`, `plugins/delegation`, `plugins/kanban`, `plugins/cron_providers`, `plugins/dashboard_auth`, `plugins/image_gen`, `plugins/config_backends`, `plugins/teams_pipeline`, `plugins/security-guidance`: only `plugins/memory/` was in the read list.
- `gateway/` beyond `bot_loop_guard.py` (authz_mixin, approval_actor, hosted_room_*, cgroup_cleanup): not examined.
- `apps/desktop/electron/*` (337 non-test files): only the README and a filename listing; update/relaunch internals inferred from filenames.
- `agent/memory_provider.py`, `memory_manager_tools.py`, `learning_graph.py`, `learning_mutations.py`, `agent/anthropic_*`, `auxiliary_*.py`, `agent/agent_init*.py`: not examined.
- `skills/` category contents: `ls` only (categories: apple, autonomous-ai-agents, creative, devops, email, media, note-taking, productivity, research, social-media, software-development, web).
- `tools/` beyond the listed files (315 total), `cron/`, `apps/bootstrap-installer/`, root `AGENTS.md`: not examined.
- Any code execution, tests, network: forbidden by the task.