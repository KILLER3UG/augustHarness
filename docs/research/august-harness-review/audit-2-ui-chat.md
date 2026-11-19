# Audit — UI shell/settings/theming + chat rendering/streaming

Repo: `C:/Dev/augustHarness` @ `9804b069` (branch master). Scope: area 3 (shell, sidebars, settings, icons, theming, a11y) and area 4 (chat rendering, streaming, tool-call cards, subagent UI). All claims below were re-verified against the working tree in this session unless marked INFERRED.

## Verdict

The chat/streaming stack is the strongest part of the frontend: `makeStreamHandlers.ts` is a complete, defensively-written reducer over the SSE vocabulary; `ActivitySummary`/`DisclosureRow`/`ThinkingDisclosure` implement the "one activity line per tool run" grammar correctly (opens on failure, settled line carries no time); markdown rendering is XSS-hardened with stream-aware highlight skipping and size-bounded caches; and the composer's context readout follows the "omit when unmeasured" rule. Area 3's foundations (token system, Backdrop modal ladder, focus-trap helper, resizable-pane keyboard support) are also genuinely good. The two real problems are (1) a confirmed silent drop of the backend's `subagentFanout` SSE event — no Zod schema, no dispatch case, no UI — so subagent-fleet receipts never reach the user, and (2) significant docs drift: at least seven items still listed as open in the owner's gap list are already fixed in code (Backdrop a11y, ModelRow trap, ClarifyTool keydown scoping, sidebar resize keyboard support, Turn Limits panel removal, t/s chip removal, "dead" stores that have live readers), which will misdirect future work. Secondary issues: typography tokenization is not holding in the largest settings panel (11 distinct sizes in MemorySection) and across ~6 chat components (5 arbitrary rem literals), and `SettingsSelect`'s custom listbox lacks arrow-key navigation.

## Findings

### [HIGH] `subagentFanout` SSE event is emitted by the backend but never reaches the UI

- What I saw (VERIFIED): `backend-py/app/services/tools/spawn_subagents_tool.py:537` emits `{'type': 'subagentFanout', 'fanoutId': ..., **payload}`, and the type is listed in `backend-py/app/services/workbench/emit_types.py:37`. On the frontend, `dispatchWorkbenchEvent` (`frontend/desktop/src/api/workbench/streamEvents.ts:435-530`) has cases for `subagentStart/Done/Proposed/Text/Retry/Warning/ToolCall/ToolResult` but **no case for `subagentFanout`**, and the switch ends at line 530 with **no `default:` clause**. The Zod schema (`frontend/desktop/src/api/schemas/workbench.ts`) has 36 `z.literal` event types, none named `subagentFanout`. A repo-wide grep finds zero frontend references.
- Why it matters: this is the fleet-level receipt ("why only 6 of 8 lanes started", budget/round accounting). The backend emits it; the desktop drops it on the floor. Because there is also no `default:` case, the failure is fully silent — at most a schema-validation console warning, never a UI surface. This confirms and pins down the known-open item ("SCHEMA_ONLY in the SSE gate").
- What good looks like: add a `subagentFanout` literal to the schema and a dispatch case that surfaces the fanout receipt (e.g. as a muted line in the subagent lane / ActivityGroup, or at minimum a dev-visible log path). Add a `default:` case that routes unknown event types to a single debug channel so future backend events can't disappear silently again.

### [MED] Docs-drift: at least seven "open" gap items are already fixed in code

- What I saw (VERIFIED, each):
  - "Backdrop lacks Escape/role=dialog/aria-modal/focus trap" → `Backdrop.tsx:142-164` implements topmost-scrim Escape at document-capture phase; `trapFocus` opt-in at `:95/:133`; the role/aria-modal split is documented at `:45-52` (role belongs on the inner dialog box — correct design).
  - "ModelRow modal lacks focus trap + initial focus" → `ModelRow.tsx:163` uses `useFocusTrap`, `:285-286` sets `role="dialog" aria-modal="true"`, `:319` has `autoFocus`.
  - "ClarifyTool binds document keydown with aria-modal=false" → the handler is now scoped to the card (`ClarifyTool.tsx:210-229`, rationale at `:113-125`); `aria-modal="false"` at `:267` is a deliberate non-modal-dialog choice.
  - "SessionSidebar resize separator not keyboard-operable" → `useResizablePane.ts:151-183` returns `role="separator"`, `tabIndex:0`, `aria-valuenow/min/max`, and Arrow/Home/End handling.
  - "Turn Limits keeps a Save button" → the turn-bounds/subagent-limits panels were removed deliberately (`SettingsPage.tsx:415-419`).
  - "'N t/s' readout still on screen" → removed; the removal is documented at `AssistantMessageContent.tsx:177-193`. ("answered via {model}" does remain at `:220-228`, with a strong kept-on-purpose rationale.)
  - "Dead stores store/promptCacheLive.ts + store/contextSectionsLive.ts have no readers" → both are read by `sections/chat/composer/ContextUsageReadout.tsx` and written by `sections/chat/makeStreamHandlers.ts`; they are live, not dead.
- Why it matters: the owner's working list of open gaps is the input to future planning. Seven stale items mean wasted re-verification, and worse, the genuinely open items (like `subagentFanout`) compete with already-done work for attention.
- What good looks like: prune the resolved items from the gap/roadmap docs in the same change that closes them; keep the list as the single source of "actually open".

### [MED] Settings-panel typography unification is not holding in the largest panel

- What I saw (VERIFIED): `sections/settings/MemorySection.tsx` uses 11 distinct text-size values: `text-2xs` (41×), `text-xs` (12×), `text-sm` (2×), plus arbitrary literals `text-[0.65625rem]`, `text-[0.71875rem]`, `text-[0.75rem]`, `text-[0.78125rem]`, `text-[0.8125rem]`, `text-[0.9375rem]`, `text-[1.0625rem]`, `text-[1.25rem]`. By contrast `PhonePairingSection.tsx` uses only `text-xs/sm/2xs` and `SettingsPage.tsx` itself has zero size classes (the unified grammar lives in CSS).
- Why it matters: the shipped direction was "settings panels unified to 20px headers + one row grammar + hairline containers". The docs' "4–8 font sizes per panel" understates the worst case; a 0.65625rem (10.5px) literal is off the token ramp entirely and will not respond to any future type-scale change.
- What good looks like: every settings surface draws from the token ramp only (`text-2xs/xs/sm/base` + the display sizes). Sweep MemorySection (the outlier) first; the other sections are close already.

### [MED] Arbitrary rem font literals are scattered across chat components too

- What I saw (VERIFIED): `ActionNeededCard.tsx:103,108,123,131` (`0.8125rem`, `0.78125rem`), `ClarifyTool.tsx:343,431` (`0.9375rem`), `DocumentBadge.tsx:35` (`0.8125rem`, `1.0625rem`), `SubagentTimeline.tsx:127,201,214,219,222,227,278` (`0.75rem`, `0.78125rem`, `0.8125rem`), `ChangesPill.tsx:142,150,181,195,214,229,408,428,438` (`0.8125rem`, `0.75rem`), `DiffView.tsx:357,388,400`. Five distinct non-token sizes total.
- Why it matters: same tokenization break as above, on the chat side. The docs counted "three un-tokenised rem font literals"; the real count is higher and spread wider, so a one-file fix won't close it.
- What good looks like: map `0.75rem→text-xs`-adjacent tokens (or add one named token if 12.5px/13px is genuinely wanted), and replace the literals file-by-file; add a lint rule (e.g. `check:design`) that forbids `text-[N.NNNNrem]` outside the token definition.

### [MED] `SettingsSelect` listbox is not keyboard-navigable once open

- What I saw (VERIFIED): `components/settings/SettingsSelect.tsx:43-57` handles only outside-click and Escape; the trigger has correct `aria-haspopup/expanded/controls` (`:66-68`), but there is no ArrowUp/ArrowDown/Home/End handling, no `aria-activedescendant`, and focus stays on the trigger when the list opens — options are reachable only by Tab or mouse.
- Why it matters: this custom listbox exists specifically to avoid native `<option>` rendering bugs on Windows/WebView2, and it is used across at least 7 settings surfaces (MemorySection, GeneralSection, ComputerAccessSettings, LibrarySection, AuditTimeline, CustomIntegrationForm, AddProviderForm). Every one of them inherits the gap. A listbox pattern without arrow keys is a WCAG 2.1.1 concern for keyboard users.
- What good looks like: on open, move focus to the selected option; handle ArrowUp/Down (wrap), Home/End, and typeahead; keep `aria-activedescendant` in sync (or use roving tabindex). Escape already works.

### [MED] Three dialog surfaces advertise `aria-modal="true"` with no focus containment anywhere

- What I saw (VERIFIED): `ModelVisibilityModal.tsx:94-95`, `NotificationsPanel.tsx:147-148`, and `ExamBanner.tsx:179-180` each render `role="dialog" aria-modal="true"` inside a `<Backdrop>` without passing `trapFocus`, and none of the three files imports `useFocusTrap`. The other four trap-less Backdrop users (CommandPalette, QuitConfirmModal, ShortcutsModal, ProviderOnboardingModal) own their own `useFocusTrap`, so they're fine.
- Why it matters: `aria-modal="true"` is a promise that keyboard focus is contained; `useFocusTrap.ts:9-25` documents exactly this failure mode ("the dialog still advertised aria-modal=true" while the trap does nothing). Screen-reader users are told the rest of the app is inert while sighted keyboard users can still Tab out behind the scrim.
- What good looks like: pass `trapFocus` to Backdrop at these three call sites (the primitive already handles nesting/inner-trap ownership), or mount their own `useFocusTrap` like the palette/quit/shortcuts modals do.

### [LOW] `dispatchWorkbenchEvent` has no `default:` case — unknown events vanish

- What I saw (VERIFIED): `streamEvents.ts:435-530` — the switch ends after `guardModeChanged` with no default; an unrecognized `type` only produces a schema-validation warning if the Zod schema also doesn't know it.
- Why it matters: this is the mechanism that made the `subagentFanout` drop silent. Any newly added backend event lands in the same gap with zero visibility.
- What good looks like: a `default:` branch that (dev mode) logs `unknown workbench event <type>` and (always) increments a dev-only counter, so new events are noticed during development instead of after a release.

### [LOW] `session_status` is a dead entry in the backend's emitted-event vocabulary

- What I saw (VERIFIED): `emit_types.py` lists `session_status` among "commonly observed" types, but a repo-wide grep finds no Python emitter and no frontend case/schema for it.
- Why it matters: it's noise in the contract list that the frontend schema was presumably validated against; a reader can't tell whether it's a real event to support.
- What good looks like: remove it from `emit_types.py` (or implement the emit + handling if it was meant to exist).

### [LOW] Sidebar resize handle announces a bare pixel number

- What I saw (VERIFIED): `useResizablePane.ts:155-158` sets `aria-valuenow/min/max` and a label, but no `aria-valuetext`; a screen reader announces e.g. "252" with no unit.
- Why it matters: minor, but the pattern is otherwise exemplary (full keyboard support); the announcement is the last inch.
- What good looks like: `aria-valuetext={`${Math.round(size)} pixels`}` (or localized equivalent) on the separator.

### [LOW] Settings tier counts in docs have drifted from the registry

- What I saw (VERIFIED): `settings-registry.ts` currently defines 46 sections plus 5 category headers (51 `id:` occurrences); tier distribution is 17 `basic` / 31 `hidden` (grep-verified). The handoff docs say "26 of 44 sections tier:hidden".
- Why it matters: the hidden-tier share is the substance of the open "depth decision" (search/deep-link-only surfaces); planning against 26/44 misjudges how much of settings is invisible-by-default (now two-thirds).
- What good looks like: regenerate the count from the registry (it's greppable) whenever the depth decision is revisited; better, note in the registry header that docs quoting counts should be updated with it.

### Candidate removals / simplifications

1. **REMOVE `session_status` from `emit_types.py`** — emitted nowhere, handled nowhere (VERIFIED by grep). Dead contract entry.
2. **CONSOLIDATE the five arbitrary rem font literals** in chat components (ActionNeededCard, ClarifyTool, DocumentBadge, SubagentTimeline, ChangesPill, DiffView) onto the token ramp — deletes ~20 one-off class strings and makes the type scale actually own the chat surface.
3. **DO NOT remove `store/promptCacheLive.ts` / `store/contextSectionsLive.ts`** — the docs call them dead; they are read by ContextUsageReadout and written by makeStreamHandlers (VERIFIED). The correct action is fixing the docs, not deleting live code. (Listed because the owner's list proposes exactly this wrong removal.)
4. **CONSOLIDATE `SettingsSelect`'s duplicated accessible name** — the trigger's `aria-label` and the listbox's `aria-label` are the same string; the listbox can inherit via `aria-labelledby` of the trigger, removing one prop thread (`SettingsSelect.tsx:65,91`). Minor.

## Strengths worth keeping

- **The activity-line grammar is real and well-implemented.** `ActivitySummary.tsx` builds one mixed-kind phrase per turn (`buildActivityCountSegments:68-103`, `buildCompletionSummary:110-121`), opens on failure (`:174-176`), collapses when the final answer starts, and puts elapsed time only on the live line (`:293-300`) — the settled line carries no time, exactly as specced.
- **`DisclosureRow`/`ThinkingDisclosure`** implement the disclosure contract correctly: `aria-expanded`/`aria-controls` pairing (`DisclosureRow.tsx:111-112`), lazy body mount with the animate-out caveat documented, and the thinking card renders distilled sentence lists (`ThinkingDisclosure` + `SubagentTimeline.tsx:339-375` reuse the same distillation).
- **`makeStreamHandlers.ts` (1265 lines) is a serious, complete reducer**: subagent blocks with nested fanout state, todo cards, recovery notices, `retryNotice` with attempt counts (`:1216,:1239`), `usedFallback`, `turnEnd` reason handling, and per-block stable ids that survive re-renders.
- **Markdown is defense-in-depth**: model HTML is escaped, links pass through `safeExternalHref` (`ChatMarkdown.tsx:43-60`), highlight.js is skipped during live parses (`:78-82`), and the parse caches are bounded by content size, not entry count (`:108-120`) — the right fix for a long-lived desktop process.
- **`Backdrop` + `useFocusTrap` + `useResizablePane` are high-quality primitives** with documented failure histories (portal remount traps, detached roots, nested-trap ownership, layer ladder) — keep them as the only path to modals and resizable panes.
- **The context-% readout follows the measurement contract**: backend-measured `contextPressure` only, nothing rendered when unmeasured, and a measured 0% still prints `0%` (`ContextUsageReadout.tsx:16-40`, `store/contextLive.ts:4`).
- **`lib/phase-labels.ts`** translates the `update_state` phase vocabulary in one place and title-cases unknown phases rather than hiding them (`:42-48`).

## Coverage ledger

Files opened this session (relative to repo root):

- frontend/desktop/src/lib/icons.tsx (full; ~89.7k chars — barrel replacement exists, 80+ glyphs incl. `COPY_ICON_SVG` used by ChatMarkdown)
- frontend/desktop/src/styles.css (full, 44 lines — Tailwind entry, no hand rules)
- frontend/desktop/src/components/chat/ActivityGroup.tsx (full)
- frontend/desktop/src/lib/tool-summary.ts (full)
- frontend/desktop/src/components/chat/DisclosureRow.tsx (full)
- frontend/desktop/src/components/chat/ThinkingDisclosure.tsx (full)
- frontend/desktop/src/store/promptCacheLive.ts (full)
- frontend/desktop/src/store/contextSectionsLive.ts (full)
- frontend/desktop/src/sections/chat/makeStreamHandlers.ts (lines 1–1265, three passes; 1265 total)
- frontend/desktop/src/api/workbench/streamEvents.ts (full)
- frontend/desktop/src/api/schemas/workbench.ts (full)
- frontend/desktop/src/components/chat/SubagentTimeline.tsx (full)
- frontend/desktop/src/components/chat/focused-subagent.ts (full)
- frontend/desktop/src/components/chat/ClarifyTool.tsx (lines 1–229 of 512 + targeted greps)
- frontend/desktop/src/sections/chat/ChatMarkdown.tsx (lines 1–120 of 684 + demotion greps)
- frontend/desktop/src/lib/markdownHeadings.ts (full)
- frontend/desktop/src/lib/phase-labels.ts (full)
- frontend/desktop/src/sections/chat/composer/ContextUsageReadout.tsx (full)
- frontend/desktop/src/sections/chat/message/AssistantMessageContent.tsx (lines 160–269 of 362 + greps)
- frontend/desktop/src/components/chat/ToolCallItem.tsx (lines 1–130 of 222)
- frontend/desktop/src/realtime/bridge.ts (lines 1–120 of 316)
- frontend/desktop/src/components/overlays/Backdrop.tsx (full)
- frontend/desktop/src/hooks/useFocusTrap.ts (full)
- frontend/desktop/src/components/settings/SettingsSelect.tsx (full)
- frontend/desktop/src/hooks/useResizablePane.ts (lines 140–187 + greps; prior session read full)
- frontend/desktop/src/components/shell/SessionSidebar.tsx (lines 1–80 of 113; prior session read full)
- frontend/desktop/src/sections/settings/SettingsPage.tsx (lines 400–439 + greps; prior session read full)
- frontend/desktop/src/settings/settings-registry.ts (structure lines 100–164 + tier/section greps; prior session read full)
- Backend cross-checks: backend-py/app/services/workbench/emit_types.py (head), backend-py/app/services/tools/spawn_subagents_tool.py:537, brain_config_service.py fanout greps.

Carried from the prior session (read in full there; contents not re-verified line-by-line this session): `App.tsx`, `routes.ts`, `components/shell/ChatLayout.tsx`, `components/shell/BackendStatusPill.tsx`, `styles/tokens.css`. No findings in this report rest solely on those reads.

Skipped, with reasons:

- `sections/chat/ChatThread.tsx` (1716 lines) — read head (1–150) + greps only; it composes the audited pieces (makeStreamHandlers, ActivitySummary, message components); budget prioritized the streaming core.
- `sections/chat/ChatThreadComposer.tsx` (780) / `ChatThreadMessagePane.tsx` (388) — greps only (context-% wiring verified via ContextUsageReadout + composer comments at :668-697); full read skipped for budget.
- Remaining `components/chat/tool/*` (ToolCallItemBody, extractors, types), ArtifactCard, ChangesCard, CircuitArtifactCard, DiffView, SearchResultsCard, todo/recovery cards — enumerated; ToolCallItem read to its composition point only.
- `styles/base.css` (477), `styles/settings.css` (58), `styles/chat.css` — measured + targeted greps only; the token source (tokens.css) was read fully in the prior session.
- `store/*` beyond the two "dead" candidates — enumerated (21 files); no other store is implicated in an open finding.
- "Sidebar section header three icons vs reference two" — known-open visual item from the owner's list; not re-verified this session (needs the reference mockup to judge).

## Open questions for the owner

1. `subagentFanout`: should the fanout receipt surface as a line in the subagent lane/ActivityGroup, as a transient toast, or only in the Runs/event-log surface? (The backend payload carries `fanoutId` + per-lane detail.)
2. Depth decision: with 31 of 46 settings sections now `tier: 'hidden'` (search/deep-link only), is two-thirds-hidden the intended steady state, or should the basic tier be re-expanded?
3. Docs hygiene: the gap list carried seven stale items — should docs updates be gated on the same change that fixes the gap, so the list stays trustworthy?
4. `answered via {model}` is kept deliberately (the only place naming the answering model). Confirm it stays as muted tier-2 text rather than migrating into the turn-provenance chip.
5. Group-across-turn-boundary remains an open owner decision; the current code collapses per message/turn. Which way should `ActivitySummary` compose when a turn continues an earlier group?
