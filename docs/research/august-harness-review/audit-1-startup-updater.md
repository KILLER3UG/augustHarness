# Audit — startup/shutdown/Tauri shell + updater/release

Repo: `C:/Dev/augustHarness` @ `9804b069` (master), audited 2026-11-14. Read-only; every claim below cites a file:line that was actually opened.

## Verdict (one paragraph)

The launch/shutdown spine is in unusually good shape: the workspace renders immediately with a passive status pill (BackendStatusPill.tsx:1-12), the supervisor identity-checks `/api/health` instead of trusting any 2xx (backend.rs:2060, main.py:750), teardown is reachable from every exit path and releases the instance lock (lib.rs:194-200, backend.rs:1755-1757), and the two install guards hold (desktop-backend-payload.mjs `assertPayloadHasNoUserState`; backend.rs:782/817 `wipeStaleTree` refusing anything outside `backend-runtime/`). The update pipeline is defensively layered (minisign at release and in-app, detached relaunch waiter scheduled before installer launch, explicit user restart), and the release pipeline is CI-only with provenance checks. The one concrete defect found: the NSIS POSTINSTALL hook relaunches `$INSTDIR\August.exe` while the shipped binary is `AugustHarness.exe` (hooks.nsh:193 vs tauri.conf.json:6) — the installer's relaunch, which its own comments say "must own bringing the app back", silently no-ops. That this survived is a direct consequence of the second confirmed open item: the packaged NSIS update matrix has never been run. Remaining findings are robustness/polish: release-constant hygiene, a health-signal inconsistency in `proxy_status`, and small lifecycle gaps.

## Findings (ordered by severity)

### [HIGH] NSIS POSTINSTALL relaunches the wrong binary name (`August.exe`, shipped is `AugustHarness.exe`)

**What I saw (VERIFIED).** `frontend/desktop/src-tauri/windows/hooks.nsh:188-193` — for every silent install (including `/UPDATE`), POSTINSTALL runs `ExecShell "open" "$INSTDIR\August.exe" "" SW_SHOWNORMAL`. The installed binary name comes from `MAINBINARYNAME` = `{{main_binary_name}}` (installer.nsi:96), which Tauri fills from `tauri.conf.json:6` `"mainBinaryName": "AugustHarness"`; every other reference in the installer uses `${MAINBINARYNAME}.exe` (installer.nsi:429, 489, 720, 826, 861, 1023-1050). So `$INSTDIR\August.exe` does not exist on a clean install and the ExecShell fails silently (NSIS sets the error flag and continues). The hooks file knows the current name elsewhere — PREINSTALL taskkills `August.exe`, `AugustHarness.exe`, `augustharness.exe` (hooks.nsh:110-112). Compounding it, `docs/agent/product.md:59-63` claims "the NSIS POSTINSTALL … skips its own `ExecShell` under `/UPDATE`, so the waiter is the single owner of the relaunch", while hooks.nsh:171-179 says the opposite ("The installer starts the app for EVERY silent install, including /UPDATE … the installer … must own bringing the app back"). Code and doc disagree, and the code's relaunch is dead anyway.

**Why it matters.** The design intent (hooks.nsh:173-179) is that the installer owns the relaunch because the detached waiter is a copy from the OLD build — a user updating from an older build runs the old waiter, which older comments describe as one that "expired at 600 s and exited without relaunching". With the installer's relaunch pointing at a nonexistent file, that safety story collapses to "the old waiter is the only relauncher" — the exact dead-app failure the comments warn about. Secondary hazard: on a machine that once installed the pre-rename `August.exe` and where the registry cleanup (installer.nsi:765-770, which only deletes the recorded `OldMainBinaryName`) didn't cover it, ExecShell would start the STALE old binary instead of the new one. Today the app does come back for current-version users only because the Rust waiter uses `std::env::current_exe()` (backend.rs:3314) and starts the correct path (backend.rs:3364-3368) — an accident of layering, not the documented design.

**What good looks like.** Change hooks.nsh:193 to `ExecShell "open" "$INSTDIR\${MAINBINARYNAME}.exe" "" SW_SHOWNORMAL` (the NSIS define is in scope at the `!insertmacro NSIS_HOOK_POSTINSTALL` site, installer.nsi:808-810). Then pick one owner and make docs match code: either keep the installer as primary relauncher and demote the waiter to pure safety net (update docs/agent/product.md:59-63), or delete the ExecShell entirely and let the waiter own it. Add the binary-name check to the packaged matrix (see next finding).

### [MED] The packaged NSIS update matrix has never been run on a real install

**What I saw (VERIFIED).** `docs/UPDATE_VERIFICATION.md` exists as a manual matrix; `docs/agent/product.md:100-102` states "The whole flow is unverified on a packaged NSIS install: the matrix … has not been run"; `docs/STATUS.md:484` lists "Verify the update flow on a packaged NSIS install — including the rename" as open item #2.

**Why it matters.** There is no test suite (AGENTS.md), so this matrix is the only end-to-end check of the update path — and the very first class of defect it would catch (wrong relaunch path, marker/waiter races, locked-file refusals) is exactly what finding #1 is. Every piece of the flow is individually reasoned about in comments, but the composed behavior on a real Windows install is unverified.

**What good looks like.** Before the next release: provision a Windows VM, install the current NSIS build, run the full `docs/UPDATE_VERIFICATION.md` matrix (fresh install → update → quit/restart → uninstall), and record results in that doc. Anything the matrix can automate as a static gate (e.g. "POSTINSTALL exe name == `${MAINBINARYNAME}`", "marker path == waiter marker path") should become a `scripts/check-*` gate so it can't regress silently.

### [MED] Waiter timing constants live inside a PowerShell string literal

**What I saw (VERIFIED).** `frontend/desktop/src-tauri/src/backend.rs:3346-3389` — the 120 s old-pid exit deadline, 1800 s installer deadline, 400 ms installer poll, 250 ms pid poll and 200 ms settle are all interpolated into the `format!`-built PowerShell script. `docs/agent/product.md:77-79` itself flags: "These three numbers live in a PowerShell string, not in a Rust constant, so `check:docs` cannot guard them."

**Why it matters.** These constants encode hard-won failure history (the 600 s expiry that left dead apps; the 200 MB payload that exceeded ten minutes). They are untestable as constants, unfindable by grep-based gates, and a future edit to the string can silently change update behavior.

**What good looks like.** Promote them to named Rust constants next to `UPDATE_HOLDOFF_MAX` (backend.rs:54), e.g. `const UPDATE_WAITER_EXIT_SECS: u64 = 120; const UPDATE_WAITER_INSTALL_SECS: u64 = 1800; const UPDATE_WAITER_POLL_MS: u64 = 400;` and interpolate those into the script. Same file, same behavior, but now greppable, reviewable, and unit-assertable.

### [MED] Tag-push releases publish live immediately; only workflow_dispatch goes through a draft

**What I saw (VERIFIED).** `.github/workflows/release-desktop.yml:157-163` — on a `v*` tag push the build job runs `node scripts/release-desktop.mjs --tauri --publish` (no `--draft`), while the `workflow_dispatch` path runs `--publish --draft` (line 163) and a separate `publish` job flips the draft live (lines 165-179). The updater feed is `…/releases/latest/download/latest.json` (tauri.conf.json:60), so a published release is immediately everyone's next update.

**Why it matters.** The draft gate exists precisely so a human looks at assets before they become the live update feed; the tag-push path bypasses it. Mitigations are real (reusable CI is a hard `needs` dependency — release-desktop.yml:29 comment — and a concurrency group serializes tags), but a bad tag still ships straight to all users with no review pause.

**What good looks like.** Make tag pushes produce drafts too (one-word change at release-desktop.yml:159), and either require the manual `workflow_dispatch` publish flip for every release, or auto-flip only after the publish job's own verification steps pass. The `--draft` machinery already exists.

### [MED] `proxy_status` reports "ok" on any 2xx, without the identity check the supervisor uses

**What I saw (VERIFIED).** `frontend/desktop/src-tauri/src/backend.rs:2607` — `proxy_status` probes `/api/health` and returns `"ok:{port}"` on any 2xx. The spawn path's `healthOkOnPort` (backend.rs:2060) additionally requires the `python: true` identity marker the backend serves (main.py:750).

**Why it matters.** If a foreign local service answers 2xx on one of the candidate ports during recovery, the UI can be told "ok:port" while the supervisor's identity-checked probe keeps failing and respawning — contradictory status signals at exactly the moment users need a truthful one.

**What good looks like.** Route `proxy_status` through the same body check as `healthOkOnPort` (parse the JSON, require `python == true`), or have `proxy_status` call `healthOkOnPort` and only then report the port.

### [LOW] Port-fallback sweep worst case blocks the watchdog thread for ~8 minutes

**What I saw (VERIFIED).** `backend.rs:32` defines the fallback range 8085..=8095 (11 ports); the python spawn path waits `Duration::from_secs(45)` per port (backend.rs:2376) inside the `portCandidates()` loop (backend.rs:2288+), all on the startup/watchdog thread (lib.rs:130-134). `waitForProxy` does bail early if the child exits (backend.rs:2102), and the node fallback waits 20 s (backend.rs:2506).

**Why it matters.** A backend that spawns, binds, but never passes the identity check on each port in turn keeps the supervisor thread in the sweep for up to ~495 s while the UI shows "Starting backend…" and the pill's 8 s restart ask can't help (the restart command needs the supervisor lock the sweep holds). Unlikely in practice (needs a child that stays alive but unhealthy on every port), but it is the longest unbounded-looking wait in the launch path.

**What good looks like.** Probe the candidate ports for a healthy identity BEFORE spawning (parallel TCP+health probes), then spawn only on ports that are genuinely free — shrinking the per-port wait to a few seconds — or cap the total sweep with a deadline that reports a hard error phase to the pill.

### [LOW] Provider health probe counts 401/404 as "healthy"

**What I saw (VERIFIED).** `backend-py/app/services/health_monitor.py:134-147` — `probe_provider` sets `success = resp.status_code < 500`, so a revoked key (401) or removed model list (404) reports the provider as healthy.

**Why it matters.** The health panel is the user's signal for "is this provider usable". A dead credential showing green is a false reassurance that surfaces later as a failed turn.

**What good looks like.** Keep reachability as the primary signal but distinguish auth failures: 401/403 should surface as a distinct "unauthenticated" state (and 404 on `/models` as "endpoint reachable, model list missing") rather than plain healthy.

### [LOW] `hooks.nsh` hardcodes the new bundle id into the AppData venv path

**What I saw (VERIFIED).** `frontend/desktop/src-tauri/windows/hooks.nsh:97` — the PREINSTALL stop script builds `Join-Path $$env:APPDATA 'com.august.harness\backend-runtime\backend-py\.venv'` as a literal string.

**Why it matters.** The identifier was already renamed once (`com.august.proxy` → `com.august.harness`; backend.rs:1442 `migrateAppDataRoot` handles the old one). The next rename must be edited in two places — Rust constants and this PowerShell string — and only one of them is type-checked.

**What good looks like.** Pass the venv path (or the appdata root) into the generated script from Rust-side truth, or template it via the installer (`{{appdata_path}}`-style placeholder), so the hook can't drift from `tauri.conf.json`.

### [LOW] Shutdown cancels only the learning scheduler; brain-backup and MCP-refresh tasks are left running

**What I saw (VERIFIED).** `backend-py/app/main.py:311-314` creates `brain_backup` task, `main.py:324-325` creates `mcp_refresh` task (both stored on `app.state` with comments about holding references), but the teardown sequence (main.py:396-541) cancels only the learning scheduler (main.py:418-421) before draining daemons/MCP servers.

**Why it matters.** Both are one-shot tasks that are usually finished by quit time, but a quit landing mid-backup kills the backup mid-write. Whether that's harmless depends on `brain_backup` writing atomically (temp+rename); nothing in the teardown path guarantees it.

**What good looks like.** In the teardown, `cancel()` + `await asyncio.gather(..., return_exceptions=True)` for `app.state.brain_backup_task` and `app.state.mcp_refresh_task` alongside the scheduler cancel — a 4-line addition that makes shutdown deterministic.

### [LOW] Local API token entropy derives from `RandomState` (SipHash) rather than a CSPRNG

**What I saw (VERIFIED).** `frontend/desktop/src-tauri/src/backend.rs:88-112` — `local_api_token` builds the per-install token from two `RandomState::finish()` outputs plus pid/time. The in-code comment documents the "without pulling in a dependency" tradeoff, and the token is never persisted (backend-py/app/lib/local_api_auth.py:46-48, 125).

**Why it matters.** SipHash output with random keys is pseudorandom but not a documented CSPRNG; the token is the LAN boundary (local_api_auth.py:71-80). The practical risk is low for a local install, but this is the one credential in the system that could be stronger for free.

**What good looks like.** Use `getrandom::getrandom` (likely already in the tree via transitive deps — check `Cargo.lock`) or `rand::random::<[u8; 16]>()` for the 128 bits, keeping the same env/IPC plumbing.

### [LOW] `_pollingWatch` is a one-shot recorder misnamed as a polling watcher

**What I saw (VERIFIED).** `backend-py/app/services/environment_watcher.py:159-176` — the "Fallback polling-based watcher (no watchdog)" runs `git branch --show-current` once, records one change, and returns. It never polls. The v2 `EnvironmentWatcher` (line 216 `start`, 240 `stop`) is the real watcher; `watch()` (line 141-157) only falls back to `_pollingWatch` on exception.

**Why it matters.** The name and docstring promise continuous fallback coverage that doesn't exist; a future caller relying on it gets exactly one event. Dead/misleading code in a small file is cheap to fix now and expensive to debug later.

**What good looks like.** Either delete `_pollingWatch` and let `watch()` fail loudly, or make it an actual interval loop (with the 5 s cadence the old design intended) — one or the other, not a lie in between.

### Candidate removals / simplifications

- **[REMOVAL] Drop the MSI bundle target.** `tauri.conf.json:32-35` builds both `msi` and `nsis`, but the entire update pipeline — hooks, marker, waiter, `launch_installer_and_exit`, the payload stamp — is NSIS-only (installer.nsi, hooks.nsh, backend.rs:3719+). The MSI is published and never consumed by the updater; removing it halves installer build time and deletes a hookless divergence surface. Owner call: keep only if enterprise deployment actually wants it.
- **[REMOVAL] Collapse the triple-name hedging once the binary name is fixed.** `August.exe`/`AugustHarness.exe`/`augustharness.exe` are enumerated in hooks.nsh:110-112, backend.rs:3365 (`Get-Process -Name 'August','augustharness'`) and the waiter's name regex (backend.rs:3360-3361). With `mainBinaryName` pinned to `AugustHarness` (tauri.conf.json:6), these lists can shrink to the one real name (keeping the regex prefix match for installers), removing three places that must stay in sync by hand.
- **[REMOVAL] Delete `_pollingWatch`** (environment_watcher.py:159-176) per the finding above — the v2 `EnvironmentWatcher` covers the need, and the fallback's one-shot behavior is worse than an honest failure.

## Strengths worth keeping

- **Identity-checked health probes everywhere**: `healthOkOnPort` requires the backend's `python: true` marker (backend.rs:2060; main.py:750) — a foreign service on the port can't fool the supervisor.
- **The two install guards hold**: the payload script refuses to stage user state (desktop-backend-payload.mjs `assertPayloadHasNoUserState`, walking only `backend-py/app`, `backend-py/sidecar`, `skills`), and `wipeStaleTree` refuses any target outside `backend-runtime/` (backend.rs:782, 817-818) — with unit tests (backend.rs:4195-4225).
- **Update ordering is right**: waiter scheduled before installer launch (useAppUpdate.ts:213, 223), stale marker deleted when the waiter is scheduled (backend.rs:3339-3345), marker written unconditionally by POSTINSTALL (hooks.nsh:164-169), installer routed to `reinst_done` under `/UPDATE` so no uninstall wizard runs (installer.nsi:364-381), restart stays the user's click with a durable per-version "Not now" (useAppUpdate.ts:158-163, 364, 385-396).
- **Signature verification at both ends**: release verifies what it publishes; the app verifies the staged installer before running it (backend.rs:3450 `verifyInstallerSignature`, re-checked in `launch_installer_and_exit`), and `check:minisign` pins the crate's published test vector plus key equality across tauri.conf.json and backend.rs (check-minisign.mjs:81-105, 152-192; backend.rs:28).
- **Quit path is watchdog-protected and idempotent**: 2 s force-exit if the webview can't confirm (lib.rs:51-62), `RunEvent::ExitRequested | Exit` both reap the backend and release the lock (lib.rs:194-200), the orphan sweep is skipped when the port is already free (backend.rs:1785-1791, `portIsFree` bind-probe at 1805-1826).
- **LAN gate fails closed and keeps loopback working**: `/api/health` stays open, loopback peers are exempted by transport fact, and a missing token in LAN mode returns 503 (local_api_auth.py:138, 347-348, 350-371) — so the desktop supervisor's loopback probe survives LAN mode.
- **Release pipeline is CI-only with provenance**: clean-tree check, reusable CI as a hard dependency, `uv lock --check`, tag↔package.json match, pinned Python 3.12.9/ngspice with sha256s and a run probe, `--require-hashes` wheel installs, payload stamp over the whole staged tree (release-desktop.yml; prepare-desktop-backend.mjs).
- **Startup order is deliberate**: data-dir migration → instance lock → supervisor state → marker cleanup → backend thread, all before the window is interactive (lib.rs:93-137).

## Coverage ledger

Files opened (relative paths; all read in full unless noted):

| File | Lines | Notes |
|---|---|---|
| frontend/desktop/src-tauri/src/backend.rs | 4559 | Full read across passes; supervisor, ports, instance lock, migration, wipe guard, update commands, waiter, minisign |
| frontend/desktop/src-tauri/src/lib.rs | 201 | Full — setup order, quit paths, command registry |
| frontend/desktop/src-tauri/src/main.rs | 6 | Full |
| frontend/desktop/src-tauri/src/tray.rs | 59 | Full — Show/Hide/Quit tray |
| frontend/desktop/src-tauri/tauri.conf.json | 67 | Full — bundle, NSIS template/hooks, updater plugin |
| frontend/desktop/src-tauri/windows/installer.nsi | 1052 | Full — /UPDATE→reinst_done, POSTINSTALL insertion, old-binary cleanup |
| frontend/desktop/src-tauri/windows/hooks.nsh | 195 | Full — PREINSTALL stop script, POSTINSTALL marker+ExecShell |
| backend-py/app/main.py | 807 | Full — lifespan, startup order, health endpoint, teardown |
| backend-py/app/services/daemon_manager.py | 628 | Full — task registry, kill/shutdown, rehydrate monotonic fix |
| backend-py/app/services/health_monitor.py | 205 | Full — probe_provider |
| backend-py/app/services/environment_watcher.py | 253 | Full — v2 watchdog + _pollingWatch |
| backend-py/app/lib/local_api_auth.py | 429 | Full — LAN gate, token pop, loopback exemption |
| backend-py/app/services/data_migrations.py | ~227+ | Partial (head + middle) — migration ladder shape |
| scripts/dev-app.mjs | 212 | Full — backend spawn for `npm run dev` |
| scripts/dev-tauri.mjs | 197 | Full — Vite+tauri dev orchestration |
| scripts/start-backend.mjs | 144 | Full — standalone backend launcher |
| scripts/check-minisign.mjs | 193 | Full — verifier vector + key equality |
| scripts/minisign-verify.mjs | 272 | Full — standalone minisign verifier |
| scripts/release-desktop.mjs | 735 | Full — latest.json, version sync, publish/draft |
| scripts/check-version-sync.mjs | 125 | Full — 9 version sources |
| scripts/prepare-desktop-backend.mjs | 693 | Full — payload staging, pins, stamp |
| scripts/desktop-backend-payload.mjs | 233 | Full — user-state guard |
| scripts/sync-tauri-version.mjs | 83 | Full — npm-version hook |
| scripts/with-tauri-signing.ps1 | 35 | Full — local signing key loader |
| install.sh | 102 | Full — dev install, version stamp write |
| install.ps1 | 155 | Full — Windows dev install |
| .github/workflows/release-desktop.yml | 179 | Full — build/publish/publish-flip jobs |
| .github/workflows/ci.yml | 138 | Full — gate groups |
| frontend/desktop/src/hooks/useAppUpdate.ts | ~480 | Full + line-pin greps — auto-download, restart flow |
| docs/UPDATE_VERIFICATION.md | ~120 | Full — manual matrix |
| docs/agent/product.md | 162 | Full — update/launch contract |
| docs/agent/release.md | 70 | Full — version sources, tag flow |
| docs/STATUS.md | — | Partial (grep + lines 520-545) — open items |
| frontend/desktop/src/components/shell/BackendStatusPill.tsx | 228 | Partial (1-80) — phases, watchdog |
| frontend/desktop/src/sections/chat/offline-queue-store.ts | ~80 | Partial (1-60) — parked sends |
| backend-py/app/august_proxy.egg-info | — | Existence only — rename evidence |

Skipped sub-areas, with reasons:

- `frontend/mobile/` — out of scope (area 3+).
- Backend harness/chat/memory internals — out of scope; only lifecycle-relevant services read.
- `src-tauri/Cargo.toml`, `Cargo.lock`, `capabilities/*.json` — not read; bundle/updater behavior fully covered by tauri.conf.json + backend.rs. No deep-link plugin or scheme handler exists anywhere in the Rust shell (grep across src-tauri returned none) — the "deep links" sub-area is therefore "not implemented", noted here rather than as a finding.
- `scripts/gates.mjs`, `check-api.mjs` (OpenAPI generation), other gate scripts — referenced but not deep-read; the OpenAPI version-source contract was verified via check-version-sync.mjs + docs/agent/release.md:15-28.
- `useBackendSetup.ts` and `api/client.ts` full bodies — verified via targeted greps and BackendStatusPill usage; phase union and health-poll behavior confirmed indirectly.

## Open questions for the owner

1. Should the installer or the waiter own the post-update relaunch? The code (hooks.nsh:171-193) and the docs (product.md:59-63) currently disagree — pick one and align both, then fix the `August.exe` name.
2. Is the MSI target load-bearing for any deployment, or can it be dropped from `tauri.conf.json`?
3. Should tag-push releases also go through the draft gate, or is immediate publish an accepted tradeoff for the sole-maintainer workflow?
4. Is `brain_backup.ensure_current_backup` atomic (temp+rename) under mid-write process death? If not, the shutdown cancellation gap becomes a data-safety item.
5. When will the packaged NSIS matrix get its first real run — and can parts of it be promoted into static gates (binary name, marker paths, hook/PSTimer constants)?
