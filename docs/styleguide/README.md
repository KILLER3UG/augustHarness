# August UI styleguide

Roles and rules for the desktop UI. Token values live in
[frontend/desktop/src/styles/tokens.css](../frontend/desktop/src/styles/tokens.css) (canonical) and
the Tailwind theme; this file documents what each role is *for*. Enforcement
that is mechanical lives in `check:design` (`scripts/check-design.mjs`) — the
ratchet stops new drift; this page tells you what to reach for instead.

Identity: **warm and quiet**. The paper background carries the chrome, color
is reserved for state, and August hosts other things (streams, diffs,
previews), so its own surfaces recede.

## Color roles

Tokens pair a surface with a foreground that meets contrast on it; always use
them together. The three text tiers (`--dt-fg-1/2/3`) are sanctioned contrast
levels (~15:1, ~7:1, ~4.5:1) — do not hand-soften text (`text-foreground/85`
exists in the wild but new code picks a tier).

- `background` = app canvas. `card` = panels lifted off it. `popover` = floating menus.
- `primary` = the single affirmative action in a flow — never decoration, never a second button.
- `muted` = de-emphasized text, captions, placeholders; not body copy.
- `accent` = hover/active backgrounds for ghost buttons and list rows; not solid fills (use `secondary`).
- `destructive` = delete/discard/irreversible actions and error states — **never Cancel**.
- `success` / `warning` / `danger` / `info` (+ `-fg` for text) = the only status colors;
  raw Tailwind warm palettes are banned by `check:design` (`raw-status-color`).
- `border` = all hairlines. `ring` = focus-visible and selection halos, not persistent decoration.
- Diff washes: `--dt-code-added-wash` / `--dt-code-removed-wash`; git status has its own
  decoration colors and nothing else borrows them.

## Type and rhythm

The rem type scale (`text-3xs` → `text-2xl`) is load-bearing: root-level
`data-text-size` rescaling reads it. Arbitrary `text-[Npx]` is banned
(`px-type` rule). Body 14px, dense rows 13px, sub-text 12px, captions 11px
uppercase `tracking-wider`. Mono (`--font-code`) for anything literal: paths,
commands, ids, tokens.

## Elevation

Use the Surface primitive (`frontend/desktop/src/components/ui/Surface`) and the
existing ladder (`--elev-flat` …). Do not hand-write a box-shadow. If a
surface seems to need more lift than the ladder's top, the thing usually
wants the focus `ring`, not a bigger shadow.

## List rows

One recipe everywhere (sessions, palette results, settings nav):

- idle: transparent; hover: `bg-accent`; the persistent current row: `bg-accent`
  plus a marker (`aria-current` / `data-current`) so CSS can tell selection
  from hover.
- Never invent a "selected" color; mix from the tokens.

## Components

Reach for the shared primitives under `frontend/desktop/src/components/ui/`
(Button, Badge, Surface, dialogs) before writing custom CSS; a raw
`<button>` outside `components/ui/` fails `raw-button`. Tooltips name
controls only — anything the user must read while acting belongs inline.
Shortcut chips render from
[lib/shortcuts.ts](../frontend/desktop/src/lib/shortcuts.ts) (the one
registry + ShortcutsModal); keys baked into label strings fail
`copy-baked-keys`.

## When this guide is silent

1. Find the closest sibling in `frontend/desktop/src/` and follow it.
2. Check `components/ui/` for a primitive that already encodes the pattern.
3. A token question: `tokens.css` is canonical — use what is there, or add
   the role in both light and dark.
4. Then ask, before inventing.

UX behavior rules (copy, feedback, back-out paths):
[ux-rules.md](ux-rules.md).
