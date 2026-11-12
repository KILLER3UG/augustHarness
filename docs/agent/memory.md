# Memory

Stored memory reaches a turn through **two automatic lanes** — the always-in
`kind='profile'` / `kind='preference'` block, and the
`new in memory since this chat opened:` delta — plus the boot memory index that
sits in the system prompt from session start. Everything else is **pull-only**:
the model searches the record with `brain_query` and pages it with `list_facts`.
There is no per-turn ranked tail and no `memoryAutoInject` setting; confusing the
automatic lanes with a search is the usual reason a "why did it not remember"
investigation goes wrong. The model-facing tools are `remember`, `list_facts`,
`forget` and `brain_query`; the store is the markdown record under
`<dataDir>/memory/`.

## The record is files

The SQL engine is retired. One `.md` file per fact (frontmatter over a
JSON-encoded body) plus a `MEMORY.md` index, both written and read by
`memory_store.mirror`; every CRUD door (`rest.save_fact` and friends) writes
the file directly, and every read door (search, BM25, the boot index,
`brain_query`, the Memory page) reads the files back. There is no second copy
to drift from. A file you delete is a forgotten memory; an edit you make is
the memory.

Scopes live in the same directory: a global fact's file stems from its key, a
`bot:<id>` row's file carries the scope prefix in the stem, because identity
is (scope, key). `MEMORY.md` lists the active, unexpired, non-sensitive,
non-person rows — the same exclusions the boot index applies. The record
itself holds every row.

## The transcript search index is derived

Transcripts (`<dataDir>/transcripts/*.jsonl`) are the record for chat history.
`state/transcript-index.sqlite` is a **rebuildable FTS5 index over them**, the
same relationship `MEMORY.md` has to `memory/`: it holds no fact that is not
derivable from the JSONL, so it is never a second copy to drift from. Three
consequences are load-bearing:

- **It can be deleted at any time** and the next boot rebuilds it
  (`transcript_index.sync()` runs from the lifespan).
- **It is schema-versioned and self-healing.** A file written by an older
  schema is dropped, not read with columns it does not have; a file that is
  not a database is discarded and rebuilt.
- **It is optional.** If `sqlite3` cannot load — Windows Smart App Control
  blocks `_sqlite3.pyd`, see `scripts/dev-app.mjs` — or anything about the
  index fails, `search_messages` falls back to the plain-Python scan and the
  product behaves exactly as it did before.

Why it exists at all: the SQL engine's `messages_fts` died with SQLite, and the
scan re-reads every transcript per query. That is fine at a dozen sessions and
stops being fine at the "weeks of sessions" scale the session-search docs
describe, because every keystroke in the search box pays for a full re-read.
The index turns that into an indexed lookup. The scan is kept as the reference
implementation, so the two can be compared and the fallback is always correct.

## The two automatic lanes, and the pull doors

**Always-in profile/preference lane** — `kind='profile'` and `kind='preference'`
facts are rendered first, inside a ~600-char budget, on every turn. There is no
keyword match and no setting to arm, which is what makes August remember *who you
are* without a coincidence — and it is the same two files Claude keeps in
context: `profile.md` and `preferences.md`. The `remember` prompt names both
kinds for exactly this reason; a fact that describes the user and lands as
`kind='fact'` is reachable only through an explicit search. The kind is also
editable by hand: the file IS the record, so `kind: profile` in the frontmatter
plus an index rebuild moves the fact into the lane. The lane keeps its existing
prompt heading; what changed is what feeds it, not what it is called. A fact
dropped for budget is **named** in a `recall partial:` line rather than
vanishing.

**The lane is only as good as the kind that feeds it.** A fact's lane is set by
the writer, and every writer that takes a rollback snapshot records the kind
with it — the snapshot IS the recovery source, so a snapshot without the kind
restores the fact as `kind='fact'` and drops it out of the lane. For the
one-time SQLite retirement the snapshots that survived were written before
that field existed, so the importer re-derives the lane from the CATEGORY the
Claude export assigned alongside it (`user` → profile, `feedback` →
preference); a category that maps to no lane stays a plain fact. Measured on
the live install before that: 19 recovered facts, ZERO profile, ZERO
preference — the always-in block rendered EMPTY on every turn, which is the
exact "August does not remember me" failure the lane exists to prevent.

**Delta** — `build_memory_delta_block` appends a plain `new in memory since this
chat opened:` section listing facts whose `updated` stamp is newer than the
session's own creation stamp — so a fact written by `remember` is visible on the
NEXT request without the model having to call `list_facts` first.

The delta is a **projection over the record, not a cursor and not a second
store**: an UPDATE to an existing key is delivered like a new write, a retired
row cannot resurface, keys the profile lane already showed are skipped, and an
unparseable session stamp returns NOTHING rather than dumping the whole store
into the tail. It rides the tail (stripped before persistence), so the cached
prefix is untouched; `memory_context_preview` deliberately omits it, because a
preview has no session.

**Everything else is pull-only.** Ranking is not attached to a turn: the model
runs the search itself through `brain_query(store='facts')`, and `list_facts`
pages the record with `offset` / `total` / `hasMore` (`include_retired` exposes
the rows consolidation parked). `retrieve_relevant_facts` has a single caller
now — `brain_query`, which passes `min_query_chars=1` so a deliberate search
answers even for `"gpu"`; the 8-character default is the floor the removed
per-turn path used to pass.

**Why the ranker no longer rides a turn.** BM25 survives inside explicit search
and the near-duplicate check that keeps `remember` from filing a second copy of
an existing fact — those are the two places a query exists at all. A ranker can
only match words the memory contains, and a model writes the words it chose, not
the words the user will ask with. That is not a tuning problem: measured on the
real 19-fact store, BM25 returned **nothing** for "what should I set my editor
theme to" against the fact that says "Prefers dark mode", and ranked the wrong
fact first for "what am I doing my degree in". The corpus is title + description
+ aliases + key + body, and `aliases` is a compensating mechanism inside search —
"Prefers dark mode" with aliases `[theme, appearance, editor theme]` answers
"what should I set my editor theme to", while the same fact without them returns
nothing — but a store that ships without them is the normal case, so the
per-turn tail was removed rather than tuned. The `remember` prompt asks for
aliases; a hand edit can add them to a file's frontmatter just the same.

Scopes are `global` and `bot:<agentId>` only; `project:<path>` is refused at
the write door on purpose, because workspace memory is the separate
`.aug/memory` markdown layer that the tail already carries.

## The boot index names what it does not carry

The boot index is the PRIMARY door now — it is in the system prompt from
session start, and the ranked tail is gone — so a fact that is missing from
it is memory the harness cannot see. It is bounded by the memory slice of the
Tier-1 index budget (2400 chars unscaled), and the bound is honoured two
ways that both matter:

- **The cut is at a LINE boundary.** A mid-line slice leaves half a hook
  that reads like a complete fact — the exact lie the index exists to avoid.
- **Every dropped fact is NAMED.** The receipt is
  `… N more in the record, not in this index: <keys>` — a count AND the keys,
  because a count alone leaves the model unable to ask for the missing one by
  name. Measured on the live 19-fact record before this: 5 facts (every
  identity fact among them) vanished silently and the index still claimed to
  be complete.

The receipt is what makes a SHORT index visibly short rather than quietly
incomplete.

## Model-managed memory (CRUD is a set)

`remember` (write/update by key), `list_facts` (enumerate keys) and `forget`
(retire/delete) are all **core** tools: they are in `HARNESS_CORE_TOOLS` so
progressive disclosure can never hide the key lookup that `remember`'s own
instructions depend on, and in `_BARE_TOOL_ALLOW` so a weak/downgraded model can
still correct what it remembers instead of only accumulating.

## Bringing a memory export in

Settings → Memory → Import has two doors. The file door parses one `.md`/`.json`
in the webview and previews it. The **whole-export door** hands the folder (or
`.zip`) another agent shipped to `POST /api/august/memory/import-claude`
(`memory_import.py`), which reads every file and writes into the record — never
a second store:

* `profile.md` lands as `kind='profile'`, `preferences.md` as `kind='preference'`
  — the two kinds the always-in lane reads, so an imported identity is recalled
  without winning a search. `areas/*` → `category='project'`,
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
cache, so `<memory_policy>` points the model at the `new in memory since this
chat opened:` delta and `list_facts` for the rest rather than trusting that index
alone — keep the prompt text and that behaviour in step if either changes.

Sub-agents read and recall but never write; the parent turn is the only
MODEL-facing memory write door (internal/UI routes also call `save_fact`).

**A write is a file write.** There is no read-before-write gate: Claude Code's
auto-memory does not have one, and August's own copy of it refused the first
write of every turn on a model that could not satisfy the JSON contract the
reviewer wanted — which is how `remember` shipped dead on the 1.7B turns. The
index is in context at session start, `new in memory since this chat opened:`
carries later writes, and `list_facts` pages the rest; the prompt asks the model
to check a key before adding a copy. A sentence already stored under a different
key is still refused with that key in the result (`existingKey`), because
"update, don't duplicate" needs a mechanism, not advice.

**A NEW memory may be checked, but the check cannot veto by failing.** With
`autoReview` on (default), `remember` — both scopes — launches one call to the
session's model with no conversation and a snapshot of the library: every skill,
the stored memory and the tool names. The reviewer answers `new`, `update`,
`merge` or `reject`. An update names the existing entry (returned as
`existingKey`); a skill or tool target is named instead of stored again; a merge
names the items to combine. Those three are vetoes, because they name something
real. An answer the reviewer could not produce — prose instead of JSON, a
timeout, no reviewer configured — is a missing opinion, not a refusal: the write
lands and the daily `memoryReview` pass still sees the row. Update writes (the
key already exists) skip the check. There is no similarity threshold: the
decision is the reviewer's, and the targets it names are validated against the
real library.

On the read side `list_facts` takes `offset` and reports `total`/`hasMore` — it
used to be hard capped at the 50 newest rows, so a store the model could write
to was one it could only partly read back — and `include_retired` exposes the
rows consolidation parked, which search skips but which are still stored memory.
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
