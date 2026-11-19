# Coverage ledger — every repo, every file read or skipped

Two parts: **A. The August repo** (what each of the six audits opened and skipped),
and **B. The reference repos** (what each deep-dive opened and skipped).

Scope of the August tree, for calibration (`find` counts, this pass):

| Area | Files |
|---|---|
| `backend-py/app` | 819 |
| `frontend/desktop/src` | 550 |
| `frontend/desktop/src-tauri/src` | 6 |
| `frontend/mobile` | 1,395 |
| `scripts/` | 46 |
| `skills/` | 26 |
| `docs/` | 37 |
| `.github/workflows/` | 2 |

Roughly 110 August files were opened at source level across the six audits — about
4% of the tree by count, concentrated on the subsystems the brief named. Every
audit produced a per-area ledger naming what it skipped and why; those are
reproduced below.

---

## A. August repo — `C:/Dev/augustHarness` @ `9804b069`

### A1. Startup / shutdown / Tauri shell / updater / release

**Read in full:** `src-tauri/src/backend.rs` (4,559 ln), `lib.rs` (201), `main.rs`
(6), `tray.rs` (59), `tauri.conf.json` (67), `windows/installer.nsi` (1,052),
`windows/hooks.nsh` (195), `app/main.py` (807), `services/daemon_manager.py` (628),
`services/health_monitor.py` (205), `services/environment_watcher.py` (253),
`lib/local_api_auth.py` (429), `scripts/dev-app.mjs` (212), `dev-tauri.mjs` (197),
`start-backend.mjs` (144), `check-minisign.mjs` (193), `minisign-verify.mjs` (272),
`release-desktop.mjs` (735), `check-version-sync.mjs` (125),
`prepare-desktop-backend.mjs` (693), `desktop-backend-payload.mjs` (233),
`sync-tauri-version.mjs` (83), `with-tauri-signing.ps1` (35), `install.sh` (102),
`install.ps1` (155), `.github/workflows/release-desktop.yml` (179), `ci.yml` (138),
`hooks/useAppUpdate.ts` (~480), `docs/UPDATE_VERIFICATION.md`, `docs/agent/product.md`
(162), `docs/agent/release.md` (70).

**Partial:** `services/data_migrations.py`, `docs/STATUS.md`,
`components/shell/BackendStatusPill.tsx`, `sections/chat/offline-queue-store.ts`.

**Skipped, with reason:** `frontend/mobile/**` (area 10); backend harness/chat/memory
internals (areas 5–8); `src-tauri/Cargo.toml`/`Cargo.lock`/`capabilities/*.json`
(bundle/updater behaviour fully covered by `tauri.conf.json` + `backend.rs`); no
deep-link plugin or scheme handler exists anywhere in the Rust shell (grep returned
none) — recorded as "not implemented" rather than a finding; `scripts/gates.mjs` and
the other `check-*.mjs` (referenced but not deep-read; the OpenAPI version-source
contract was verified via `check-version-sync.mjs` + `release.md:15-28`);
`useBackendSetup.ts` and `api/client.ts` (verified by targeted grep).

### A2. UI shell / settings / theming / chat rendering / streaming

**Read in full:** `lib/icons.tsx`, `styles.css`, `components/chat/ActivityGroup.tsx`,
`lib/tool-summary.ts`, `components/chat/DisclosureRow.tsx`,
`components/chat/ThinkingDisclosure.tsx`, `store/promptCacheLive.ts`,
`store/contextSectionsLive.ts`, `sections/chat/makeStreamHandlers.ts` (1,265 ln, three
passes), `api/workbench/streamEvents.ts`, `api/schemas/workbench.ts`,
`components/chat/SubagentTimeline.tsx`, `components/chat/focused-subagent.ts`,
`lib/markdownHeadings.ts`, `lib/phase-labels.ts`,
`sections/chat/composer/ContextUsageReadout.tsx`, `components/overlays/Backdrop.tsx`,
`hooks/useFocusTrap.ts`, `components/settings/SettingsSelect.tsx`.

**Partial:** `ClarifyTool.tsx` (1–229 of 512 + greps), `ChatMarkdown.tsx` (1–120 of
684 + demotion greps), `AssistantMessageContent.tsx` (160–269 of 362 + greps),
`ToolCallItem.tsx` (1–130 of 222), `realtime/bridge.ts` (1–120 of 316),
`useResizablePane.ts` (140–187 + greps), `SessionSidebar.tsx` (1–80 of 113),
`SettingsPage.tsx` (400–439 + greps), `settings-registry.ts` (structure + greps).

**Carried from a prior session** (read there, not re-verified line-by-line here; no
finding rests solely on them): `App.tsx`, `routes.ts`, `components/shell/ChatLayout.tsx`,
`BackendStatusPill.tsx`, `styles/tokens.css`.

**Skipped, with reason:** `sections/chat/ChatThread.tsx` (1,716 ln — head + greps; it
composes the audited pieces); `ChatThreadComposer.tsx` (780) / `ChatThreadMessagePane.tsx`
(388) — greps only; the remaining `components/chat/tool/*`, `ArtifactCard`,
`ChangesCard`, `CircuitArtifactCard`, `DiffView`, `SearchResultsCard`, todo/recovery
cards — enumerated; `styles/base.css` (477), `settings.css` (58), `chat.css` —
measured + greps (the token source `tokens.css` was read in a prior session);
`store/*` beyond the two candidates (21 files, none implicated).

### A3. Memory / recall / skills / self-evolution

**Read in full or at depth:** `memory_store/brain.py` (1,324 ln),
`memory_store/mirror.py`, `memory_store/fact_retrieval.py`, `memory_store/rest.py`,
`memory_store/transcript_index.py`, `memory_store/kv.py`, `memory_store/tables.py`,
`tool_registrations/session_tools.py` (memory half, 1–1,390),
`tool_registrations/skill_tools.py`, `tool_registrations/harness_tools.py`,
`services/auto_review.py`, `services/harness_rails.py`,
`services/harness_self_improve.py` (430–794), `services/skill_service.py`
(122–421, 580–759, 1,349–1,468, 2,072–2,828), `services/skill_distiller.py`
(1,569–1,985), `services/memory_import.py` (231–470), `services/retired_sqlite.py`
(structure + WAL staging), `services/brain_backup.py` (structure + restore),
`services/skill_packs.py` (structure + preview/scan/install),
`services/learning_scheduler.py` (structure + review job),
`services/harness_promote.py` (write site), `services/workbench/workbench.py`
(boot-index freeze 1,010–1,084; tail assembly 3,355–3,484),
`services/workbench/subagent.py` (deny set 94–99), `services/session_scope.py`,
`services/capabilities_prompt.py`, `services/brain_config_service.py` (defaults),
`memory_store/consolidation.py` (1–130, 440–1,011), plus the spec and area docs.

**Skipped, with reason:** `memory_store/{messages,sessions,transcript_blocks,wire,timeutil}.py`
(periphery; behaviour spot-checked through callers); `skill_versions.py`,
`memory_quality.py` (call sites only); `episode_miner.py` (1,257 ln — structure
only); the distiller's `apply_verdict` body (1,178–1,568); frontend Memory/Skills UI
internals (presence and routing verified, flows not walked); `routers/august.py`,
`routers/brain_config.py` (only the memory/skill door call sites).

### A4. Tools / MCP / sandbox / permissions / routing / local models

**Read:** `tool_registry.py`, `tool_policy.py`, `tools/tool_bridges.py`,
`tools/mcp_client.py` (4 chunks), `tools/chat_secrets.py`,
`workbench/tool_defs_cache.py`, `workbench/json_salvage.py`,
`workbench/loop/exec.py` (targeted), `workbench/loop/surface.py`,
`workbench/workbench.py` (guard/approval/disclosure regions),
`workbench/permissions.py`, `workbench/subagent.py`, `routers/mcp.py`,
`routers/harness_mcp.py`, `lib/gateway_auth.py`, `lib/local_api_auth.py`,
`lib/local_api_guard.py`, `sandbox/{hardline,policy,paths,runner}.py`,
`sandbox/backends/container.py`, `hooks/{secret_guard,registry,lifecycle}.py`,
`secret_store.py`, `service_connections.py` (420–540 + greps),
`providers/clients/{base,openai,anthropic,rate_gate}.py`,
`providers/{api_format,model_resolver,route_resolver,resolver}.py`,
`workbench/providers.py` (470–590), `model_service.py` (400–519), `alias_service.py`,
`cost_estimator.py`, `fallback_service.py`, `routers/proxy.py` (1–300),
`routers/brain_config.py` (600–679), `llama_runtime.py`, `hf_hub.py`,
`hf_downloads.py`, `comfyui.py`, `data/mcp-servers.json` (full; contains a live secret,
redacted in the report).

**Skipped, with reason:** `frontend/desktop/**` (Tauri token doors verified via backend
docstrings only); most of the 60+ routers (only area-relevant ingress read; the rest are
CRUD over services already covered); full proxy adapters (verified by grep on the tool-arg
parse sites); `workbench.py` bulk (7,683 ln — only the guard/approval/disclosure regions);
`alias_mapping_service.py` body, `hf_downloads.py` tail, `llama_runtime.py` tail
(inventoried and symbol-mapped only).

### A5. Backend routers / services / daemons / persistence / mobile

**Read in full:** `main.py` (807), `config.py` (113), `data_migrations.py` (227),
`daemon_manager.py` (628), `best_effort.py` (54), `deferred_writes.py` (37),
`health_monitor.py` (205), `frontend/mobile/App.tsx` (504).

**Partial:** `error_families.py` (1–40), `cognitive_boot.py` (1–153),
`filestore.py` (40–100, 132–160, 198–239), `learning_scheduler.py` (1–45, 439–470),
`environment_watcher.py` (1–40 + symbol index), `memory_store/__init__.py`,
`brain_config_service.py` (symbol + range index), `routers/proxy.py` (33–80, 408–591),
`routers/models.py` (70–110), `frontend/mobile/pairing.ts`, `mobile/package.json`,
`mobile/scripts/audit-mobile-parity.js` (1–50 of 301), `docs/ROADMAP.md` (80–110,
215–235), `scripts/check-loop-imports.mjs`.

**Machine scan:** all 47 router modules for `APIRouter(prefix=…)` + decorator paths —
448 routes, duplicate detection.

**Skipped, with reason:** `workbench.py` (7,683 ln — line count + symbol index only;
flagged as a removal candidate); `skill_service.py` (2,828), `skill_distiller.py`
(2,184), `memory_store/consolidation.py` (1,888), `rest.py` (1,396), `brain.py`
(1,324) — line counts only; `routers/workbench.py` (2,959) — route decorators and
`HTTPException` counts only; `learning_scheduler.py` job bodies, `environment_watcher.py`
internals — symbols and entry points only; desktop frontend, gateway messaging internals,
provider clients (other areas).

### A6. CI / gates / observability / developer experience

**Read:** `scripts/gates.mjs` (full), `.github/workflows/ci.yml` (gate jobs),
`.github/workflows/release-desktop.yml` (needs/publish structure), `package.json` (all 40
scripts + the `check:*` aliases), `scripts/check-sse-parity.mjs` (SCHEMA_ONLY, the six
checks, the report lines), the `scripts/*.mjs` inventory (28 files), the
`{lint-budget,render-baseline,design-baseline,naming-baseline}.json` presence,
`scripts/audit-mobile-parity.js` (1–50), `docs/DEVELOPER_GUIDE.md`,
`docs/CONFIGURATION.md`, `frontend/mobile/package.json`,
`backend-py/app/services/{event_log,turn_outcomes,health_monitor,error_families}.py` (symbol
index), `backend-py/pyproject.toml`, `backend-py/scripts/harness_eval.py` (existence +
`--ablation` flag).

**Run:** `node scripts/gates.mjs static` → passed. It is the only gate executed; the others
need the backend interpreter or npm deps, and one (`gen:api`) regenerates a tracked file, so
it is a write and out of scope for a read-only audit.

**Skipped, with reason:** the full bodies of the 28 gate scripts (the pattern and rationale
were read from `gates.mjs` + `check-sse-parity.mjs` + the headers; reading all 28 would not
change a finding); the CI workflow's container/build steps (not gate logic); the mobile parity
audit beyond its base-URL default; `scripts/bench/` and `scripts/harness_eval.py` bodies (the
ablation design is documented in `docs/agent/harness.md`, which was read instead).

**Provenance:** delegated to a time-boxed subagent that produced a draft skeleton and stalled
without writing findings; the audit was completed directly instead. This is recorded because
the coverage above is first-hand, not a subagent's self-report.

---

## B. Reference repos — cloned into scratch, read-only

All clones are `--depth 1` snapshots of `main`/`master` at the time of the run
(2026-11-14), outside the August tree. Sizes are working-tree file counts.

| Repo | Clone | Files | Deep-dive note |
|---|---|---|---|
| NousResearch/hermes-agent | `repos/hermes-agent` | 25,535 | `harness-hermes-agent.md` |
| deepseek-ai/deepseek-harness | `repos/deepseek-harness` | 21,632 | `harness-deepseek-harness.md` |
| sst/opencode | `repos/opencode` | 11,473 | `harness-minimax-kilocode-opencode.md` |
| Kilo-Org/kilocode | `repos/kilocode` | 11,271 | `harness-minimax-kilocode-opencode.md` |
| can1357/oh-my-pi | `repos/oh-my-pi` | 10,538 | `harness-pi-and-oh-my-pi.md` |
| openai/codex | `repos/codex` | 10,287 | `harness-codex-goose-gemini.md` |
| MiniMax-AI/minimax-code | `repos/minimax-code` | 4,562 | `harness-minimax-kilocode-opencode.md` |
| earendil-works/pi | `repos/pi` | 4,533 | `harness-pi-and-oh-my-pi.md` |
| google-gemini/gemini-cli | `repos/gemini-cli` | 3,047 | `harness-codex-goose-gemini.md` |
| All-Hands-AI/OpenHands | `repos/OpenHands` | 2,899 | `harness-crush-openhands.md` |
| block/goose | `repos/goose` | 2,585 | `harness-codex-goose-gemini.md` |
| charmbracelet/crush | `repos/crush` | 1,418 | `harness-crush-openhands.md` |
| Aider-AI/aider | `repos/aider` | 691 | `harness-aider-openhands-crush-claude.md` |
| Claude Code / Desktop | — (no source) | — | `harness-claude.md` |

### Per-repo coverage notes (from each dive's own ledger)

**hermes-agent** — read: `README.md`, `agent/memory_manager.py` (1–120 of 962),
`agent/memory_context.py` (1–100 of 199), `agent/skill_bundles.py` (1–100 of 213),
`agent/context_compressor.py` (1–120 of 5,679), `tools/approval.py` (1–100 of 1,437),
`tools/approval_floors.py` (1–60 of 209), `tools/tool_search.py` (1–80 of 652),
`tools/delegate_tool.py` (1–80 of 1,006), `tools/delegate_tool_toolsets.py`,
`tools/path_security.py`, `apps/desktop/README.md` (1–100 of 274),
`gateway/bot_loop_guard.py`, `agent/turn_recovery.py` (1–60 + symbol list),
`cli-config.yaml.example` (1–100 of 2,427), `plugins/memory/byterover/README.md`,
`skills/AGENTS.md` (1–60 of 81). Skipped: `agent_init*.py`, the compressor family beyond
the main file, `approval_{smart,prompt,gateway_wait,yolo}.py` bodies (composition
described by the facade docstring), all plugins except `memory/`, `gateway/` beyond
`bot_loop_guard.py`, the 337 electron files (filenames only), `learning_graph*.py`, the
`anthropic_*` / `auxiliary_*` families, the 315-file `tools/` beyond the listed, `cron/`,
`apps/bootstrap-installer/`.

**deepseek-harness** — read: `README.md`, `AGENTS.md`, `docs/architecture.md`,
`docs/agent-lifecycle.md`, `docs/capability-seams.md` (full), `docs/cordis-primer.md`,
`docs/tool-execution-pipeline.md`, `docs/defensive-patterns/README.md`;
`core/agent-loop` (README + head), the compaction group + `compaction-basic`,
the skill group + `skill-filesystem`, the guard group + `repeat-tool-reminder` +
`timeout-policy`, the sandbox group, the subagent group, the spill group + `spill-policy`,
the llm group, `settings/settings`, `credentials`, `context/session-reference`,
`client/ui-chat`, `session/session-persistence-jsonl`; `apps/desktop/README.md`,
`apps/desktop/publishing/README.md`, `apps/desktop-publisher/README.md`. Skipped:
`docs/{config,tool,persistence}-catalog.md` (generated, thousands of lines — sampled by
header/grep), `docs/subsystems/*` / `user/*` / `cookbook/*` (redundant with package
READMEs), ~55 packages' source, `python/`, `native/`, `benchmarks/`, `snapshots/`,
`website/`, `vendor/` (cordis source), `apps/cli`, `apps/web` src.

**opencode / kilocode / minimax-code** — read: all three READMEs + AGENTS.md;
minimax `docs/architecture.md`, `docs/hooks.md`, `docs/plugins/README.md`,
`pi-turn-runner.ts`, `rule-model.ts`, `local-memory-facade.ts`,
`tool-result-compaction-config.ts`, `runaway-guard-config.ts` + listings; kilocode
`agent/agent.ts`, `permission/index.ts`, `session/compaction.ts`, `skill/index.ts` +
`discovery.ts`, `kilo-memory/src/memory.ts`, `kilo-vscode` structure, `kilo-ui`
components listing; opencode `AGENTS.md`, `desktop/AGENTS.md`, `core/src/agent.ts`,
`permission.ts`, `managed-policy.ts`, `tool/runtime.ts`, `session/compaction.ts`,
`session/subagent-job.ts`, `usage-limits.ts`, `services/update/README.md`, desktop
`src/main` structure. Skipped: `third_party/pi-mono` internals (vendored engine),
`kilo-jetbrains`, `kilo-web-ui`/`kilo-console`/translations, `opencode/packages/{app,
session-ui,stories,services/site}`, `kilo-sandbox` + containers internals (existence
verified), builds/tests/network.

**codex / goose / gemini-cli** — read: codex `README`, `docs/{config,sandbox,execpolicy,
skills}.md` (stubs), `memories/README.md`, `skills/src/lib.rs`, `hooks/src/lib.rs`,
`agent-roles/src/lib.rs`, `agent-graph-store/src/types.rs`, `execpolicy/src/policy.rs`,
`sandboxing/src/lib.rs`, `protocol/src/protocol.rs` (`AskForApproval`),
`protocol/src/config_types.rs`, `rollout/src/lib.rs`, `core/src/compact.rs`,
`config/src/config_layer_source.rs`, `cli/src/doctor/updates.rs`, `apply-patch/src/lib.rs`
+ listings; goose `README`, `docs/goose-architecture.md`, `config-files.md`,
`updating-goose.md`, `permission/{mod,permission_judge,permission_store}.rs`,
`goose-agent/{lib,machine,subagent,tool_approval}.rs`,
`goose-context-management/src/lib.rs`, `skills/mod.rs` + listings; gemini `README`,
`docs/core/{subagents,local-model-routing}.md`, `docs/cli/{sandbox,auto-memory,
checkpointing,model-routing,settings}.md`, `docs/hooks/index.md`,
`policy/policies/read-only.toml`, `confirmation-bus/types.ts`, `context/contextManager.ts`
+ listings. Skipped: codex `tui` internals, app-server protocol, code-mode, plugins/
plugin-host, connectors, keyring/secrets, network-proxy, guardian-context, ~110 of 120
crates; goose `goose-mcp` internals, per-provider code, acp, scheduler, recipes, desktop
UI source, evals, oauth/otel/dictation/live_voice; gemini `policy-engine.ts` internals,
`conseca`, scheduler, `a2a-server`, `sdk`, `vscode-ide-companion`, most tool
implementations, tests.

**crush / OpenHands** — read (crush): `README.md`, `docs/hooks/README.md`,
`permission/permission.go`, `app/app.go` (wiring), `agent/agent.go` (stop conditions,
summarize, settle), `agent/coordinator.go` (head), `agent/agent_dispatch{,_model}.go`,
`agent/tools/bash.go`, `skills/{skills,manager,tracker,catalog,embed}.go`,
`update/update.go`, `session/session.go` (head), `db/connect.go` + migrations,
`config/{config,load}.go` (selected), `hooks/runner.go`, `server/endpoints.go`,
`lsp/manager.go` (head), `question/question.go` (head). Read (OpenHands): `AGENTS.md`,
`README.md` (head), `config/defaults.json`, `src/api/{agent-server-config,
agent-server-compatibility,agent-canvas-updates,canvas-extensions-service,hooks-service}.ts`
(heads), `src/contexts/conversation-websocket-context.tsx`,
`src/hooks/{use-agent-state,use-compact-context-action,use-context-window-usage,
use-connection-error-grace-period}.ts`, `src/stores/conversation-store.ts` (head),
skills UI listing + `skill-card.tsx`, `step-disclosure-context.tsx`, `features/chat/`
listing, `electron/main.mjs` (head), `.agents/skills/frontend-api-contracts/SKILL.md`.
Skipped (crush): `internal/ui/**` impls, `internal/backend/`, `internal/server/*` beyond
endpoint names, `internal/llm/` + fantasy/catwalk (vendored providers), `config/{catwalk,
hyper,copilot}.go` + oauth, `*_test.go` (names only, for the invariants). Skipped
(OpenHands): `software-agent-sdk` (not cloned — agent server, tools, memory, delegation
internals therefore **not examined**), `OpenHands/extensions`, `features/{backends,
browser,settings,automations,sidebar,home,onboarding}`, `tests/`, `playwright.*`, `helm/`,
`docker/`, `scripts/`, `src/extensions/`, `src/api/{cloud,acp-service,conversation-service,
event-service}` internals, `src/services/`, `src/types/`.

**aider** — read: `README.md`, `HISTORY.md` (head), `aider/` listing,
`aider/coders/` (all coder classes enumerated: architect, ask, context, editblock,
editblock_fenced, editblock_func, base, …), `aider/history.py`, `aider/models.py`
(1,338 ln — structure), `aider/repomap.py` (867 ln — structure),
`aider/coders/base_coder.py` (2,485 ln — structure), `aider/commands.py`,
`aider/prompts.py`, `aider/io.py`, `aider/onboarding.py`, `aider/gui.py`.
Skipped: `benchmark/`, `requirements/`, `tests/`, the full body of `base_coder.py` and
`repomap.py` (structure + key functions only — aider's contribution is architectural, not
a harness with a tool loop August lacks).

**Claude Code / Desktop** — **no source exists**; documentation only. Pages read in full
(fetched 2026-11-14, stored at `august-review/claude-docs*.json`):
`how-claude-code-works`, `memory`, `context-window`, `prompt-caching`, `settings`,
`hooks`, `skills`, `sub-agents`, `permissions`, `permission-modes`, `sandboxing`,
`desktop`, `tools-reference`. Not fetched / not examined: the update mechanism,
`settings-reference` key list, `hooks-guide` (only the lifecycle page), Agent SDK pages,
cloud/remote-control pages, IDE integration pages, `llms.txt` index. No Anthropic
endpoint was called; no paid API was used. Every claim in `harness-claude.md` is
documentation-level and marked as such.

---

## C. Cloned repositories that could not be used

None. All 13 named reference repos cloned successfully and were read.

## D. What this pass did not cover

- **A live run of the product.** The brief forbids paid API keys and mocks were the
  prescribed substitute; no turn was executed against a real provider, so every claim
  about *behaviour under load* (streaming, compaction at the threshold, a real update on
  a packaged install) is VERIFIED-as-code, not VERIFIED-as-observed. The packaged NSIS
  update matrix remains unrun (and is itself a finding).
- **The mobile app on a device.** `frontend/mobile` was read statically; nothing was run
  on a phone or a simulator.
- **Area 6's full gate inventory** — see A6 above; the CI/observability audit was still in
  flight when this ledger was written.
- **`workbench.py` in full** (7,683 ln) and `skill_service.py` (2,828) were inventoried,
  not line-read; both are flagged as refactor candidates rather than audited surfaces.
- **Hermes's Electron internals** (337 non-test files) and **DeepSeek's ~55 remaining
  packages** were sampled, not read exhaustively — both are far larger than the 13
  dimensions require.
- **OpenHands' agent server** (`OpenHands/software-agent-sdk`) is a separate repo and was
  not cloned; its memory, tools, compaction and delegation internals are therefore
  unexamined, and the OpenHands cells in the comparison matrix say so.