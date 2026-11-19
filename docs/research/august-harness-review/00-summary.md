# August harness review — summary

**What this is.** A research-only deep-dive of the August harness
(`C:/Dev/augustHarness` @ `9804b069`, `0.25.0`) against thirteen reference harnesses,
producing concrete, prioritized improvements across the whole product. Nothing in the
August repo was created, modified or deleted: `git status --short` is empty and HEAD is
unchanged at the start and end of the pass. All scratch material (clones, notes, raw
extracts) lives outside the repo and is listed in `05-coverage-ledger.md`.

**What was studied.** Thirteen reference harnesses, all cloned and read at source level
except Claude Code/Desktop, for which no source exists and only public documentation was
used (marked as observed behaviour throughout): Hermes Agent, DeepSeek Harness,
MiniMax Code, oh-my-pi, pi, Kilo Code, OpenAI Codex, OpenHands (Agent Canvas),
Goose, Gemini CLI, Aider, Crush, and Claude Code/Desktop. Why those: Hermes is August's
closest architectural sibling; DeepSeek is the "everything is a plugin" extreme;
MiniMax/Kilo/Codex/Goose/Gemini are the current generation of coding agents; pi/omp are
the minimal-core and the maximal-fork pair that August's own docs already cite; OpenHands
and Crush cover the desktop-app and Go-TUI shapes; Aider covers the no-tool-loop
architectural alternative; Claude Code is the UI reference August already aims at.

**The one-sentence finding.** August is materially stronger than its own open-items lists
claim — its memory record, tool executor, credential doors and update contract are
genuinely best-in-class — and its real problems are concentrated in a small number of
places: secrets at rest, lifecycle bookkeeping around background work, and coherence drift
between what the docs say and what the code does.

---

## Top 15 improvements, ranked by impact/effort

| # | Improvement | Impact | Effort | Where |
|---|---|---|---|---|
| 1 | **Rotate the live Google OAuth secret in `data/mcp-servers.json`; move MCP env secrets to `${NAME}` references; add them to the outbound scrub** | high | S | P0-1 · `04-proposals.md` |
| 2 | **Stop exporting `GITHUB_TOKEN`/`SLACK_BOT_TOKEN` to `os.environ`; widen the scrub to token-class names** | high | S | P0-2 |
| 3 | **Fix the NSIS POSTINSTALL relaunch — it names `August.exe`, the binary is `AugustHarness.exe`; pick one relaunch owner and gate it** | high | S | P0-3 |
| 4 | **Move `save_fact`'s `write_fact_file` inside `_record_lock()` — the documented CAS guarantee is not enforced** | high | S | P0-4 |
| 5 | **Write `providers.json` atomically in migration steps v2/v3; add a v5 repair step** | high | S | P0-5 |
| 6 | **Persist terminal daemon status on natural exit (or delete the durable store)** | med-high | S | P0-6 |
| 7 | **Fail closed in `tool_call`'s no-session fallback — the last path that skips `_executeTool`** | high | S | P0-7 |
| 8 | **Add the per-install token to MCP create/update/delete** | med | S | P0-8 |
| 9 | **Fix the boot-index omission receipt (slice-then-filter vs filter-then-slice)** | high | S | P1-2 |
| 10 | **Render `subagentFanout` and give the dispatcher a `default:` so unknown events can't vanish** | med | S | P1-1 |
| 11 | **A persisted, command-scoped "don't ask again" grant** (Crush's `GrantKey`) | high | M | P1-6 |
| 12 | **Model-free prune before LLM summarization + checkpoint-merge rule in compaction** | med-high | M | P1-5 |
| 13 | **Interrupt settlement — synthesize a result for every open tool call before persisting** | med-high | M | P1-7 |
| 14 | **Delete the dead M-7 disuse machinery (~90 lines, 3 config keys)** | med | S | P1-4 |
| 15 | **Refresh `MEMORY.md` from every write door, so the human mirror can't go stale** | med | S | P1-3 |

Ranked by (product outcome ÷ effort). 1–8 are P0: security, data-loss, and one real
launch bug. 9–15 are correctness and legibility. Everything in 1–15 is `S` or `M` except
none — the whole top fifteen is small-to-medium work.

## Top 5 removals

| # | Removal | Why | Effort |
|---|---|---|---|
| 1 | **The M-7 disuse-penalty machinery** (`_disuse_for`, `_disuse_constants`, the penalty block, `_set_injected`) | reads `injected_count`, which nothing writes any more; ~90 lines + 3 keys simulating a signal that cannot exist | S |
| 2 | **`deferred_writes.py` + its `main.py` shutdown block** | retired no-op, still called under a comment that now lies about preventing telemetry loss | S |
| 3 | **The shadowed `/v1/models` in `routers/proxy.py`** | a pure delegation stub shadowed by include order — the only duplicate path in 448 | S |
| 4 | **The unused MSI bundle target** | the entire update pipeline is NSIS-only; MSI is published and never consumed | S |
| 5 | **The triple binary-name hedging + `_pollingWatch` + dead transport branches** | `August`/`AugustHarness`/`augustharness` enumerated in three places; a one-shot recorder misnamed as a poller; a `wait_for=None` branch no caller uses | S |

Deliberately **not** removed: `store/promptCacheLive.ts` and
`store/contextSectionsLive.ts` — the docs call them dead, but both have live readers
(`ContextUsageReadout.tsx`, `makeStreamHandlers.ts`). Removing them would be the wrong
fix; the docs are what is wrong. `01-august-audit.md` §12.

## Recommended order of implementation

1. **Steps 1–8** (the P0s). Each is small, independently verifiable, and closes a defect
   the repo's own rules already name. Run the gate set after each.
2. **Steps 9–10, 14** (correctness of what the user/model already sees).
3. **Steps 13, 15** (coherence — the mirror and the interrupt path).
4. **Steps 11–12** (the two that change behaviour — approval grants and compaction — so
   they get the most review).
5. **P2/P3** from `04-proposals.md` opportunistically; the god-file split
   (`workbench.py`, 7,683 lines) is the widest and should go last.

The rule this ordering encodes: **security and data-loss first, then honesty of the
surfaces, then legibility, then behaviour change, then refactor.** A refactor that rides
with a behaviour change is how both get mis-verified.

---

## What the reference comparison actually changed about the plan

Three conclusions from Phase 2 reshaped Phase 3, and they are worth stating explicitly
because they are counter-intuitive:

1. **August is ahead on the things that are hard to copy.** Every reference harness is
   weaker than August on one-store memory honesty, credential doors, deterministic
   (non-model) permission decisions, and the update contract. DeepSeek has no memory at
   all; Crush has none; Goose and Claude use a model as the permission judge; Crush and
   OpenHands ship check-only updates. August's posture is not behind — it is a different
   and in several ways better position.
2. **August is behind on exactly one structural thing: real OS sandboxing.** Codex
   (Landlock/Seatbelt/MXC), Gemini (Seatbelt profiles, Docker, runsc, LXC), DeepSeek
   (bwrap/Landlock/Seatbelt/restricted-token) and omp (bwrap/reflink/overlayfs) all
   confine model-written shell at the kernel. August's AppContainer is opt-in and
   `workspace-write` does not even confine to the workspace on POSIX. This is the one
   "the references do it and it fits" item that is genuinely L-sized, and it is called out
   as a gap rather than smuggled into the top 15.
3. **The biggest single technique win is in compaction, not features.** DeepSeek's
   model-free prune + checkpoint-merge, and omp's superseded-read pruning and
   useless-result elision, are worth more to a long session than any UI change — and
   August's five compaction paths are already reconciled, so the change is local.

## What this review rejects

Nine proposals were considered and rejected, with reasons, in
`04-proposals.md` §"Proposals explicitly REJECTED": a model-as-permission-judge, Cordis
plugin ceremony, 5 compaction methods × 5 memory backends, shell-at-config-load,
model-authored memory with no review door, check-only updates, restoring a test suite, a
vector/graph store, and subagents writing memory. Two of those (the test suite, the vector
store) are the repo's own standing rulings and are listed so a future pass does not
re-open them.

## What could not be covered or verified

Stated plainly, because the brief asked for it:

- **No live run of the product.** The brief forbids burning paid API keys and prescribes
  mocks/local models; no turn was executed against a real provider. Every behavioural claim
  is VERIFIED-as-code, not VERIFIED-as-observed. The packaged NSIS update matrix — the one
  end-to-end check of the update path — has still never been run, and that is itself the
  second finding in the audit.
- **No device run of the mobile companion.** `frontend/mobile` was read statically.
- **Area 11's audit was completed directly**, not by the delegated subagent (it produced a
  draft skeleton and stalled). Everything in `01-august-audit.md` §11 and
  `audit-6-ci-observability.md` is first-hand. The gate set itself is stronger than the brief
  assumed; the real gap is behavioural detection (no packaged-install test, no live provider
  test).
- **`workbench.py` (7,683 lines) and `skill_service.py` (2,828)** were inventoried, not
  line-read; they are refactor candidates, not audited surfaces.
- **Hermes's Electron internals** (337 files) and **DeepSeek's ~55 remaining packages**
  were sampled, not exhausted — both are far larger than the 13 dimensions require.
- **OpenHands' agent server** lives in a separate repo (`OpenHands/software-agent-sdk`)
  that was not cloned; its memory, tools, compaction and delegation are unexamined, and the
  OpenHands cells in the comparison matrix say so rather than guessing.

## Files

| File | What |
|---|---|
| `00-summary.md` | this document |
| `01-august-audit.md` | per-area findings with file:line evidence, plus the docs-drift register |
| `02-harness-notes/*.md` | one per harness (13 files) |
| `03-comparison-matrix.md` | area × harness table, with August's position per row |
| `04-proposals.md` | every proposal: evidence, reference, change, effort, impact, risk, removals |
| `05-coverage-ledger.md` | every repo, every file read or skipped |
| `06-open-questions.md` | 24 decisions that need the owner |