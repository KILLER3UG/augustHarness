# August self-audit — findings by area

Repo: `C:/Dev/augustHarness` @ `9804b069` (master, `0.25.0`). Read-only audit run
2026-11-14; the working tree is unchanged (`git status` clean at the start and
end of the pass). Every claim below is VERIFIED against a file that was opened
in this pass unless explicitly marked INFERRED.

Method: six parallel read-only audits, one per area pair, each required to
produce a coverage ledger (which files were opened and which were skipped, with
a reason). The ledgers are merged into `05-coverage-ledger.md`.

Headline: **August is in materially better shape than its own open-items lists
claim.** The strongest parts of the system — the file-backed memory record, the
one-choke-point tool executor, the identity-checked health probes, the two
install guards, the update ordering, the chat/streaming reducer — are all
genuinely good and are *verified* rather than assumed. The real problems are
concentrated in three places:

1. **Secrets at rest and in the environment** (two HIGH findings, both
   security, both confirmations of items the repo already knew about).
2. **Lifecycle bookkeeping around background work** (a daemon status column that
   is write-only, a migration step that can truncate `providers.json`, a health
   monitor that is never stopped, a teardown where 14 of 16 failure handlers are
   a bare `pass`).
3. **Coherence drift** — docs that still describe the retired SQLite record,
   gap lists carrying seven items that are already fixed, a gate vocabulary with
   an event nothing renders, and type-token discipline that does not hold.

A cross-cutting finding worth stating first: **the repo's own "open items" lists
are stale in both directions.** `docs/ROADMAP.md` and `docs/GAPS_AND_BUGS.md`
still list as open: the Backdrop a11y gap, the ModelRow focus trap, the
ClarifyTool keydown scoping, the sidebar resize keyboard support, the Turn Limits
Save button, the `N t/s` readout, and two "dead" stores — all seven are already
fixed in code. Meanwhile two genuinely open defects (the wrong-binary relaunch,
the boot-index receipt) appear on no list. See `01-august-audit.md` §9.

---

## 1. Launch / startup / shutdown + Tauri shell

| Sev | Finding | Evidence | Status |
|---|---|---|---|
| **HIGH** | NSIS POSTINSTALL relaunches `$INSTDIR\August.exe`; the shipped binary is `AugustHarness.exe`. The installer's relaunch silently no-ops, and the app only comes back because the Rust waiter happens to use `current_exe()`. The code and `docs/agent/product.md` also contradict each other about who owns the relaunch. | `frontend/desktop/src-tauri/windows/hooks.nsh:193` vs `tauri.conf.json:6` | VERIFIED |
| MED | The packaged NSIS update matrix has never been run on a real install — the only end-to-end check of the update path, and exactly the check that would have caught the item above. | `docs/UPDATE_VERIFICATION.md`; `docs/agent/product.md:100-102` | VERIFIED |
| MED | Waiter timing constants (120 s exit / 1800 s install / 400 ms poll) live inside a PowerShell `format!` string, ungreppable by gates. | `src-tauri/src/backend.rs:3346-3389` | VERIFIED |
| MED | Tag-push releases publish live immediately; only `workflow_dispatch` goes through the draft → publish-flip job. A bad tag ships to all users with no review pause. | `.github/workflows/release-desktop.yml:157-163` | VERIFIED |
| MED | `proxy_status` reports "ok" on any 2xx, without the `python: true` identity check the supervisor uses — contradictory signals if a foreign service holds a candidate port. | `backend.rs:2607` vs `:2060`; `main.py:750` | VERIFIED |
| LOW | Port-fallback sweep (8085..=8095 × 45 s) can hold the supervisor thread ~8 min with the UI on "Starting backend…". | `backend.rs:32, 2288, 2376` | VERIFIED |
| LOW | Provider health probe counts any `<500` as healthy, so 401/403/404 report green. | `services/health_monitor.py:134-147` | VERIFIED |
| LOW | `hooks.nsh:97` hardcodes the bundle id into the venv path — the next rename must edit two places, one of which is a PowerShell string. | `hooks.nsh:97` | VERIFIED |
| LOW | Shutdown cancels only the learning scheduler; `brain_backup` and `mcp_refresh` tasks are never cancelled or awaited. | `main.py:311-314, 324-325, 418-421` | VERIFIED |
| LOW | Local API token entropy derives from `RandomState` (SipHash) rather than a CSPRNG. | `backend.rs:88-112` | VERIFIED |
| LOW | `_pollingWatch` is a one-shot recorder misnamed as a polling watcher — never polls. | `services/environment_watcher.py:159-176` | VERIFIED |

**Strengths:** identity-checked health probes; both install guards hold
(`assertPayloadHasNoUserState`; `wipeStaleTree` refusing anything outside
`backend-runtime/`); update ordering (waiter scheduled *before* installer
launch, stale marker deleted when scheduled, POSTINSTALL writes the marker
unconditionally); minisign verified at both ends; CI-only releases with
provenance; LAN gate fails closed with a loopback exemption.

**Removals:** drop the unused MSI bundle target; collapse the
`August`/`AugustHarness`/`augustharness` triple-name hedging; delete
`_pollingWatch`.

---

## 2. Updater / release pipeline

Covered with §1. The pipeline is defensively layered and CI-only. The two things
that matter are the wrong-binary relaunch (HIGH, above) and the never-run
packaged matrix (MED). Everything else — draft gate, signature verification,
version-source sync, payload staging — is sound.

---

## 3. UI shell, sidebars, settings, icons, theming

| Sev | Finding | Evidence | Status |
|---|---|---|---|
| MED | `MemorySection.tsx` uses 11 distinct text sizes, including five arbitrary rem literals (`0.65625rem`–`1.25rem`) — the largest settings panel is off the token ramp entirely. | `sections/settings/MemorySection.tsx` | VERIFIED |
| MED | `SettingsSelect`'s custom listbox has no ArrowUp/Down/Home/End, no `aria-activedescendant`, and leaves focus on the trigger when open — inherited by 7+ settings surfaces. | `components/settings/SettingsSelect.tsx:43-57` | VERIFIED |
| MED | `ModelVisibilityModal`, `NotificationsPanel`, `ExamBanner` advertise `aria-modal="true"` with no focus trap anywhere. | those three files, each ~`:94`/`:147`/`:179` | VERIFIED |
| LOW | Settings tier counts drifted: registry now has 31 hidden of 46 sections; docs say "26 of 44". | `settings-registry.ts` | VERIFIED |
| LOW | Sidebar resize handle announces a bare pixel number (`aria-valuenow` but no `aria-valuetext`). | `useResizablePane.ts:155-158` | VERIFIED |
| — | **Already fixed, still listed as open:** Backdrop Escape/topmost-scrim + opt-in trap; ModelRow `useFocusTrap` + `autoFocus`; ClarifyTool scoped keydown; `useResizablePane` full keyboard separator. | `Backdrop.tsx:142-164`; `ModelRow.tsx:163,285-286,319`; `ClarifyTool.tsx:210-229`; `useResizablePane.ts:151-183` | VERIFIED |

**Strengths:** `Backdrop` + `useFocusTrap` + `useResizablePane` are high-quality
primitives with documented failure histories; the token system is real (the
breakage is in adoption, not in the tokens).

---

## 4. Chat rendering / streaming / tool cards / subagent UI

| Sev | Finding | Evidence | Status |
|---|---|---|---|
| **HIGH** | `subagentFanout` is emitted by the backend and **silently dropped by the frontend**: no Zod schema literal, no dispatch case, and the `switch` has no `default:` — so any unknown event vanishes. The subagent fleet receipt ("why only 6 of 8 lanes started") never reaches the user. | `services/tools/spawn_subagents_tool.py:537`; `workbench/emit_types.py:37`; `api/workbench/streamEvents.ts:435-530`; `api/schemas/workbench.ts` | VERIFIED |
| LOW | Five arbitrary rem font literals recur across six chat components (`ActionNeededCard`, `ClarifyTool`, `DocumentBadge`, `SubagentTimeline`, `ChangesPill`, `DiffView`). | those files | VERIFIED |
| LOW | `session_status` is a dead entry in the backend's emitted-event vocabulary — no emitter, no handler. | `emit_types.py` | VERIFIED |

**Strengths:** `makeStreamHandlers.ts` (1,265 lines) is a complete, defensively
written reducer over the whole SSE vocabulary (subagent blocks, todos, recovery,
retry notices, `turnEnd`, stable per-block ids); the activity-line grammar
implements "one line per run, opens on failure, no time on settled lines"
correctly; `DisclosureRow` owns its body with real `aria-controls`/`aria-expanded`;
markdown is XSS-hardened with stream-aware highlight skipping and size-bounded
caches; the composer context-% follows "omit when unmeasured".

---

## 5. Memory + recall

| Sev | Finding | Evidence | Status |
|---|---|---|---|
| MED | Boot-index omission receipt under-counts or vanishes when sensitive/person rows sit in the newest-40 slice: the slice happens before the filter, and `total` re-counts without the `sensitiveMemoryReason` predicate. 40 visible facts of which 5 trip the scanner → **no receipt at all**. | `memory_store/brain.py:813-877, 866-873` | VERIFIED |
| MED | `save_fact`'s `if_updated_at` CAS check runs inside `_record_lock()`, but `record.write_fact_file(row)` is **outside** it — two threads can both pass the guard and the first write is silently lost. The docstring claims the lock closes this. | `memory_store/rest.py:415-439, 517` | VERIFIED |
| MED | The M-7 disuse penalty reads `injected_count`, which nothing writes any more (`_set_injected` has zero callers since the per-turn ranked tail was removed). ~90 lines + 3 config knobs of dead scoring machinery. | `fact_retrieval.py:404-456, 555-583`; `rest.py:559` | VERIFIED |
| LOW→MED | Four write doors edit fact files without refreshing `MEMORY.md`: `brain_update_row`, `apply_retire_decision`, `update_fact_value`, `restore_superseded_losers`. The human audit mirror goes stale until an unrelated write rebuilds it. | `brain.py:1277`; `consolidation.py:689-691`; `rest.py:696-703, 766-778` | VERIFIED |
| LOW | Delta lane slices to 8 candidates *before* the sensitivity filter, so 8 flagged newest writes can hide a qualifying 9th fact with no receipt. | `fact_retrieval.py:863, 872-877` | VERIFIED |
| LOW | `list_facts` mixes project entries into `total` while they ride page 0 without consuming offset → one extra empty page. | `tool_registrations/session_tools.py:1362-1371` | VERIFIED |
| LOW | Auto-review's 60 s timeout sits on the synchronous write path — a slow reviewer stalls the save for up to a minute. | `auto_review.py:42, 537`; `session_tools.py:815-818` | VERIFIED |

**Strengths:** the one-store rule is enforced in code, not just prose
(`mirror.py` is the only facts writer; the record dir is inside the model-write
fence); the honest-budget pattern (line-boundary cuts + named omission receipts)
is applied consistently; fail-open vs fail-closed is a documented decision per
door; the transcript FTS5 index is derived, rebuildable, synced before every
query, and falls back to a scan whenever it cannot answer; `list_facts` /
`session_search` are genuinely enumerable.

---

## 6. Skills + self-evolution

| Sev | Finding | Evidence | Status |
|---|---|---|---|
| LOW | `_siblingFiles` rglobs a skill directory with no depth/file cap (only the *display* is capped at 25), unlike the bounded `_iterSkillMd` walker. | `tool_registrations/skill_tools.py:199-226` | VERIFIED |
| LOW | Stale comment: description cap documented as 500, code says `_DESCRIPTIONMax = 1024`. | `skill_service.py:410-418` | VERIFIED |
| LOW | `_deriveFactKey`'s `scope` parameter is dead and self-documented for removal. | `session_tools.py:159-174` | VERIFIED |
| LOW | Legacy usage-sidecar migration (`_legacy_usage_files`, `_migrate_legacy_usage`) runs on every read/write with no sunset. | `skill_service.py:2586-2668` | VERIFIED |

**Strengths:** review-before-create with fail-closed-on-unavailable; destructive
kinds wait for a human while create/patch are autonomous under the reviewer;
the applier refuses create-over-existing, bundled shadowing, and body-less
patches; patches carry forward `trigger`/`supersedes`/`origin`/unknown
frontmatter so a patch cannot silently retire a skill from recall; the learning
pass uses per-session cursors (not one global high-water id); the rails are an
allow-list with self-naming refusals and evidence tracing to the user's own
episodes.

---

## 7. Tools / MCP / sandbox / permissions

| Sev | Finding | Evidence | Status |
|---|---|---|---|
| **HIGH** | A live Google OAuth client secret (`GOCSPX-…`) sits in plaintext in `data/mcp-servers.json` (untracked, gitignored). Outside the outbound scrub's collection set entirely. | `data/mcp-servers.json` (read in full; value not reproduced) | VERIFIED |
| **HIGH** | `GITHUB_TOKEN` / `SLACK_BOT_TOKEN` are exported to `os.environ` on connect; the outbound scrub only sweeps `*_API_KEY` names, so token-class secrets are invisible to it. | `services/service_connections.py:493, 516`; `hooks/secret_guard.py:114` | VERIFIED |
| MED | `tool_call`'s no-session fallback dispatches **raw** through the registry — no PRE/POST hooks, and no guard when `get_session()` returns `None` rather than raising. The only residual bypass of the 2026-10-21 `_executeTool` unification. | `services/tools/tool_bridges.py:151-155, 198-215` | VERIFIED |
| MED | MCP server create/update/delete lack the per-install token that start/stop require — a non-browser local process can register an arbitrary stdio command that discovery later spawns. | `routers/mcp.py:117, 158, 224` vs `:232, 272` | VERIFIED |
| MED | Per-server MCP `env` secrets are not in the outbound scrub's collectors at all. | `hooks/secret_guard.py:114-140` | VERIFIED |
| LOW | Unpriced models fall back to the *most expensive known model* as a "conservative upper bound" — inflated cost estimates. | `cost_estimator.py:200, ~255-266` | VERIFIED |
| LOW | EventSource paths accept the per-install token in the query string. | `lib/local_api_auth.py:160-163` | VERIFIED |
| LOW | Hardline's matcher is a best-effort denylist around a solid tokenizer; AppContainer remains opt-in. | `sandbox/hardline.py:455-473` | VERIFIED |
| LOW | `workspace-write` tier does not confine writes to the workspace on POSIX (Home/Temp allowlist, no workspace cap). | `sandbox/policy.py:105-119` | VERIFIED |
| LOW | Dead code: `wait_for=None` branch in `streamSse` (every caller passes a timeout); duplicated `break` in the MCP read loop; `sandbox/runner.py` is a thin pass-through. | `providers/clients/base.py`; `tools/mcp_client.py:1206-1208` | VERIFIED |

**Strengths:** one execution choke point (`_executeTool`) with PRE/POST hooks,
shared MCP lifecycle + timeout, `_expect` receipts; MCP tools mutating by
default; both network doors fail closed (`403/503/401`, `hmac.compare_digest`);
guard-mode normalization clamps unknown spellings to the most restrictive mode;
DPAPI-encrypted secret store; credential stores unlistable at seven doors; a
single command tokenizer; progressive tool disclosure is real (BM25-budgeted
against the model's actual window).

**Correction to a prior hypothesis:** two earlier working hypotheses about
`gateway_auth.py` (a hardcoded `DEFAULT_KEY`; blank key fails open) were
re-checked and are **FALSE** — the file fails closed.

---

## 8. LLM routing / local models / per-model handling

No HIGH/MED defects. Verified: base URLs are used exactly as pasted (only the
format leaf is appended); the per-model `apiFormat` override wins over the
provider format and is honored by chat, Test, Live/BTW and the `/v1` adapters;
loopback hosts need no key; `cost_estimator.price_for_model` is the only pricing
source (`0.0` is a price, `None` is unset); routing evidence is **record-only** —
the arena endpoints rank win rates and nothing rewrites a session's provider.
Local models: loopback detection + `llama.cpp` runtime download with pinned
SHA256s + HF search + ComfyUI all present.

The only finding is the pricing fallback (LOW, §7).

---

## 9. Backend routers / services / daemons / persistence

| Sev | Finding | Evidence | Status |
|---|---|---|---|
| **HIGH** | `daemons.json` status is written once at spawn and never updated; only `kill()` deletes the row. A force-killed quit leaves completed daemons rehydrating as `running` on next boot (only the persisted wall-clock TTL stops it). | `services/daemon_manager.py:205-206, 236-242, 268-304` | VERIFIED |
| MED | Migration ladder steps v2 and v3 write `providers.json` with bare `write_text`, while step 4 does it atomically and documents that v2/v3 were shipped wrong. A mid-write crash truncates the user's provider config, and the version is then stamped. | `services/data_migrations.py:107, 138, 193-204, 225` | VERIFIED |
| MED | `lifespan` is 260 lines of prose-ordered boot with 14 of 16 teardown exception handlers a bare `pass` — a failed session/telemetry flush during quit is indistinguishable from success. | `main.py:134-541` | VERIFIED |
| MED | Bare `except Exception: pass` is still dominant (411 sites) while `best_effort.py` exists for exactly this and is used 10 times. | `services/best_effort.py:1-21` | VERIFIED |
| MED | Health monitor started at boot and never stopped; probes treat any non-5xx as healthy; probes run serially. | `main.py:193-199`; `health_monitor.py:145-163` | VERIFIED |
| MED | Five per-session telemetry JSONL ledgers have no size cap although `append_ledger(max_bytes=…)` exists. | `memory_store/sessions.py:567-575`; `tables.py:191-197` | VERIFIED |
| MED | God-files with no internal seams: `workbench.py` 7,683 lines, `routers/workbench.py` 2,959, `skill_service.py` 2,828, `skill_distiller.py` 2,184. | `wc -l` | VERIFIED |
| MED | A new numeric brain-config field silently inherits the workbench-loop range `(0, 500)` via a bare `else` fallback; bounds are re-clamped separately in `getDelegationLimits`. | `brain_config_service.py:165-228, 552-590, 850-853` | VERIFIED |
| LOW | `/v1/models` is declared twice; the `proxy.py` copy is a pure delegation stub shadowed by include order. | `routers/models.py:78`; `routers/proxy.py:585-591` | VERIFIED |
| LOW | Docs still advertise the retired SQLite record. | `docs/CONFIGURATION.md:29, 944`; `docs/DEVELOPER_GUIDE.md:40` | VERIFIED |
| LOW | `deferred_writes.py` is a retired no-op still wired into the shutdown path under a comment that now lies. | `services/deferred_writes.py:1-37`; `main.py:466-475` | VERIFIED |
| LOW | `filestore.read_json` treats a corrupt store as "absent" — correct at boot, dangerous for money-shaped data (no quarantine copy). | `services/filestore.py:66-80` | VERIFIED |
| LOW | `config.py` has no range validation on `port`; `settings.reload()` cannot affect the already-captured CORS list. | `config.py:65-110`; `main.py:210, 561-567` | VERIFIED |

**Strengths:** `filestore.py` is the quiet backbone and it is right (one `home(kind)`
gate, atomic + fsync writes, a JSONL reader that distinguishes a torn trailing
line from mid-file corruption); `error_families.py` is a real vocabulary with a
written migration story; the migration ladder's rules are written as rules;
route surface is consistent (448 routes, exactly one duplicate path, 355
`HTTPException` sites with explicit status codes, zero bare dict error returns);
`/api/health` has a single owner whose docstring records the collision history.

---

## 10. Mobile companion

| Sev | Finding | Evidence | Status |
|---|---|---|---|
| MED | The companion is a WebView of the desktop SPA: one `<WebView>`, a CSS reset, a theme bridge, a pairing sheet, a status bar. No native data path, no offline, no SSE client; token is `useState` only (no `expo-secure-store` — needs an owner). | `frontend/mobile/App.tsx:22-78, 168-176, 230-273` | VERIFIED |
| LOW | The companion never consumes an SSE stream itself — parity rests entirely on the desktop bundle inside the WebView. The roadmap's "token in query string for two EventSource paths" is not visible in any router. | `docs/ROADMAP.md:227-230`; `frontend/mobile/pairing.ts:105-112` | VERIFIED |
| LOW | `npm test -w frontend/mobile` needs a live backend on `127.0.0.1:8085`, so `gates:mobile` fails on a clean checkout for a reason unrelated to the diff. | `frontend/mobile/scripts/audit-mobile-parity.js:13-14` | VERIFIED |

The open owner decision — **second client or second August** — is still open
(`docs/ROADMAP.md:101-107`). Today's WebView shell silently answers "second
client of the desktop's own backend" without anyone deciding.

---

## 11. CI / gates / observability / developer experience

Audit completed (see `audit-6-ci-observability.md` for the full ledger). Verdict: **the gate
architecture is better than the brief's premise suggests** — one gate list drives CI, release
and the developer, and `check:sse` detects six distinct failure classes including "emitted and
schematised but NOT dispatched — THE SILENT DROP," with an allowlist that fails on stale entries
in both directions.

| Sev | Finding | Evidence | Status |
|---|---|---|---|
| MED | `subagentFanout` is green in `check:sse` **by an allowlist entry** (`SCHEMA_ONLY`) while nothing renders it — the gate records the decision but cannot notice it is wrong. | `scripts/check-sse-parity.mjs:95-101, 345-346, 374-376, 409-414` | VERIFIED |
| MED | `gates:mobile` needs a live backend on `127.0.0.1:8085`, so it fails on a clean checkout for a reason unrelated to the diff — a gate people learn to skip. | `frontend/mobile/package.json`; `scripts/audit-mobile-parity.js:14` | VERIFIED |
| LOW | `e2e:smoke` is the only behavioural detector; a developer running only `gates:static` gets no evidence the app starts. | `scripts/gates.mjs` groups | VERIFIED |
| LOW | No gate checks that a **path named in a doc exists** — `check:docs` covers numbers, `check:doc-links` covers links. Three separate instances of this class in this audit. | `check-docs-sync.mjs`, `check-doc-links.mjs` | VERIFIED |
| LOW | No mutation testing and no fixture harness for the UI surfaces (OpenHands ships stryker + a playwright matrix; DeepSeek ~10 vitest configs). | tree-wide | VERIFIED |
| LOW | The dev loop has no desktop sandbox (Hermes's `dev-sandbox.sh` equivalent). | `scripts/dev-app.mjs` | VERIFIED |

**The blind-spot list** — defect classes with no detector today, four of which have already
bitten: the app does not start; an update does not bring the app back (**bitten** — `hooks.nsh`);
a provider path fails end-to-end; a memory write silently loses data (**bitten** — the CAS race);
a gate passes on zero matches; a renderer is wrong (**bitten**); a UI control is unreachable
(**bitten** — `subagentFanout`); a doc names a store that no longer exists (**bitten**).

Four cheap gates are proposed to close most of them: `check:doc-paths`,
`check:installer-names`, extending `check:sse` with a required `default:` case, and a
`node --check` sweep over `scripts/*.mjs`. None is a test suite.

**Strengths:** the single gate list; `check:sse`'s six-class detection with recorded
rationale; the shrink-only ratchet baselines; `e2e:smoke` never touching the user's data dir
and running on Windows in CI; the release job saying plainly that it builds and does not test.

**Run:** `node scripts/gates.mjs static` → passed. It is the only gate executed (the others
need the backend interpreter or npm deps, and one regenerates a tracked file — a write).

---

## 12. The docs-drift register (cross-cutting)

Every claim below was re-checked against the tree in this pass. Each is a case
where the repo's own documentation is wrong about the code.

| Doc says | Tree says | Evidence |
|---|---|---|
| `MEMORY.md`/spec: `promptCacheLive.ts` + `contextSectionsLive.ts` are dead stores with no readers | Both have live readers | `sections/chat/composer/ContextUsageReadout.tsx`; `makeStreamHandlers.ts` |
| ROADMAP: Backdrop lacks Escape / role / aria-modal / focus trap | Escape + topmost-scrim + opt-in `trapFocus` implemented | `Backdrop.tsx:142-164, 95, 133` |
| ROADMAP: ModelRow modal lacks focus trap + initial focus | `useFocusTrap`, `role="dialog"`, `autoFocus` | `ModelRow.tsx:163, 285-286, 319` |
| ROADMAP: ClarifyTool binds document-wide keydown while `aria-modal=false` | Handler scoped to the card | `ClarifyTool.tsx:210-229` |
| ROADMAP: sidebar resize separator not keyboard-operable | `role="separator"`, `tabIndex`, arrows, Home/End | `useResizablePane.ts:151-183` |
| ROADMAP: Turn Limits keeps a Save button | Turn-bounds/subagent-limits panels removed | `SettingsPage.tsx:415-419` |
| ROADMAP: `N t/s` readout still on screen | Removed | `AssistantMessageContent.tsx:177-193` |
| CONFIGURATION: `data/august_brain.sqlite` is the SoT | The record is `dataDir/memory/`; the DB is retired | `docs/CONFIGURATION.md:29, 944` vs `memory_store/mirror.py:1-10` |
| DEVELOPER_GUIDE: `app/migrations/` + `memory_conn.py` | Neither exists | tree; `memory_conn.py` absent |
| Settings docs: "26 of 44 sections tier:hidden" | 31 of 46 | `settings-registry.ts` |
| ROADMAP: mobile token rides in the query string on two EventSource paths | Not visible in any router | `grep -rn "access_token|token=" backend-py/app/routers/` → zero |
| `emit_types.py`: `session_status` is a commonly observed type | No emitter, no handler | grep |

This register is the highest-leverage cleanup in the whole audit: a plan built on
these lists would re-do seven finished items and miss two live defects.

---

## Coverage

Six audits, ~2,400 files of scope, ~110 files opened at source level. Per-area
ledgers (including explicit skips with reasons) are consolidated in
`05-coverage-ledger.md`. Nothing in `C:/Dev/augustHarness` was created,
modified or deleted: `git status --short` is empty and HEAD is `9804b069` at the
start and end of the pass.