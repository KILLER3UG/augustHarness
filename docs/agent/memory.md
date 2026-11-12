# Memory

Stored memory reaches a turn through three lanes, and only one of them is a
keyword search. Confusing them is the usual reason a "why did it not remember"
investigation goes wrong. The model-facing tools are `remember`, `list_facts`,
`forget` and `brain_query`; the store is the markdown record under
`<dataDir>/memory/`.

## The record is files

The SQL engine is retired. One `.md` file per fact (frontmatter over a
JSON-encoded body) plus a `MEMORY.md` index, both written and read by
`memory_store.mirror`; every CRUD door (`rest.save_fact` and friends) writes
the file directly, and every read door (recall, BM25, the boot index,
`brain_query`, the Memory page) reads the files back. There is no second copy
to drift from. A file you delete is a forgotten memory; an edit you make is
the memory.

Scopes live in the same directory: a global fact's file stems from its key, a
`bot:<id>` row's file carries the scope prefix in the stem, because identity
is (scope, key). `MEMORY.md` lists the active, unexpired, non-sensitive,
non-person rows — the same exclusions the boot index applies. The record
itself holds every row.

## The three lanes

**Durable memory** — the `Relevant memory:` tail is BM25 over the record
(in-memory Python index over the parsed files), gated by brain-config
`memoryAutoInject` (**ARMED BY DEFAULT since 2026-10-24**; it was OFF, which
was a trust decision rather than a measured one — the write door that feeds
this lane, `memoryIsJunk`, refuses 0 of 14 durable rules and catches 5 of 5
junk cases, so the content is already filtered). An install that turned it off
keeps it off: verified through the real `PUT /api/brain/config` door, a
persisted `false` beats the new default.

Three things are true regardless of that gate:

- `kind='profile'` facts are an always-in lane (rendered first, ~600-char
  budget, injected even with auto-inject off — this is what makes August
  remember *who you are* without a keyword coincidence).
- A fact dropped for budget is **named** in a `recall partial:` line rather
  than vanishing.
- `build_memory_delta_block` appends a plain `new in memory since this chat
  opened:` section listing facts whose `updated` stamp is newer than the
  session's own creation stamp — so a fact written by `remember` is visible on
  the NEXT request without the model having to call `list_facts` first.

The delta is a **projection over the record, not a cursor and not a second
store**: an UPDATE to an existing key is delivered like a new write, a retired
row cannot resurface, keys the recall lane already showed are skipped, and an
unparseable session stamp returns NOTHING rather than dumping the whole store
into the tail. It rides the tail (stripped before persistence), so the cached
prefix is untouched; `memory_context_preview` deliberately omits it, because a
preview has no session.

Scopes are `global` and `bot:<agentId>` only; `project:<path>` is refused at
the write door on purpose, because workspace memory is the separate
`.aug/memory` markdown layer that the tail already carries.

## Model-managed memory (CRUD is a set)

`remember` (write/update by key), `list_facts` (enumerate keys) and `forget`
(retire/delete) are all **core** tools: they are in `HARNESS_CORE_TOOLS` so
progressive disclosure can never hide the key lookup that `remember`'s own
instructions depend on, and in `_BARE_TOOL_ALLOW` so a weak/downgraded model can
still correct what it remembers instead of only accumulating.
`retrieve_relevant_facts(min_query_chars=…)` splits the two callers: the
automatic tail keeps the 8-character floor (a 4-character message must not spend
context), while a deliberate `brain_query(store='facts')` search answers even for
`"gpu"`.

## Bringing a memory export in

Settings → Memory → Import has two doors. The file door parses one `.md`/`.json`
in the webview and previews it. The **whole-export door** hands the folder (or
`.zip`) another agent shipped to `POST /api/august/memory/import-claude`
(`memory_import.py`), which reads every file and writes into the record — never
a second store:

* `profile.md` lands as `kind='profile'`, `preferences.md` as `kind='preference'`
  — the two kinds the always-in-context lane reads, so an imported identity is
  recalled without winning a BM25 slot. `areas/*` → `category='project'`,
  `topics/*` → `category='reference'`.
* one bullet = one fact, keyed by the same slug `remember` derives, so re-importing
  the same export reports `unchanged` instead of restating rows;
* a near-duplicate under a **different** key is reported as folded into that key
  and not written (the store's own `DEDUPE_SIMILARITY` bar, not a new one);
* **two bars run before anything is written.** `memoryIsJunk` refuses task state,
  completed-work logs and fragments — except in the `profile`/`preference` lane,
  where "Goes by Sheesh" IS the memory and the under-24-char rule measured as a
  false positive. Then one batched review-model call (`_model_review`, 40 lines
  per call) answers `KEEP` or `DROP <why>` for every survivor, and the report
  names how many each bar removed. A line the reviewer never answered is kept and
  counted as `reviewMissing`: an outage must not block a migration, and it must
  not be reported as approval either.
* every write goes through `save_fact`, so the credential refusal still applies,
  and each landed row gets a rollback entry — the import is undoable per row.

`dry_run` reads the store exactly like the real run and writes nothing, so the
plan's numbers are the outcome's numbers. Fuzzy merges (paraphrases that miss the
similarity bar) stay with the AI-arrange door, which spends one model call to
decide add/update/delete.

The import is the only bulk door, and its judge batches lines per call; a single
`remember` write gets the fresh-context auto-review above instead. What is
already stored gets its model read from the standing review pass
(`memoryReview`, daily): one call across the durable corpus flags duplicates,
stale entries and retirements — those land as proposals, and a retirement is the
one memory action that still waits for a human, because it removes rather than
adds.

The boot memory index is **frozen per session** to protect the provider prefix
cache, so `<memory_policy>` tells the model to re-read `list_facts` after
writing rather than trusting that index — keep the prompt text and that
behaviour in step if either changes.

Sub-agents read and recall but never write; the parent turn is the only
MODEL-facing memory write door (internal/UI routes also call `save_fact`).

**That read is enforced, and the read is now complete.** `remember` refuses its
first write in a turn until `list_facts` (or `brain_query` over a memory store)
has run once that turn — only when the store actually holds something to check,
and never when `modelMemoryRead` is off, since a gate the model cannot satisfy
would make memory unwritable. A sentence already stored under a different key is
refused with that key in the result (`existingKey`), because "update, don't
duplicate" was advice, not a mechanism.

**Before a NEW memory is written, a fresh-context reviewer approves it.** With
`autoReview` on (default), `remember` — both scopes — launches one extra call to
the session's model with no conversation and a snapshot of the library: every
skill, the stored memory and the tool names. The reviewer answers `new`,
`update`, `merge` or `reject`. An update names the existing entry (returned as
`existingKey`); a skill or tool target is named instead of stored again; a
merge names the items to combine; an unavailable reviewer refuses the write
rather than approving it. Update writes (the key already exists) skip the gate.
There is no similarity threshold: the decision is the reviewer's, and the
targets it names are validated against the real library.

On the read side `list_facts` takes `offset` and reports `total`/`hasMore` — it
used to be hard capped at the 50 newest rows, so a store the model could write
to was one it could only partly read back — and `include_retired` exposes the
rows consolidation parked, which recall skips but which are still stored memory.
`brain_query` advertises the `memory` (KV notes) store for the same reason:
reachable-but-unadvertised is a door the model has to guess at.

## Serialization traps

A fact/KV value is **stored JSON-encoded** — in the record file's BODY (and in
`state/kv.json`'s `value` field) — so every read door must decode it or the
human browse list shows `“"a sentence"”` where the boot index shows the
sentence (and the edit field re-encodes it on every save) —
`memory_store.brain_browse` decodes a JSON *string* and leaves an object
verbatim, because unwrapping one would drop `details` on the next save.

## A dedup set may only outlive the bytes it dedups

The memory tail and the relevant-skills tail are **request-scoped**: they are
patched onto the turn's messages and `turn_close` strips the patched bytes at
the end of the turn. They are not written to the transcript. A "told once"
exclusion set that outlives the turn therefore suppresses re-telling content
that exists **nowhere** — not in the transcript, not in the next tail — so the
fact is simply gone until a compaction or a restart clears the set.

This shipped once (`_seen_memory_keys` / `_seen_skill_hints` on the session) and
was reverted: the dedup it carried was real, but its lifetime was wrong, and the
failure mode was silent memory loss rather than a wrong answer. The dedup that
survives is **per turn**, inside one build — the relevant-skills block and the
memory lanes dedup against each other within a single render, and nothing is
carried across turns.

**Why:** the transcript is the durable record. A set keyed on "what this session
was told" is only valid while those bytes are still in the context.

**How to apply:** if the re-telling cost is worth addressing again, key the dedup
on the *content identity* the build already has, and scope it to the turn by
construction — return it from the turn context, do not hang it off the session
object. A bare `set` attribute on the session is what invited the bug.

## Where the stores live

`dataDir()` has four homes; the content domain owns two and change nothing
about the other two:

| Home | Holds |
|---|---|
| `memory/` | the fact record (one `.md` per fact + `MEMORY.md`) |
| `state/` | kv, internal state, proposals, exams, blackboard, episodic timeline, episodes, goals, config audit, refine store, daemons, recurring tasks, workstreams |
| `telemetry/` | rotating JSONL ledgers (turn outcomes, usage, lifecycle, decision ledger, routing evidence, guardrail log, sub-agent runs) + fingerprint/topic JSON |
| `transcripts/` | `<sessionId>.jsonl` per chat + `sessions.json` |

`memory/`, `state/`, `telemetry/` and `transcripts/` are all in
`hardline._AGENT_STATE_DIRS`, so `write_file`, the terminal and code mode refuse
writes into them: a model that could write the record could install
instruction-shaped content beside the facts a person reads to audit it. The
sanctioned writers are the memory_store doors, which are not behind that tool
door. The project layer (`<workspace>/.aug/memory`) is a different path and
stays model-writable by design. Settings → Memory → "Open folder" opens the
record through the backend so the frontend never learns the data dir.

The one-time import from the retired SQL database lives in
`app/services/retired_sqlite.py`: on the first boot after the engine swap it
exports every table with rows to the home above, recovers every memory an
`available` rollback-log entry still names, writes the
`state/sqlite-imported.json` marker, and renames the database (with `-wal` and
`-shm`) to `august_brain.sqlite.retired` — never deleted. The owner's file is
never written: it is opened READ-ONLY when no WAL is pending, and when `-wal`
is non-empty — the normal state after the packaged `taskkill /T /F` quit — the
database and its sidecars are copied to a temp dir and the COPY is opened
read-write so the WAL replays, then the copy is deleted. A failed import writes
a failed marker and leaves everything untouched.

## Backups and restore

**Memory survives independently of recall** — `app/services/brain_backup.py`
takes a verified `tar.gz` of `memory/` + `state/` + `config.json` with a
SHA-256 manifest, re-opens every archive and verifies every file hash before
trusting it (the retired `PRAGMA integrity_check`, in the archive's own
terms), and prunes to a verified rolling set of 5; `GET /api/brain/integrity`
is the health read. Settings → Memory shows the backups card. A restore is
staged through `POST /api/brain/backups/restore` and applied at the next
launch.

**A restore is staged and applied at the next launch, never in place:**
store threads hold the record files open for atomic writes, so an in-process
swap yields half-old reads. `apply_pending_restore()` therefore runs in
`main.py` before any store opens, extracts to a staging directory, and swaps
`memory/` / `state/` / `config.json` one entry at a time, keeping what it
replaced as `<name>.pre-restore`; the pending marker is a file, not a row,
because the case needing a restore is one where the record may not open.
Legacy SQL-era copies (`.sqlite`) still list — they hold user data — but
restore is refused with the retired-engine reason. Do not add an in-process
swap.

## The memory-user experience

The current user-facing surfaces (Settings → Memory list, Library, save chip,
proposals, backups) and the open UX work are in
[../specs/memory-and-skills.md](../specs/memory-and-skills.md). This file is the
rule set; that one is the product state.
