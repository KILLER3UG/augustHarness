---
name: research-paper-studio
description: "Draft and revise a full research paper (Chapter 1-5) inside one per-paper workspace, with every citation resolved to a real record and a traceability matrix tying objectives to conclusions. Use when the user starts, continues, or audits a thesis/paper, asks for literature synthesis, chapter drafting, citation checks, docx export, or defense prep; do not use it for casual writing that is not a structured academic paper."
category: research
version: 0.1.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [research, writing, citations, thesis, traceability]
    related_skills: [independent-review, pdf]
---

# Research Paper Studio

A structured academic paper is a long-running job, not a one-shot generation:
it outlives a single context window, its claims must trace to real sources, and
its chapters depend on each other. This skill runs that job inside one
per-paper workspace folder, drafting chapter by chapter from a state file so a
small local model can keep the whole paper coherent without holding every word
in context.

It does **not** invent sources, data, or results, and it does not bypass the
school's AI-use policy — see the integrity rules, which are hard rules and not
suggestions.

## When to Use

- The user starts a new thesis/paper and needs the workspace and chapter
  skeleton set up.
- The user asks to search for literature, add sources to the library, or
  synthesize prior work.
- The user asks to draft, revise, or check a specific chapter.
- The user asks whether every citation is real, or to export to `.docx`, or to
  import an adviser's comments.
- The user is preparing for a defense and wants slides or practice questions.

## When NOT to Use

- The writing is casual, or not a structured academic paper.
- The user only wants a quick summary of one file; a plain read answers it.
- The paper's discipline needs a structure this skill does not define and the
  user has not supplied one — ask for their school's template instead of
  inventing chapters.

## The per-paper workspace

One folder per paper, always this shape, so any chapter can be continued from
disk alone:

```
<paper-workspace>/
  paper-state.md      # topic, objectives, school structure, citation style, chapter status
  sources/            # one record per source: summary + resolved citation
  drafts/
    chapter1.md … chapter5.md
    abstract.md references.md appendices.md
    provenance/       # sidecar per chapter: what the agent drafted vs. what the user wrote
  traceability.md     # objectives -> methods -> results -> conclusions
  exports/            # formatted .docx and imported adviser comments
```

`paper-state.md` is the source of truth for *where the paper is*. Every drafting
session reads it first and updates it last.

## Default chapter structure

Configurable per school; APA 7 is the default citation style.

- Chapter 1 — Introduction
- Chapter 2 — Review of Related Literature and framework
- Chapter 3 — Methodology
- Chapter 4 — Results and Discussion
- Chapter 5 — Summary, Conclusions, and Recommendations
- Plus: abstract, references, appendices

## Procedure

1. **Initialize the workspace.** Create the folder layout above and write
   `paper-state.md`: topic, research questions/objectives, the school's
   structure and citation style, and a status line per chapter. Do not draft
   prose yet. *Done when `paper-state.md` exists and names every chapter file.*

2. **Build the source library.** For each relevant work: search with
   `web_search`, read it with `web_fetch`, then write one record under
   `sources/` containing the summary, the resolved citation, and the exact
   identifier used to verify it. *Done when every file in `sources/` carries a
   citation block with a real identifier.*

3. **Resolve every citation.** Each citation must resolve to a real record —
   title, authors, year, and a DOI or URL. Verify it with `verify_citation`,
   which resolves against free sources in this order: Crossref, then OpenAlex,
   then arXiv. A citation that comes back unverified is flagged and **never
   cited**. Resolve at claim time, not from memory of the reference. After
   drafting, run `check_paper_citations` over the workspace to audit the whole
   set at once. *Done when `check_paper_citations` reports no unverified
   citation in any draft.*

4. **Draft the traceability matrix.** Link each Chapter 1 objective to the
   Chapter 3 method that addresses it, the Chapter 4 result that answers it,
   and the Chapter 5 conclusion that states it. Write it as a markdown table
   in `traceability.md`, one row per objective, then run `check_traceability`
   over the workspace and fix what it flags. *Done when `check_traceability`
   reports no empty link and no row that has drifted from the chapters.*

5. **Draft chapter by chapter.** Write one chapter at a time from
   `paper-state.md` plus the relevant `sources/` records — never the whole
   paper in one pass. After each chapter, write its provenance sidecar and
   update the chapter status. *Done when the chapter's status line is updated
   and its sidecar exists.*

6. **Export and import feedback.** Export the formatted `.docx` to `exports/`
   (v1 target; LaTeX is out of scope). When the adviser returns comments,
   import them and fold each one into the chapter or the traceability matrix.
   *Done when the exported file matches the current drafts.*

7. **Prepare the defense.** Build slides from the traceability matrix and run
   practice questions against the chapter content. *Done when every objective
   has at least one anticipated question.*

## Integrity rules

These are hard rules. A draft that violates one is wrong, not stylistically
imperfect.

1. **Never fabricate a reference.** Every citation resolves to a real record
   via Crossref → OpenAlex → arXiv. An unverifiable citation is flagged to the
   user, not quietly kept.
2. **Never invent data, results, or survey responses.** Chapter 4 comes only
   from data that was actually collected or experiments that were actually run.
3. **Write original text.** Keep the per-chapter provenance sidecar recording
   what the agent drafted versus what the user wrote, and remind the user to
   follow their school's AI-use policy.

## Pitfalls

- **Drafting the whole paper in one turn.** Long drafting truncates on small
  local contexts; chapter-by-chapter from `paper-state.md` is what keeps the
  paper coherent.
- **Citing from the abstract.** If only the abstract was read, the summary says
  so; do not attribute findings the body was never checked for.
- **Trusting a remembered reference.** A citation the model recalls from memory
  is unverified until it resolves against Crossref, OpenAlex, or arXiv.
- **Letting the matrix drift.** A chapter rewritten without updating
  `traceability.md` silently breaks the objectives-to-conclusions chain.

## Verification

- `check_paper_citations` over the workspace reports no unverified citation;
  every citation in `drafts/` and the references list resolves to a real
  record, and any that does not is flagged to the user.
- `check_traceability` over the workspace reports no empty link and no row
  that has drifted from the chapter drafts.
- Every chapter file has a provenance sidecar.
- The exported `.docx` matches the current chapter drafts.
