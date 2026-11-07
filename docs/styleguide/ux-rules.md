# August UX rules

Behavioral rules for every UI change. These extend [PRODUCT.md](../PRODUCT.md)'s
principles ("Honest surfaces") into the specifics a reviewer checks. The
mechanical subset is enforced by `check:design` (`copy-filler`,
`copy-baked-keys`); the rest is review discipline.

## UI copy must not overclaim

Never imply August took an action, made a decision, or observed a fact
without real state behind it. Use neutral process language while pending
("Sending…", "Running…"); reserve result verbs ("skipped", "protected",
"found", "verified", "deleted") for actual results. Copy is concise,
specific, and free of filler ("please", "simply", "just") — state the
action directly.

- An unknown is never rendered as a zero: "0 runs" for a failed read
  answers a question nobody asked. Say the read failed and what still works.
- A degraded mode says exactly what is and is not happening ("changes may
  not save until restart"), never just "error".

## Match in-flight feedback to duration

| Duration | Feedback |
|---|---|
| 0–100 ms | none — anything visible reads as a glitch |
| 100 ms–1 s | disabled state only |
| 1–3 s | disabled + spinner or label swap |
| 3 s+ / multi-step | stage labels or progress |

Corollaries: bind `disabled` immediately (double-clicks cannot
double-submit) and defer the *visible* loading state ~200 ms so fast local
work paints nothing. Pre-reserve space a control may grow into (`width`,
not `min-width`) so it does not resize mid-action.

## Keep the back-out path quiet

Cancel, Dismiss, Close, and Discard are not destructive: ghost styling, no
color, no shortcut chip, no animated affordance. The visual weight belongs
to the affirmative action so the two do not compete. Esc always works.
A quit or stop that loses running work is the exception: it IS destructive,
states the consequence plainly, and defaults to Cancel.

## Match siblings

Adjacent components in one flow share icons, shortcut conventions, and
submit semantics. Diverge only for a real role difference — or fix both.
This is not "match every pattern in the repo" (some are debt); it is about
components the user sees at adjacent moments.

## Keyboard and focus

Common workflows complete in 1–2 actions. Default focus lands on the field
or primary action Enter should submit; Esc backs out without noise. Long
option lists get a searchable surface (the command palette) rather than
more scrolling. Shortcut labels come from the registry and match the
platform — a placeholder for an undecided shortcut ships as no label.

## Empty and error states

Missing data shows a direct action to acquire it ("Add a project to get
started"). Toasts for transient confirmations; inline persistent errors
when the user must read, retry, copy, or act. External links (docs, token
pages, billing) sit next to the empty state that needs them, not buried in
a menu.

## Motion

Reduced motion is respected per element: movement stops
(`motion-reduce:animate-none`), but a still spinner state still says
"working". Animation clarifies continuity (expanding content, enter/exit)
— it never decorates.
