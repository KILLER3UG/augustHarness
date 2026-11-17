---
name: programmer-pack
description: "Drive a coding job end to end: repo recon, a todo plan, implementation with background subagents for independent lanes, real test/build runs, and a clean-context diff review before calling the work done. Use when the user asks to build, fix, refactor, or review code in a repository; do not use it for one-line code questions a plain read answers."
category: software-development
version: 0.1.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [coding, refactoring, testing, subagents, review]
    related_skills: [independent-review, research-paper-studio]
---

# Programmer pack

A coding job is a loop the agent runs, not a surface the user operates: the
user asks, steers, and approves risky actions; the agent reconnoiters, plans,
implements, verifies, and reviews. The transcript carries the receipts — the
todo list, the subagent lanes, the test output, the review verdict — so the
user can view and undo without a control panel for every step.

It does not invent files, symbols, APIs, or test results, and it never calls
work done on a claim: green is something the tools reported, not something the
agent asserts.

## When to Use

- The user asks to build a feature, fix a bug, refactor, or review a change in
  a repository.
- The work spans several files or has independent parallel lanes.
- The user wants tests run, a diff reviewed, or a change verified before
  merging.

## When NOT to Use

- A one-line question about a file a plain read answers.
- The "change" is a sentence in chat with no repository behind it.
- The user asked only for an opinion — do not start editing.

## Procedure

1. **Recon before touching anything.** Summarize the target area with
   `module_context` (purpose, exported names, imports, imported-by), then
   `list_directory` and `search_files` to map the neighborhood, then
   `read_files` for the few files the change must touch. Trace every symbol to
   its definition and usages; never invent a file, symbol, API, or import.
   Check the project manifest for the real dependency versions before
   assuming a library exists.
2. **Plan in the open.** Write the steps with `update_todos` and keep them
   current as work progresses — one todo per verifiable step, ordered by
   risk. Flag the risky ones (data loss, auth, public interfaces, anything
   that needs the user's approval) and ask before crossing that line.
3. **Implement small.** Prefer `apply_patch` for targeted edits and
   `write_file` / `write_files` for full-file rewrites; match the file's
   existing style. Keep each step small enough that its verification is
   obvious.
4. **Fan out only independent lanes.** Use `spawn_subagents` when the work
   partitions cleanly (per-file migrations, independent test suites, research
   vs. implementation) — each work item states its files and its done
   condition, and lanes coordinate through `shared_write` / `shared_read`.
   Never point two subagents at the same files. Dependent work stays
   sequential with you; a subagent is a worker for an independent item, not a
   way to parallelize a chain. Background completions arrive on their own —
   check them with `list_background_tasks` / `read_background_task` and fold
   the results in.
5. **Verify by running.** Run the relevant tests, linters, and builds with
   `run_command` (or `run_commands` for a fixed sequence) and read the real
   output. A failure is information: fix the root cause, not the symptom, and
   check sibling call paths for the same flaw. Stop after about three failed
   fix attempts on the same file and ask the user instead of looping.
6. **Review the diff before calling it done.** Collect the real diff
   (`git diff` through `run_command`) and run `review_change` with the
   requirement, the diff, and the actual test/build evidence — its findings
   are advisory and are not applied automatically; address them or answer
   them. For a planned multi-step job, `step_review` records a verdict per
   completed step.
7. **Report the truth.** What changed, what was run and what it returned,
   what is verified, what is left, and anything surprising. Scratch files the
   job created are deleted before the report; nothing is committed or pushed
   unless the user asked.

## Integrity rules

These are hard rules. A change that violates one is wrong, not stylistically
imperfect.

1. **Green is observed, not asserted.** Every "tests pass" cites a run this
   session actually made, with its exit status. A suite that was not run is
   not green.
2. **Never weaken a guard to go green.** A test, type check, or lint that
   fails is a finding. Relaxing it to make the run pass is a defect — fix the
   code the check is protecting, or get the owner's explicit ruling.
3. **The diff is the deliverable.** Every claim in the final report must be
   visible in the diff or in a tool result from this session.
4. **The user owns the repository.** No commits, pushes, force-pushes, or
   history rewrites unless the user asked for that exact action; stage by
   path, never wholesale.

## Pitfalls

- **Recon skipped.** Editing the first file that mentions the symptom breaks
  a neighbor; `module_context` first is cheaper than a wrong patch.
- **Subagents on shared files.** Two lanes editing one file race and both
  reports look successful; partition lanes by file ownership, not by task
  mood.
- **Verifying with the wrong command.** A build passing is not tests passing;
  run the gate the change actually claims to satisfy.
- **Scope creep by refactor.** Drive-by renames and reformatting bury the
  change; touch only what the task needs.
- **Silent background lanes.** A subagent that finished while other work ran
  is easy to forget; reconcile every lane before the final report.

## Verification

- The todo list shows every step completed and every claimed run's evidence
  is in the transcript.
- `review_change` (or `step_review` per step) ran on the final diff and its
  findings are addressed or answered.
- The final report names files changed, commands run with their real
  results, and anything deliberately left undone.
