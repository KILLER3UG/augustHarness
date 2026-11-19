# Audit — backend routers/services/daemons/persistence + mobile companion

Complete. Read-only static audit — no files in the repo were modified.
Repo: C:/Dev/augustHarness @ 9804b069 (master)

## Verdict
Areas 9 and 10 are in good shape structurally and uneven in the details that only show up at the edges. The persistence layer is the strongest part of the backend: `filestore.py` gives the whole area one atomic, fsyncing write path and one honest JSONL reader, the markdown record is rebuilt on boot, and the migration ladder is documented as rules rather than as history. The routers are disciplined — 448 declared routes with exactly one duplicate path, no bare dict error returns, consistent `HTTPException` envelopes. What is weak is lifecycle bookkeeping around background work: a daemon row whose status is written once and never updated, a migration step that can silently truncate `providers.json`, a health-monitor loop that is started and never stopped, and a teardown where 14 of 16 failure handlers are a bare `pass`. None of those are architectural; all of them are the kind of defect that costs a user their config or their money. The mobile companion is the thinnest of the three frontends by a wide margin — a 504-line WebView wrapper with a pairing sheet — and the owner decision that would give it a reason to exist (second client vs second August, `docs/ROADMAP.md:101-107`) is still open, so it currently has neither a native capability nor a settled role.

## Findings

### [HIGH] `daemons.json` status column is write-only, so a restart can resurrect a daemon that already finished
- What I saw: spawn hard-codes `status: 'running'` (`backend-py/app/services/daemon_manager.py:205-206`); the natural-exit path updates only the in-memory `DaemonResult` and never rewrites the store (`:504-508`, loop exits at `:488`/`:502`); only `kill()` deletes the row (`:236-242`). `rehydrate_from_db` rehydrates every row whose stored `status == 'running'` (`:275-277`). The only backstop is the persisted wall-clock deadline (`:296-304`).
- Why it matters: the packaged quit force-kills the process (see `main.py:296-297` comment), so completed daemons are routinely left `running`. With `HARNESS_DAEMON_TTL_S` default 3600 s (`:103`) the deadline usually saves you — INFERRED: the bug bites when the quit lands inside the TTL, and then a poll loop restarts every 30 s (`:485`) spending model calls with no UI to stop it. Same defect class the uuid-suffix fix (`:159-165`) addressed.
- What good looks like: `_runLoop`'s exit path persists terminal status before notifying; a boot sweep drops rows past their deadline instead of trusting in-memory monotonic math; rehydrate reports skipped rows.

### [MED] Migration ladder steps v2/v3 write `providers.json` non-atomically, violating the ladder's own re-runnability rule
- What I saw: `backend-py/app/services/data_migrations.py:107` and `:138` use bare `p.write_text(...)`. `_step_4` does it right with `mkstemp` + `os.replace` (`:197-204`) and explains at `:193-196` why v2/v3 were shipped wrong.
- Why it matters: ladder rule 2 (`:14-16`) demands every step tolerate its own partial effects. A crash mid-write truncates the file; both readers swallow that (`:96-98`, `:127-129` return quietly) and `apply_migrations` then stamps the version (`:225`), so the loss is permanent and silent — and it is the user's provider configuration, the thing the two install guards exist to protect.
- What good looks like: a single `_atomic_write_json` helper used by every step; since "add first, remove later" (`:20`) forbids editing shipped steps, add a v5 repair step that restores a `.bak` when a truncation marker is present.

### [MED] `lifespan` is 260 lines of prose-ordered boot with a teardown that swallows failures silently
- What I saw: `main.py:134-541`. Startup stages run strictly by comment: migrations (`:165-174`) → hooks (`:176-191`) → health monitor (`:193-199`) → llama sweep (`:201-208`) → OAuth mirror (`:210-239`) → log hub (`:240-246`) → brain restore/SQLite retirement (`:250-267`) → orphan sweeps (`:286-304`) → backup task (`:305-314`) → cognitive boot (`:336-342`) → gateway (`:343-350`) → orchestrator (`:351-357`) → learning scheduler (`:385-394`). Teardown `:416-541` has 16 `except Exception:` handlers, 14 of which are a bare `pass` with no log line at all; only the terminal-session drain (`:521`) and the MCP drain (`:528`) log, both at DEBUG with `exc_info=True` (VERIFIED by a line scan of every handler in the teardown region: 420, 426, 430, 437, 444, 453, 463, 473, 482, 488, 495, 502, 509, 538 are bare `pass`).
- Why it matters: `_TeardownClock` (`:400-414`) exists to name a slow shutdown step, but a step that *throws* is indistinguishable from one that succeeded — a failed `event_log.flush` or `flush_pending_saves` silently loses the last turn's messages, which is exactly what those two comments (`:456-458`, `:466-468`) say they prevent. Startup order is load-bearing (migrations before any store; brain restore before any read) yet nothing asserts it.
- What good looks like: named stage lists (`_STARTUP_STAGES`/`_SHUTDOWN_STAGES` of callables) so order is data; `logger.debug(..., exc_info=True)` in every teardown `pass`.

### [MED] Mobile companion is a WebView wrapper of the desktop SPA — no native surface, no offline, token in RAM only
- What I saw: `frontend/mobile/App.tsx:230-273` renders one `<WebView source={{uri: proxyUrl}}>`; the entire native value-add is a CSS bootstrap (`:22-78`), a theme bridge (`:58-74`), a pairing sheet (`:288-370`) and a status bar (`:216-229`). Token is `useState` only (`:176`) with the reason stated at `:168-175`: no `expo-secure-store` dependency, and a dependency change needs the owner. `MOBILE_WEB_BOOTSTRAP` is injected on every document load (`:243-244`), and a token change forces a remount (`:204-207`).
- Why it matters: this is a second *client* in name only — it is the same React SPA in a shell. Every desktop-only capability (file pickers, `target=_blank`, multi-window, service workers, push) is degraded by WebView, and the phone cannot do anything the browser cannot. INFERRED: the CSS block's own comment (`:49-53`) records that most of its selectors have matched nothing since the desktop moved to React in June.
- What good looks like: either commit to the wrapper (delete dead CSS, add offline shell + push registration + biometric-gated token storage) or build the minimal native companion: pair once, chat over SSE, session list, memory/skills read.

### [MED] Bare `except Exception: pass` is still the dominant swallow pattern — 411 sites — while the named helper exists and is used 10 times
- What I saw: `grep -c "except Exception"` over `backend-py/app` = 1,558; `except Exception` immediately followed by `pass` = 411. `services/best_effort.py:1-54` exists for exactly this, documents the problem as "941 blocks, ~800 followed by a bare pass" (`:3-4`) and is imported in only 10 places outside its own module.
- Why it matters: `main.py`'s own teardown is 14 of those 411 (`main.py:416-541`), so a failed flush during quit is unobservable. The helper's strict mode (`HARNESS_STRICT_BEST_EFFORT=1`, `:11-12`) also cannot catch what never routes through it.
- What good looks like: extend the BLE001 ratchet (`pyproject.toml` per-file-ignores, cited at `best_effort.py:18-20`) file by file; convert `main.py` teardown first, since that is where a swallowed error costs user data.

### [MED] God-files with no internal seams: `services/workbench/workbench.py` 7,683 lines, `routers/workbench.py` 2,959, `skill_service.py` 2,828, `skill_distiller.py` 2,184
- What I saw: line counts via `wc -l` (VERIFIED). `routers/workbench.py` is one router with 2,959 lines against 47 router modules and 118 entries in `app/services/`; `memory_store/` alone is 9,231 lines across 14 files (`wc -l backend-py/app/services/memory_store/*.py`), showing the split pattern already works one directory over.
- Why it matters: a 7.7k-line module is a merge-conflict magnet and defeats targeted review; the area's own gate (`scripts/check-loop-imports.mjs`, VERIFIED present, `:76-82`) only guards `loop/` → workbench direction, so cycles inside `services/workbench/` are unguarded.
- What good looks like: split `workbench.py` by responsibility the way `memory_store/` was split (sessions, turn loop, budgets, tool dispatch, subagents) and give `routers/workbench.py` one module per resource.

### [MED] Health probes treat any non-5xx as healthy, so a 401/403 provider reports "healthy"
- What I saw: `services/health_monitor.py:145-152` — `success = resp.status_code < 500`, `error = None if success else f'HTTP {resp.status_code}'`. `ProviderHealth.status` then derives `healthy | degraded | unreachable` from that boolean (`:37-52`).
- Why it matters: a provider whose key was revoked or whose base URL moved behind an auth wall returns 401/403 forever and is reported `healthy` with a 0-error ring buffer. The monitor's whole purpose is to tell the user which gateway is lying to them, and the one failure class that matters most in practice (auth) is invisible. `last_error` stays `None` too, so the UI has nothing to show.
- What good looks like: `200 <= status < 300` is healthy, `401/403` is its own state (`unauthorized`), `404` is `misconfigured` (wrong base URL — the exact failure the `/models` suffix probe is designed to catch), and 5xx/network is `unreachable`.

### [MED] Health probes run serially, so a slow provider delays every other probe
- What I saw: `health_monitor.py:157-163` `probe_all` loops `await self.probe_provider(pid)` one at a time, each with `_PROBE_TIMEOUT_S = 5` (`:22`); the loop sleeps `_PROBE_INTERVAL_S = 60` after the whole batch (`:196-204`).
- Why it matters: with N configured providers the tick takes up to 5N seconds; at 12 providers a single unresponsive gateway eats the full minute and the loop never sleeps, so probes become continuous rather than periodic. The ring buffer (`_RING_SIZE = 50`, `:23`) then mixes intervals and "last 3" (`:40-46`) means a different wall-clock window per provider.
- What good looks like: `asyncio.gather(*(self.probe_provider(p) for p in ...))`; and record the tick timestamp per probe so `status` can say how stale the sample is.

### [MED] Telemetry ledgers are per-session JSONL with no size cap — the five `telemetry/*.jsonl` files grow forever
- What I saw: `memory_store/sessions.py:567-575` enumerates the per-session child stores as `telemetry/lifecycle.jsonl`, `usage-events.jsonl`, `tool-guardrail-log.jsonl`, `turn-outcomes.jsonl`, plus `session-topics.json`. `learning_scheduler.py:101-112` caps only its own ledger (`_LEDGER_KEEP_PER_JOB`), and `automation_memory.py:192` caps only the automation-runs ledger; `append_ledger(..., max_bytes=…)` exists in `tables.py:191-197` but the telemetry writers do not pass a cap (VERIFIED: `grep rotate_jsonl` finds only `automation_memory`, `tables`, `retired_sqlite`).
- Why it matters: `turn_outcomes` is one row per turn and `tool_guardrail_log` one row per guarded tool call. A heavy user's `dataDir` grows without bound, and the boot-time transcript-index sync (`main.py:276-285`) plus every session delete that rewrites these files (`sessions.py:567-575`) pay a cost proportional to total history, not to the session being deleted.
- What good looks like: pass `max_bytes` for each telemetry ledger (the mechanism is already there and used elsewhere), or a row-count prune in the same shape as `_prune_locked`.

### [MED] Any new numeric brain-config field silently inherits the workbench-loop range `(0, 500)`
- What I saw: `brain_config_service.py` does declare its ranges — 20+ named constants at `:165-228` (`maxAgentDepthRange = (1, 5)`, `budgetSoftTokensRange = (0, 50_000_000)`, …). But the validation branch that consumes them is a per-key `if/elif` chain ending in a bare `else: lo, hi = maxWorkbenchLoopsRange` (`:850-851`), applied to every `kind == 'num'` key the chain did not name. So the default range for an unlisted numeric field is the *workbench tool loop* range, not "no constraint".
- Why it matters: a field added to `allowedKeys`/`fieldKind` but forgotten in the chain accepts 0-500 and silently refuses anything larger — `factBudget` (0-100,000) or `budgetSoftTokensRange` (0-50,000,000) are exactly the shapes that would be rejected with a confusing "must be between 0 and 500". That is the "a knob that refuses its own realistic input is a lie" failure the file itself warns about at `:722-727`. Separately, `getDelegationLimits` (`:552-590`) clamps again at read time with its own literals, and its comment ("Do not clamp a second time here or the two answers can disagree", `:562-563`) is the only thing keeping the two in sync.
- What good looks like: one `_NUMERIC_RANGES: dict[str, tuple[float, float]]` built from the existing constants and consumed by both `validatePatch` and `getDelegationLimits`; make an unnamed numeric key a hard error rather than a silent fallback.

### [LOW] `config.py` does no range validation on `port`, and `settings` is a module-level singleton mutated by `reload()`
- What I saw: `config.py:68` `port: int = int(os.environ.get('HARNESS_PROXY_PORT', '8085'))` — no `ge`/`le` constraint on the Pydantic field (`:65-110` declares none). `reload()` (`:83-91`) re-reads `config.json`/`providers.json` but not env-derived fields. `main.py:210` calls `settings.reload()` mid-lifespan, and `_cors_allow_origins()` reads `settings.port` at import time (`main.py:40`, used at `:563`) — after reload, CORS was already fixed.
- Why it matters: an out-of-range or non-numeric `HARNESS_PROXY_PORT` produces a confusing bind failure rather than a startup error; and because CORS origins are captured once (`main.py:69-73` documents this deliberately for the LAN flag) the reload at `:210` cannot change them, which is correct today but is an unguarded invariant.
- What good looks like: `port: int = Field(8085, ge=1024, le=65535)`; assert in `_cors_allow_origins()` that it is called before the middleware is added, or compute origins lazily.

### [LOW] Docs still describe the retired SQLite record — verified at `docs/CONFIGURATION.md:29` and `:944`
- What I saw: `docs/CONFIGURATION.md:29` lists `data/august_brain.sqlite` as the store for "Sessions, messages, memory, audit, kv, episodes", and `:944` calls it the "**SoT** for sessions, memory, audit, kv, episodes". `docs/DEVELOPER_GUIDE.md:40` still draws `memory_conn.py` in the tree diagram, and that file does not exist (VERIFIED: `ls backend-py/app/memory_conn.py` → no such file).
- What the code does instead: `main.py:256-267` runs a one-time retirement import from the SQLite file, `main.py:268-275` rebuilds the `MEMORY.md` mirror on boot, `memory_store/mirror.py:1-10` states `<dataDir>/memory/` **is** the record, and `memory_store/` is 14 file-backed modules (9,231 lines).
- Why it matters: an operator following CONFIGURATION.md backs up the retired database — the one file that is no longer authoritative — and an agent reading DEVELOPER_GUIDE goes looking for a module that was deleted. This is the exact "capability the model cannot use is not advertised" failure in reverse: a store that no longer exists is still advertised.
- What good looks like: rewrite both passages to point at `dataDir/memory/` (one markdown file per fact + `MEMORY.md`), `dataDir/state/`, `dataDir/transcripts/`, `dataDir/telemetry/`; note the retired DB is read once by `retired_sqlite.run_sqlite_retirement()`.

### [LOW] `deferred_writes.py` is a retired no-op still wired into the shutdown path — with a comment that now lies
- What I saw: `services/deferred_writes.py:1-37` declares itself "RETIRED with SQLite", `defer_commit()` is a no-op (`:22-27`) and `flush_thread_pending()` returns 0 (`:31-33`). Yet `main.py:466-475` still calls it under the comment "Flush debounced telemetry commits (P4.2): turn outcomes / lifecycle / internal_state inside the ≤2s window would otherwise be lost on shutdown" — which is no longer true, since the file stores fsync at the call site (`filestore.py:83-95`, VERIFIED atomic + `os.fsync`).
- Why it matters: a shutdown checkpoint that does nothing while claiming to prevent data loss is worse than no checkpoint — it reads as coverage. And it burns one of the 16 `_tc.mark()` checkpoints the `_TeardownClock` reports on.
- What good looks like: delete the module, its importers (`defer_commit`/`flush_thread_pending` call sites) and the `main.py:466-475` block; or, if the names must stay for the transcript domain, keep the module and delete the call site plus the misleading comment.

### [LOW] `filestore.read_json` treats a corrupted store as "absent" — correct for boot, dangerous for money-shaped data
- What I saw: `services/filestore.py:66-80` returns `default` on `(OSError, ValueError)` after logging a warning. `daemon_manager._load_daemon_rows` relies on that (`:49-53`) and then `_save_daemon_rows` overwrites the file with whatever survived the filter (`:56-57`), so a partially-corrupt `daemons.json` is silently replaced by a subset.
- Why it matters: the write path is atomic and fsynced (`filestore.py:83-95`) — good — but the read path has no "quarantine and keep the bad bytes" step, so a parse error destroys the only copy of the record it could not read.
- What good looks like: on `ValueError`, rename the bad file to `*.corrupt-<ts>` before returning `default`, so recovery is possible and the loss is auditable.

### [LOW] `/v1/models` is registered twice; the loser is a pure delegation stub
- What I saw: `routers/models.py:78` declares `@router.get('/v1/models')` gated by `require_gateway_key` (`:79`), and its docstring (`:82-88`) records that it shadowed an unauthenticated twin. `routers/proxy.py:585-591` still declares the same path, whose entire body imports and calls `openaiModels` (`:588-591`). `main.py:660` registers `modelsRoutes` before `main.py:661` registers `proxyRoutes`, and FastAPI is first-match-wins (the rule `main.py:648-649` cites for the skills/packs case) — so the proxy copy is unreachable (INFERRED from include order).
- Why it matters: this is the *only* duplicate path among 448 declared routes (VERIFIED by scanning every `APIRouter(prefix=…)` and decorator in `app/routers/*.py`), and it is a duplicate the code already knew about and documented instead of deleting. A dead route that shadows nothing but reads as live is how the next person adds a third copy.
- What good looks like: delete `proxy.py:585-591`; keep the single authenticated implementation in `models.py`, whose docstring then describes reality.

### [LOW] The companion never consumes an SSE stream itself — parity rests entirely on the desktop SPA inside the WebView
- What I saw: `grep -rn "access_token\|token=" backend-py/app/routers/*.py` returns **zero** hits (VERIFIED), so the "token in the query string for the two `EventSource` streams" claim at `docs/ROADMAP.md:227-230` is not visible in any router. What exists is the injection path: `frontend/mobile/pairing.ts:105-112` publishes `window.__HARNESS_LOCAL_TOKEN__`, read by `frontend/desktop/src/api/client.ts:41-43` (`hostGlobalToken()`).
- Why it matters: the phone's streaming chat, session list and every other capability come from the web bundle, not from the native app. Any parity claim for area 10 is therefore a claim about the SPA, and the native shell contributes only a viewport meta tag, a CSS reset, a theme message and a token. INFERRED: the two token-in-query EventSource routes referenced by the roadmap are either in the desktop bundle's own fetch layer or have been removed; either way the roadmap paragraph is stale.
- What good looks like: correct `docs/ROADMAP.md:227-230` to describe the actual transport (`window.__HARNESS_LOCAL_TOKEN__` + `client.ts`), and state plainly in the mobile README that the companion has no native data path.

### [LOW] `npm test -w frontend/mobile` needs a live backend, so the "no test suite" policy has a hidden runtime dependency
- What I saw: `frontend/mobile/package.json` maps `test` → `typecheck && audit:parity`, and `audit:parity` defaults to `HARNESS_AUDIT_BASE_URL || 'http://127.0.0.1:8085'` (`scripts/audit-mobile-parity.js:14`). The script also walks `frontend/desktop/src` (`:13`) and compares route surfaces.
- Why it matters: `gates:mobile` therefore fails on a clean checkout with no backend running, for a reason that has nothing to do with the diff. That is the kind of gate people learn to skip. AGENTS.md is explicit that there is no test suite and no `test` script; this package quietly keeps one under a different name.
- What good looks like: split the static half (desktop-source comparison, no server) from the live half (needs a backend), so the gate that runs in CI is the one that cannot flake.

### [LOW] `main.py` binds `require_lan_token` as a global dependency, but 28 of 47 router modules never reference an auth helper — correct today, invisible tomorrow
- What I saw: `main.py:554-559` installs `Depends(require_lan_token)` on the app, with the reasoning written out (`:556-558`: a route added next year cannot be reachable by accident). Scanning all 47 router modules for `local_api_auth|require_lan_token|local_api_guard|require_gateway_key|require_session_token`: 19 reference one, 28 do not (VERIFIED by script). The 28 are mostly genuinely internal surfaces (`docs_state`, `whats_new`, `usage`, `kanban`, `calendar`, `skills`, `sessions`, `config`), which is the point — the global dependency is what makes them safe.
- Why it matters: the global dependency is the right design and makes the 17 non-referencing routers harmless. But nothing distinguishes "this router is deliberately public" from "this router forgot", so a future author reading one of the 17 has no signal either way.
- What good looks like: a one-line module docstring convention (`# public: no token door, safe because …`) on the intentionally-open routers, checked by a gate that asserts every router is in one list or the other.

### [LOW] `settings.reload()` at `main.py:210` cannot affect CORS, and nothing says so at the call site
- What I saw: `_cors_allow_origins()` reads `settings.port` (`main.py:40`) and its result is passed to `add_middleware` at import time (`:561-567`); `settings.reload()` runs later at `:210` and does not re-read env-derived fields (`config.py:83-91`). The comment at `main.py:69-73` explains why the middleware takes its list once, but it is 500 lines away from the reload that a reader might think refreshes it.
- Why it matters: this is correct-by-accident-of-ordering. A refactor that moves `add_middleware` below the lifespan body (a natural cleanup, since middleware order matters) silently pins CORS to pre-reload settings.
- What good looks like: compute the origin list once into a module constant next to the middleware registration and have `_cors_allow_origins()` read that constant, so a second call site cannot disagree.

### [LOW] Session saves are debounced in memory and flushed at teardown — the one path that can still lose a turn is a hard kill
- What I saw: `workbench/sessions.py:767-790` `save_sessions_now()` and `:791-800` `flush_pending_saves()`, both called from `main.py:534-540` and `:456-465`. The debounce window is not stated in the file head read here; the AGENTS.md-level contract is "the user's chat history must survive every restart" (`main.py:531-533`).
- Why it matters: the packaged quit is a force-kill (`taskkill /T /F`, cited in `deferred_writes.py:6-7`), so a `flush_pending_saves()` in an async teardown may not run at all on the real quit path. The safety net is therefore the shell's own save, not this one — and nothing in the backend says which is authoritative.
- What good looks like: state the debounce interval in the docstring, and confirm (in the shell, not here) whether `flush_pending_saves` is reachable on the packaged quit path; if not, shorten the debounce or write on every turn boundary.

## Removal candidates
1. **`routers/proxy.py:585-591`** — the shadowed `/v1/models`. Delete; `models.py:78` is the real one. (No importers; verified by grep that only the two decorators exist.)
2. **`services/deferred_writes.py` (37 lines) + `main.py:466-475`** — a retired no-op kept alive by its own importers. Deleting the shutdown block removes one of the 14 silent teardown handlers and one misleading comment.
3. **`daemon_manager`'s durable store, if rehydration is not wanted.** The file, the `status` column, `expires_at` string math and `rehydrate_from_db` (78 lines) exist only to survive a force-kill. If the product decision is "daemons do not survive a restart", all of it goes and the HIGH finding above disappears with it.
4. **`services/workbench/workbench.py` split into `services/workbench/{sessions,turn,budget,tools,subagents}.py`** — not a removal but the largest simplification available; `memory_store/` proves the pattern (14 modules, one facade `__init__.py` re-exporting the old names, so no importer changes).

## Strengths worth keeping
- `filestore.py` is the quiet backbone and it is right: one `home(kind)` gate for the four store roots (`:54-63`), `write_json` atomic + `fsync` (`:83-95`), `append_jsonl` fsync per line (`:132-140`), and `load_jsonl` that distinguishes a torn trailing line (debug) from mid-file corruption (warning with the 1-based line number, `:143-149`). Copy this pattern everywhere.
- `best_effort.py` names the problem honestly and gives the fix a ratchet (`:1-21`) — even at 10 adoptions against 411 bare passes, the direction is right and the strict mode is a real lever.
- `error_families.py` is a real vocabulary with a written migration story (`:1-25`): one enum shared by the loop and the recorder, and the recorder's signature-stable labels deliberately *not* renamed so lesson-promotion state is not orphaned. That is how you retire a taxonomy.
- The migration ladder's rules are written as rules (`data_migrations.py:10-24`), and step 4's comment (`:193-196`) records that steps 2 and 3 were shipped wrong. Self-criticism in the code is worth keeping even after the fix lands.
- `daemon_manager` keeps its defect history where the next reader will trip over it: the uuid suffix (`:159-165`), `last_hash` on `info` rather than the ephemeral result (`:591-598`), reaper arming inside rehydrate (`:323-335`). Comments that explain a past bug are the cheapest regression test this repo has.
- Route surface is consistent: 448 declared routes across 47 modules, exactly one duplicate path, and all routers carry an explicit `prefix=` except the five legacy flat ones (`harness_mcp`, `models`, `proxy`, `security`, `service_connections`). Error envelopes are consistent too: 355 `HTTPException` sites, each with an explicit `status_code` (400×142, 404×132, 500×18, 409×11, 502×9, 503×8, 403×8, 401×3, 504×1) and zero bare `return {'error': ...}` anywhere in `routers/` (VERIFIED by grep and by script).
- `/api/health` has a single owner with the history of the collision in its docstring (`main.py:727-754`), and its `network` block uses the exact field names the phone panel needs (`:742-745`) — an API designed for its second consumer.

## Coverage ledger
Files opened (relative):
- `backend-py/app/main.py` (all 807)
- `backend-py/app/config.py` (all 113)
- `backend-py/app/services/data_migrations.py` (all 227)
- `backend-py/app/services/daemon_manager.py` (all 628)
- `backend-py/app/services/best_effort.py` (all 54)
- `backend-py/app/services/error_families.py` (1-40 of 129)
- `backend-py/app/services/deferred_writes.py` (all 37)
- `backend-py/app/services/cognitive_boot.py` (1-153)
- `backend-py/app/services/filestore.py` (40-100, 132-160, 198-239 index)
- `backend-py/app/services/health_monitor.py` (all 205)
- `backend-py/app/services/learning_scheduler.py` (1-45, 439-470)
- `backend-py/app/services/environment_watcher.py` (1-40, symbol index)
- `backend-py/app/services/memory_store/__init__.py` (1-60)
- `backend-py/app/services/brain_config_service.py` (symbol + range index via grep)
- `backend-py/app/routers/proxy.py` (33-80, 408-591)
- `backend-py/app/routers/models.py` (70-110)
- `backend-py/app/routers/*.py` — machine scan of all 47 for `APIRouter(prefix=…)` + decorator paths (448 routes, duplicate detection)
- `frontend/mobile/App.tsx` (all 504), `frontend/mobile/pairing.ts` (1-119 via grep index), `frontend/mobile/package.json`, `frontend/mobile/scripts/audit-mobile-parity.js` (1-50 of 301)
- `docs/ROADMAP.md` (80-110, 215-235)
- `scripts/check-loop-imports.mjs` (symbol index)

Skipped, with reason:
- `services/workbench/workbench.py` (7,683) — line count + the symbol `brain_config_service` imports from it. A full read is its own audit; flagged as removal candidate 4.
- `skill_service.py` (2,828), `skill_distiller.py` (2,184), `memory_store/consolidation.py` (1,888), `rest.py` (1,396), `brain.py` (1,324) — line counts only.
- `routers/workbench.py` (2,959) — route decorators and `HTTPException` counts only.
- `learning_scheduler.py` job bodies (221-405), `environment_watcher.py` internals (140-253) — symbols and entry points only.
- Desktop frontend, gateway messaging internals, provider clients — other areas.
- No execution: read-only audit, no tests, no builds, no network, no git mutations.

## Open questions for the owner
1. **Is the phone a second client or a second August?** `docs/ROADMAP.md:101-107` says the decision is open and names the trade (remote-control surface, small vs full local install, a second August). Today's WebView shell silently answers "second client of the desktop's own backend" without anyone deciding. Which is it?
2. **May `frontend/mobile` take `expo-secure-store`?** `App.tsx:168-175` and `docs/ROADMAP.md:222-226` both say the token is RAM-only because a dependency needs the owner. It is the worst UX in the companion (re-pair after every kill) and the fix is one dependency.
3. **Should daemons survive a restart at all?** If not, `daemons.json`, its `status` column, the `expires_at` string math and `rehydrate_from_db` (78 lines) all go, and the HIGH finding disappears with them.
4. **`deferred_writes.py` — delete or keep?** Retired no-op with live importers in the transcript domain.
5. **Should teardown failures be silent?** 14 bare `except Exception: pass` blocks in `main.py`'s teardown. Converting them to logged swallows is mechanical, but it will surface failures nobody has seen since the writes became durable — do you want them found?
6. **Is `HARNESS_ENABLE_DOCS=1` a documented support step?** `/docs` off by default (`main.py:82-86`) is right, but there is no written "turn it on, capture, turn it off" path for support.
7. **Is `npm test -w frontend/mobile` allowed to need a live backend?** It currently does, which contradicts the no-test-suite policy in spirit even if not in letter.
