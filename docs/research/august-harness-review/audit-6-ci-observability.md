# Audit — CI, gates, observability, developer experience

Branch `master`, HEAD `9804b069`. Read-only. `node scripts/gates.mjs static` was run and
passed (the only gate executed; everything else was read).

> **Provenance note.** This area was delegated to a time-boxed subagent, which produced a
> draft skeleton and then stalled without writing findings. The audit below was therefore
> completed directly, by reading `scripts/gates.mjs`, `.github/workflows/{ci,release-desktop}.yml`,
> `package.json`, the gate scripts, and the observability modules. Everything here is
> VERIFIED unless marked INFERRED.

## Verdict

The gate architecture is **better than the brief's premise suggests**, and better than most
repos of this size. `scripts/gates.mjs` is a single list that CI, release and a developer
all run — the header records why ("a workflow that hand-lists gates is how the release job
came to skip ten of them"). `check:sse` in particular is a genuinely sophisticated gate: it
detects **six** distinct failure classes, including the exact one that matters most —
"emitted and schematised but NOT dispatched — THE SILENT DROP" — with an explicit
`SCHEMA_ONLY` allowlist that requires a written reason per frame and fails on a stale entry
in either direction.

The real weakness is not the gate set, it is **what the gate set is structurally blind to**.
Because the repo has no test suite (owner decision), the only detectors are static. Four
classes of defect have no detector at all, and one of them — the wrong-binary relaunch — is
a live HIGH defect today. The gate set is strong on *consistency* (does the OpenAPI match,
do the versions match, is every tool claimed) and absent on *behaviour* (does the app start,
does the update come back, does a provider actually answer).

## Findings

### [MED] `subagentFanout` is green in `check:sse` by an allowlist entry, while nothing renders it
- **What I saw (VERIFIED):** `check-sse-parity.mjs:95-101` lists `subagentFanout` in
  `SCHEMA_ONLY` with a recorded reason; `:345-346` and `:374-376` print "deliberately
  undispatched". `:409-414` fails the gate if a `SCHEMA_ONLY` entry later gains a dispatcher
  case — so the allowlist is maintained in both directions. But the frame is still never
  rendered (`api/workbench/streamEvents.ts` has no case, no `default:`), so the subagent
  fleet receipt still does not reach the user. The gate correctly records the *decision*;
  it cannot notice that the decision is wrong.
- **Why it matters:** the gate's honesty is what makes this a product bug rather than a
  tooling bug. The receipt is the answer to "why did only 6 of 8 lanes start" and it is
  being deliberately dropped.
- **What good looks like:** implement P1-1 (render it), then remove the `SCHEMA_ONLY` entry —
  the gate will then hold the new dispatch case in place.

### [MED] `gates:mobile` requires a live backend, so it fails on a clean checkout
- **What I saw (VERIFIED):** `frontend/mobile/package.json` maps `test` →
  `typecheck && audit:parity`; `audit:parity` defaults to
  `HARNESS_AUDIT_BASE_URL || 'http://127.0.0.1:8085'`
  (`scripts/audit-mobile-parity.js:14`). `gates.mjs` puts that under the `mobile` group.
- **Why it matters:** a gate that fails for a reason unrelated to the diff is a gate people
  learn to skip. And it contradicts the spirit of "there is no test suite" — this package
  keeps one under a different name.
- **What good looks like:** split the static half (desktop-source comparison, no server)
  from the live half; the static half goes in `gates:mobile`.

### [LOW] `e2e:smoke` is the only behavioural detector, and a developer can skip it
- **What I saw (VERIFIED):** `gates.mjs` `smoke` group runs `e2e:smoke`; the `backend` group
  *also* runs it; `all` is `static + backend + frontend + mobile`, so the smoke runs once via
  `backend`. Correct — but a developer who runs only `gates:static` (the group matching their
  diff) gets **no** evidence the app starts, and the smoke is the only thing that would catch
  the wrong-binary relaunch class of defect.
- **Why it matters:** the repo's own docs call `e2e:smoke` "the floor under everything a
  linter cannot see". A developer who runs only the group matching their diff skips the floor.
- **What good looks like:** document (or gate) "run `gates:smoke` before a release tag"; the
  release workflow already depends on `ci` which runs it, so this is a developer-experience
  gap, not a CI gap.

### [LOW] No detector for "the docs describe a store that no longer exists"
- **What I saw (VERIFIED):** `docs/CONFIGURATION.md:29, 944` still call
  `august_brain.sqlite` the SoT; `docs/DEVELOPER_GUIDE.md:40` draws `memory_conn.py` and
  `app/migrations/`, neither of which exists. `check:docs` verifies *numbers* in the agent
  docs and `check:doc-links` verifies *links* — neither checks that a named path exists.
- **Why it matters:** this is the third instance of the same class in this audit (the seven
  stale gap items, the stale SQLite docs, the `subagentFanout` allowlist). The repo has
  excellent structural gates and no *claim* gate.
- **What good looks like:** a `check:doc-paths` gate that extracts backticked paths from
  `docs/**/*.md` and fails on any that does not resolve. Cheap, and it would have caught all
  three.

### [LOW] No mutation testing, no fixture harness for the UI surfaces
- **What I saw (VERIFIED):** no stryker/mutation config anywhere; the frontend has no
  storybook-equivalent. OpenHands ships a playwright matrix (bind-policy/live/mock-llm/
  docker/windows) and stryker mutation testing; DeepSeek ships ~10 vitest configs.
- **Why it matters:** the "no test suite" ruling is deliberate, but the *absence of a fixture
  harness* is a separate choice — and it is why the orca-round UI work was measured in
  isolated mounts and scripted turns rather than in a reusable harness.
- **What good looks like:** an `npm run fixtures` that mounts the chat surfaces with a seeded
  transcript and no backend, so a UI change is verifiable in seconds. This is a new capability,
  not a test suite, and it does not violate the ruling.

### [LOW] The dev loop has no sandbox for the desktop app
- **What I saw (VERIFIED):** `scripts/dev-app.mjs` / `dev-tauri.mjs` exist; no equivalent of
  Hermes's `dev-sandbox.sh` (throwaway `HERMES_HOME`, separate Electron userData, distinct app
  name). `HARNESS_DESKTOP_APP_DATA` exists for debug builds only, and the checkout is still
  named `august-proxy` (the rename is blocked while a session holds the workspace root).
- **Why it matters:** the dev loop contends with the installed app's single-instance lock and
  the real data dir — which is how a stray `HARNESS_DATA_DIR` run can end up staging a
  developer's memory into an installer payload.
- **What good looks like:** the Hermes one-liner (P3-1).

## The blind-spot list

Defect classes with **no detector at all** today. This is the most useful section, and it is
what the "no test suite" ruling actually costs.

| # | Blind spot | Why nothing catches it | Already bitten? |
|---|---|---|---|
| 1 | **The app does not start** (import error, failed migration, lifespan raise, unregistered router) | only `e2e:smoke`; not in `gates:static` | — |
| 2 | **An update does not bring the app back** (wrong binary name, marker/waiter race, locked file) | no packaged-install test; the matrix has never been run | **YES** — `hooks.nsh:193` |
| 3 | **A provider/model path fails end-to-end** (wire-format regression, a 400 from a strict gateway) | `check:api` checks the committed spec, not the live call; no integration test against a real provider | — (historical: the `session_id: null` class) |
| 4 | **A memory write silently loses data** (CAS race, a door that skips the index) | no concurrency test; no invariant test on the record | **YES** — `save_fact` CAS (P0-4), four doors skipping `MEMORY.md` (P1-3) |
| 5 | **A gate passes on zero matches** (scanner that measures nothing) | the repo's own rule says this is possible; only `check:sse` and `check:skills` have negative tests | — |
| 6 | **A renderer is wrong** (a component renders nothing, an animation is flat) | no fixture harness, no visual regression | **YES** — several orca-round defects were found only by measuring in Chromium |
| 7 | **A UI control is unreachable** (a prop never passed, a store with no reader) | no static "reachability" gate | **YES** — `subagentFanout`; the two "dead" stores (which were not dead) |
| 8 | **A doc claims something the tree no longer has** | `check:docs` covers numbers; `check:doc-links` covers links; nothing covers paths | **YES** — the SQLite docs |

Three of the eight have already bitten. That is the honest cost of the ruling, and it is the
argument for the four cheap gates named below rather than for restoring a suite.

## Four cheap gates that would close most of the blind spots

| Gate | Catches | Effort |
|---|---|---|
| `check:doc-paths` | backticked paths in `docs/**/*.md` that do not resolve | S |
| `check:installer-names` | POSTINSTALL exe name ≠ `${MAINBINARYNAME}`; waiter marker path ≠ POSTINSTALL marker path | S |
| `check:event-dispatch` (extend `check:sse`) | any emitted frame with no dispatch case **and** no `SCHEMA_ONLY` entry — already 90% built; the missing half is a `default:` case in `streamEvents.ts` | S |
| `gates:static` includes `node --check` over `scripts/*.mjs` | syntax-rot in the 28 gate scripts | S |

None of these is a test suite. Each is a static consistency check of the same kind the repo
already runs, aimed at the class of defect that has actually shipped.

## Strengths worth keeping

- **One gate list.** `scripts/gates.mjs` is the single source; CI's five jobs each call one
  `npm run gates:<group>`. The header states the failure mode it exists to prevent ("a
  workflow that hand-lists gates is how the release job came to skip ten of them"). This is
  better discipline than most repos with a full test suite.
- **`check:sse` is the best gate in the repo.** Six failure classes, discovered emitters (not
  a hand-list), a supply check that distinguishes an acknowledgement from a real handler, a
  variable-then-emit detector, and an allowlist that fails on stale entries in both
  directions. The comments record the exact defects that motivated each check (`recovery`, the
  background-subscriber acknowledgement).
- **Ratchet baselines as a budget.** `lint-budget.json`, `render-baseline.json`,
  `design-baseline.json`, `naming-baseline.json` — shrink-only budgets that fail when a count
  grows, with the baseline as the floor. This is the right shape for "no new debt" without a
  test suite.
- **`check:truncate`, `check:brain-config`, `check:skills`, `check:tool-schemas`** each close a
  defect that was invisible to every other gate, and each has a written rationale.
- **`e2e:smoke` never touches the user's data dir** (`HARNESS_DATA_DIR` at a temp path,
  removed after) and runs on Windows in CI — the platform the product ships on.
- **The release job builds and does not test**, and says so, rather than pretending a deleted
  suite still runs.

## Coverage ledger

**Read:** `scripts/gates.mjs` (full), `.github/workflows/ci.yml` (gate jobs),
`.github/workflows/release-desktop.yml` (needs/publish structure), `package.json` (all 40
scripts + the `check:*` aliases), `scripts/check-sse-parity.mjs` (SCHEMA_ONLY, the six
checks, the report lines), the `scripts/*.mjs` inventory (28 files), the
`{lint-budget,render-baseline,design-baseline,naming-baseline}.json` presence,
`scripts/audit-mobile-parity.js` (1–50), `docs/DEVELOPER_GUIDE.md`,
`docs/CONFIGURATION.md`, `frontend/mobile/package.json`,
`backend-py/app/services/{event_log,turn_outcomes,health_monitor,error_families}.py` (symbol
index), `backend-py/pyproject.toml` (ruff/mypy config), `backend-py/scripts/harness_eval.py`
(existence + `--ablation` flag).

**Run:** `node scripts/gates.mjs static` → passed. No other gate was run (they need the backend
interpreter or npm deps; a gate run that regenerates `frontend/desktop/src/api/gen/openapi.ts`
is a write, so it is out of scope for a read-only audit).

**Skipped, with reason:** the full bodies of the 28 gate scripts (the pattern and rationale
were read from `gates.mjs` + `check-sse-parity.mjs` + the headers; reading all 28 would not
change a finding); the CI workflow's container/build steps (not gate logic); the mobile parity
audit beyond its base-URL default; `scripts/bench/` and `scripts/harness_eval.py` bodies (the
ablation design is documented in `docs/agent/harness.md`, which was read instead).

## Open questions for the owner

1. Is the four-gate set above worth adding, or does it read as "tests by another name"?
2. Should `gates:smoke` be a documented pre-tag step for humans, given the release workflow
   already runs it via `ci`?
3. Is a fixture harness for the chat surfaces in scope (new capability, not a test suite)?
4. `docs/state/` is validated by the backend, not a static gate — should a stale system-map doc
   be a gate failure?