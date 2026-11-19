# Audit — memory/recall + skills/self-evolution

Repo: C:/Dev/augustHarness @ 9804b069 (master), audited 2026-11-14. Read-only static review of
area 5 (memory + recall) and area 6 (skills + self-evolution). Every citation is a file:line I
opened and read; nothing is quoted from docs alone.

## Verdict

The memory and skills domains are in unusually good shape for a no-test-suite repo: the
one-store rule is real (files are the record, `mirror.py` is the only writer, the write fences
keep models out), the "honest budget" pattern (line-boundary cuts plus named omission receipts)
is applied consistently across the boot index and both lanes, and every autonomous-write gate
has an explicit, documented fail-open/fail-closed decision. The defects found are mostly at the
seams between doors rather than inside them: the boot index's omission receipt mis-counts when
sensitive rows sit in the newest-40 slice, `save_fact`'s CAS guard releases the record lock
before the file write (the documented guarantee is not actually enforced), the M-7 "disuse
penalty" reads a counter nothing has written since the per-turn ranked tail was removed (dead
machinery — a clean removal), and four write doors edit fact files without refreshing the
`MEMORY.md` index that the other doors maintain. Nothing found threatens the record itself; the
work is coherence cleanup, not rescue.

## Findings (ordered by severity)

### [MED] Boot-index omission receipt under-counts (or vanishes) when sensitive rows are inside the newest-40 slice

- **What I saw (VERIFIED)** — `backend-py/app/services/memory_store/brain.py:813-877`.
  `brain_index_snippet` slices `factRows` to 40 *before* the per-row filters: the render loop
  skips `sensitive` rows, `subject_type == 'person'` rows (brain.py:829) and rows tripping
  `sensitiveMemoryReason` (brain.py:831-835), but the "+N more" receipt is computed as
  `total > len(factRows)` where `total` (brain.py:866-873) re-counts visible∧category∧¬sensitive∧¬person
  rows **without** the `sensitiveMemoryReason` content check, and `len(factRows)` is the
  pre-filter 40-row slice that still contains the skipped rows.
  - 40 visible facts, 5 of which trip the content scanner: 35 lines render, `total=40`,
    `40 > 40` is false → **no receipt at all**, 5 facts silently absent from the primary door.
  - 50 visible facts, 5 sensitive-flagged inside the newest 40: receipt says "+2 more" while 7
    non-sensitive facts are actually missing.
- **Why it matters** — the receipt exists precisely so a short index is "visibly short instead of
  quietly incomplete" (brain.py:940-950). This is the same silent-omission failure mode
  resurfacing through the filter path. A model reading the index cannot ask for facts it was
  never told exist.
- **What good looks like** — count the receipt against `len(fact_lines)` (what actually
  rendered), and make the `total` recount apply the same `sensitiveMemoryReason` predicate the
  render loop applies. Then `total - emitted` is the true omission count in both directions.

### [MED] `save_fact`'s version guard releases the lock before the write — the CAS guarantee is not enforced

- **What I saw (VERIFIED)** — `backend-py/app/services/memory_store/rest.py:415-517`. The
  `if_updated_at` compare-and-swap check runs inside `with _record_lock():` (rest.py:415,
  417-439), and the docstring says "The guard belongs INSIDE the write… The lock above closes
  it" (rest.py:424-427). But the write itself, `record.write_fact_file(row)` at rest.py:517, is
  **outside** the `with` block. `_record_lock()` is an in-process `threading.RLock`
  (`memory_store/tables.py:50-57`). Two threads can both read the same on-disk `updated_at`
  inside the lock, both pass the CAS check, and then both write after releasing it — last
  writer wins and the first update is silently lost despite `if_updated_at`.
- **Why it matters** — `remember`'s merge-and-retry contract ("a stale `if_version` returns
  `{ok:false, conflict:'version', current}`", spec §2) rests on this guard. `update_fact_value`
  (rest.py:684-696) and `touch_fact_usage` (rest.py:544-551) do write inside the lock;
  `save_fact` — the door with the most callers — is the exception.
- **What good looks like** — move `record.write_fact_file(row)` (and ideally the index
  invalidation) inside the `with _record_lock():` block, matching the other writers. One indent.

### [MED] The M-7 "disuse penalty" is dead machinery: nothing writes `injected_count` anymore

- **What I saw (VERIFIED)** — `fact_retrieval.py:404-429` (`_disuse_for`) reads
  `injected_count`; `fact_retrieval.py:555-583` applies the penalty in
  `retrieve_relevant_facts`; `fact_retrieval.py:432-456` (`_disuse_constants`) reads three
  config knobs (`memoryDisuseWeight/Cap/Rescue`). The only writer ever was `_set_injected`
  (`rest.py:559-562`), which fed the per-turn ranked tail that was removed. A repo-wide grep
  finds no caller of `_set_injected` and no other writer of `injected_count` (only the
  retired-SQLite importer defaulting it to 0, `retired_sqlite.py:423,552`). Every row therefore
  scores `unused = max(0, 0 - use_count*rescue) = 0` → penalty always 0. `_set_injected` is
  unreachable code.
- **Why it matters** — ~90 lines of scoring logic, three config knobs, and two wire columns
  simulate a signal that can never exist. The next auditor (or model) reads "M-7 disuse penalty"
  as an active ranking force; it is not. This is exactly the "guard nothing can trip" the
  repo's own audit rules call dead weight.
- **What good looks like** — remove `_disuse_for`/`_disuse_constants`/the penalty block and
  `_set_injected`, or repurpose them only if a ranked surface returns. Keep the
  `injected_count`/`last_injected_at` columns (wire shape/UI compatibility) but stop pretending
  they feed ranking. **Removal candidate #1.**

### [LOW→MED] Four write doors edit the record without rewriting the `MEMORY.md` index

- **What I saw (VERIFIED, by absence)** — `save_fact` (rest.py:526), `delete_fact`
  (rest.py:847), `_merge_duplicates` (consolidation.py:870) and `_supersede_contradictions`
  (consolidation.py:1010) all refresh the markdown index. These do not:
  - `brain_update_row` (Settings row edit) — brain.py:1277 writes the file, no
    `note_fact_changed`/`rewrite_index` call;
  - `apply_retire_decision` — consolidation.py:689-691 flips status to `retired`, writes the
    file, invalidates the BM25 cache (702-707), never rewrites the index — so `MEMORY.md` keeps
    listing a retired fact (its `indexable()` test, mirror.py:427-438, would now exclude it);
  - `update_fact_value` — rest.py:696-703, same gap;
  - `restore_superseded_losers` — rest.py:766-778, a restored row becomes active but stays
    unlisted until some unrelated write triggers a rebuild.
- **Why it matters** — `MEMORY.md` is the human's audit mirror ("Open folder" is a shipped
  surface). Stale entries cut both ways: a retired row still listed reads as active; an
  edited/renamed title shows the old text; a restored row is invisible. Self-healing only on
  the next unrelated `save_fact`/consolidation pass.
- **What good looks like** — one rule, one helper: every door that calls
  `record.write_fact_file` for a facts row also calls `record.note_fact_changed(key)`
  (best-effort, never raises — the helper already has that contract, mirror.py:495-505).

### [LOW] Delta lane cuts to 8 facts *before* the sensitivity filter

- **What I saw (VERIFIED)** — `fact_retrieval.py:863` slices `candidates[:_DELTA_MAX_FACTS]`
  (8); the render loop then skips rows failing `sensitive`/`sensitiveMemoryReason`
  (fact_retrieval.py:872-877) without backfilling from candidates 9+. If the 8 newest
  post-session-open facts all trip the filters, the delta renders empty although qualifying
  writes exist just past the cut.
- **Why it matters** — a burst of sensitive-flagged writes can hide a legitimate new fact from
  the next turn, and nothing names the omission (the delta has no receipt).
- **What good looks like** — filter first, then take the newest 8 (the profile lane already
  filters inside `_load_index` before ranking — same order here).

### [LOW] `list_facts` `hasMore` arithmetic mixes project entries into the store total

- **What I saw (VERIFIED)** — `tool_registrations/session_tools.py:1362-1371`:
  `total = store_total + projectCount`, but `hasMore = off + len(rows) < total` where `rows`
  counts only facts-store rows, and project entries are prepended only on page 0 without
  consuming offset. With `facts == limit` and 3 project entries, page 0 reports
  `hasMore: true`; page 1 returns an empty page (project entries already shown).
- **Why it matters** — one wasted round-trip and a slightly dishonest paging contract for the
  model's audit reads. Cosmetic, but this endpoint's whole point is honest enumerability.
- **What good looks like** — report `storeTotal`/`projectTotal` (already present) and compute
  `hasMore` against `storeTotal` alone, stating that project entries ride page 0.

### [LOW] Auto-review's 60-second timeout sits on the synchronous write path

- **What I saw (VERIFIED)** — `auto_review.py:42` (`_REVIEW_TIMEOUT_S = 60`) and
  `auto_review.py:537` (`await asyncio.wait_for(client(prompt), timeout=…)`); called from
  `_remember` (session_tools.py:815-818) and from the `skill_create` door
  (harness_tools.py:50) before anything is filed. A slow/unresponsive reviewer model stalls the
  save-chip write for up to a minute.
- **Why it matters** — the fail-open design is right (unavailable = write lands, logged once,
  session_tools.py:440-458), but the latency cost is paid on every NEW memory even when the
  reviewer is merely slow. A 1.7B local model answers fast; a remote provider outage turns each
  first-save into a 60s hang.
- **What good looks like** — a tighter default (e.g. 15-20s), or file-first-then-review with a
  post-hoc `reject` → rollback entry (the rollback store already makes every write undoable).
  Owner's call; the current behavior is safe, just slow under failure.

### [LOW] `_siblingFiles` enumerates a skill directory without the walker's caps

- **What I saw (VERIFIED)** — `tool_registrations/skill_tools.py:199-226` uses
  `dirPath.rglob('*')` with no depth/file cap (only the *display* is capped at 25,
  `_MAX_SKILL_FILES`, skill_tools.py:144,213). Discovery's `_iterSkillMd` is deliberately
  depth-4/400-file bounded (skill_service.py:329-377).
- **Why it matters** — a pathological skill directory (vendored tree, node_modules dropped
  inside) makes every `load_skill` L1 call enumerate it all. Display is bounded; the walk is
  not. It does not descend symlinked dirs (pathlib default), so no escape.
- **What good looks like** — reuse a bounded walk (or cap `entries` at a few hundred) so the
  listing cost is bounded like everything else in discovery.

### [LOW] Stale comment: description cap says 500, code says 1024

- **What I saw (VERIFIED)** — `skill_service.py:410-418`: the comment explains "500 chars is
  room for…" directly above `_DESCRIPTIONMax = 1024`.
- **Why it matters** — a reader tuning the index budget from the comment will compute the wrong
  number; stale numeric comments are how the next audit gets written wrong.
- **What good looks like** — fix the comment (or make the constant reference the budget math it
  derives from).

### [LOW] `_deriveFactKey` carries a dead `scope` parameter, self-documented for removal

- **What I saw (VERIFIED)** — `tool_registrations/session_tools.py:159-174`: the docstring says
  "The `scope` argument is kept for call-site compatibility and is now unused; remove it with
  its three callers."
- **Why it matters** — the repo already ruled on this; it is an open TODO in code form.
- **What good looks like** — do the removal. **Removal candidate #2.**

### [LOW] Legacy usage-sidecar migration code is permanent-looking

- **What I saw (VERIFIED)** — `skill_service.py:2586-2668` (`_legacy_usage_files`,
  `_migrate_legacy_usage`) relocates pre-fix `.usage.json` files on every read/write, forever.
- **Why it matters** — it is migration code with no sunset; each `record_skill_use` pays the
  legacy scan. Fine today; it will read as load-bearing ritual in a year.
- **What good looks like** — either time-box it (log a "legacy sidecar seen" count; remove when
  a release cycle reports zero) or document the sunset in the docstring. **Removal candidate #3
  (deferred, not immediate).**

## Strengths worth keeping

- **The one-store rule is enforced in code, not just prose.** `mirror.py` is the only facts
  writer; `load_facts`/`write_fact_file`/`rebuild_mirror` (mirror.py:395-539) are the whole
  record; `rebuild_mirror` never deletes an unparseable file (mirror.py:516-535); the record
  dir sits in the model-write fence. The transcript FTS5 index follows the same discipline:
  schema-versioned, dropped-not-repaired on corruption, synced before every query, and it
  returns `None` (→ scan fallback) for sub-trigram terms so the two paths can never disagree
  (transcript_index.py:46, 373-438).
- **Honest budgets everywhere.** Boot index cuts at line boundaries and names dropped keys
  (brain.py:958-1024); the profile lane repacks to reserve room for its own receipt and lets
  the receipt win over tidiness (fact_retrieval.py:766-788); the skills catalogue records
  overflow instead of silently truncating (capabilities_prompt.py:190-213).
- **Fail-open vs fail-closed is a decision, not an accident.** Memory auto-review: unavailable
  = write lands + once-per-class warning (session_tools.py:379-397, 440-458). Skill create:
  unavailable = files nothing (harness_tools.py:77-82). Fabricated reviewer targets fail closed
  so a hallucinated name cannot redirect a write (auto_review.py:369-378).
- **The rails are an allow-list with self-naming refusals** (harness_rails.py:17-25, 46, 69-85),
  evidence must trace to the user's own episodes (387-417), support files ride the same content
  scan as the body (437-471), and probation can only revert its own still-newest snapshot
  (526-556).
- **The applier refuses the traps it used to fall into**: create-over-existing (harness_self_improve.py:694-703),
  bundled-shadowing (707-717), body-less patches that would render placeholder prose (653-656),
  and it carries forward `trigger`/`supersedes`/`origin`/unknown frontmatter so a patch cannot
  silently retire a skill from recall (738-793).
- **Retirement is propose-only with claim-before-write and stale-evidence recheck**
  (consolidation.py:446-570, 573-708) — a proposal decides once and re-verifies the evidence
  before flipping a row.
- **Scope union (`global ∪ bot:<id>`) is one predicate** (`_visible`, rest.py:111-139) applied
  at every read door — corpus, boot index, browse, forget — so the doors cannot drift.
- **`list_facts`/`session_search` are genuinely enumerable** (`offset`/`total`/`hasMore`/
  `include_retired`, session_tools.py:1232-1375) — a store the model can write to is one it can
  fully read back.
- **Subagents read-but-never-write is enforced as a deny set over the whole memory CRUD surface**
  (`remember`, `forget`, `list_facts` — workbench/subagent.py:94-99), plus a separate automation/
  cron read-only gate (session_tools.py:935-973).
- **The import door spends model judgment honestly**: junk bar with a profile/preference
  exception, one batched KEEP/DROP judge, `reviewMissing` counted separately from approval, and
  per-row rollback snapshots that carry `kind`/`category` (memory_import.py:250-306, 360-461).

## Coverage ledger

Files opened and read (full or targeted):

| File | Read |
|---|---|
| backend-py/app/services/memory_store/brain.py | full (1324 ln) |
| backend-py/app/services/memory_store/mirror.py | full |
| backend-py/app/services/memory_store/fact_retrieval.py | full |
| backend-py/app/services/memory_store/rest.py | full |
| backend-py/app/services/memory_store/consolidation.py | header + retire/apply + merge/supersede (1-130, 440-1011) |
| backend-py/app/services/memory_store/transcript_index.py | full |
| backend-py/app/services/memory_store/kv.py | structure + key fns |
| backend-py/app/services/memory_store/tables.py | `store_lock` + homes |
| backend-py/app/services/tool_registrations/session_tools.py | memory half (1-1390) |
| backend-py/app/services/tool_registrations/skill_tools.py | full |
| backend-py/app/services/tool_registrations/harness_tools.py | full |
| backend-py/app/services/auto_review.py | full |
| backend-py/app/services/skill_service.py | discovery/parse/create/patch/trash/usage (122-421, 580-759, 1349-1468, 2072-2828) |
| backend-py/app/services/skill_distiller.py | review pass + cursors (1569-1985) |
| backend-py/app/services/harness_rails.py | full |
| backend-py/app/services/harness_self_improve.py | decide + `_apply_skill_write` (430-794) |
| backend-py/app/services/memory_import.py | 231-470 |
| backend-py/app/services/retired_sqlite.py | structure + WAL staging |
| backend-py/app/services/brain_backup.py | structure + staged restore |
| backend-py/app/services/skill_packs.py | structure + preview/scan/install |
| backend-py/app/services/learning_scheduler.py | structure + review job |
| backend-py/app/services/harness_promote.py | `derived_from` write site |
| backend-py/app/services/workbench/workbench.py | boot-index freeze (1010-1084) + tail assembly (3355-3484) |
| backend-py/app/services/workbench/subagent.py | deny set (94-99) |
| backend-py/app/services/session_scope.py | scope vocabulary |
| backend-py/app/services/capabilities_prompt.py | index budget + alias pre-read structure |
| backend-py/app/services/brain_config_service.py | defaults (memoryReview, autoReview, projectSkills, skillAutonomy, memoryIndexCategories) |
| docs/specs/memory-and-skills.md, docs/agent/memory.md, docs/agent/skills.md | full (spec baseline) |
| frontend/desktop/src/sections/settings/MemorySection.tsx + ImportMemoryDialog.tsx, components/chat/MemorySaveChip.tsx | existence verified (not line-read) |
| package.json + scripts/gates.mjs + backend-py/scripts/check_skills.py | `check:skills` wiring verified |

Not read in detail (with reason):

- `memory_store/messages.py`, `sessions.py`, `transcript_blocks.py`, `wire.py`, `timeutil.py` —
  support serialization/session helpers on the audit's periphery; their behavior was spot-checked
  through their callers. Low risk, but uncovered.
- `skill_versions.py` (192 ln) and `memory_quality.py` (226 ln) — snapshot store and dedupe
  ladder; only their call sites verified.
- `episode_miner.py` (1257 ln) — structure only; the review pass and rails were the depth
  targets.
- Skill-distiller `apply_verdict` body (1178-1568) — the verdict→write plumbing; the memory bar
  and cursor logic were read, the per-action apply branches were not.
- Frontend Memory/Skills UI internals — presence and routing verified; UX flows not walked
  (static backend audit).
- Routers (`routers/august.py`, `brain_config.py`) — only the memory/skill door call sites.

## Open questions for the owner

1. **Boot-index receipt fix (finding 1)** — should the `total` recount also apply the
   `sensitiveMemoryReason` content filter, so the receipt counts exactly what the render
   skipped? (One-line alignment; changes receipt numbers only.)
2. **Disuse machinery (finding 3)** — remove now, or keep until a ranked memory surface
   returns? The columns and config keys can stay either way.
3. **Auto-review latency (finding 7)** — is a 60s synchronous reviewer acceptable on the save
   path, or should the default timeout drop / the review move behind the write?
4. **Subagent memory reads** — the deny set blocks `list_facts` too (spec §5 records this as a
   product ruling "stricter than read-only"). Still wanted?
5. **`MEMORY.md` rewrite discipline (finding 4)** — happy to standardize on
   "every facts-row write calls `note_fact_changed`"; confirm no door deliberately skips it.
6. **Save-chip usage** — still unmeasured per spec §6.7; remove-or-keep decision still open.
