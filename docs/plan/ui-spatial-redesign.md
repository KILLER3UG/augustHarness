# UI/UX Defect & Spatial Reorganization Spec

> **Audit baseline:** branch `fix/local-model-2026-11-16` @ `c80ed982`, live Tauri desktop shell (`npm run dev:vite` + `npm run tauri:dev:cdp` on CDP `:9223`), captured at `1440×900` and `1280×800`.
> **Evidence directory:** `docs/evidence/ui-redesign/before/` (23 PNGs).
> **Scope:** UI/UX defect audit, target spatial specification per slice (`composer`, `shell`, `canvas`), palette tokens, disjoint file-ownership map, and rule reconciliation against [docs/agent/product.md](../agent/product.md).

---

## 1. Captured Evidence Inventory (`docs/evidence/ui-redesign/before/`)

| # | Screenshot | Viewport | What it shows |
|---|---|---|---|
| 01 | `01-chat-empty-1440x900.png` | 1440×900 | Empty `/c/...` thread: Launchpad greeting, missing starter chips, floating pill with `+` only, model+context footer below pill |
| 02 | `02-chat-thread-top-1440x900.png` | 1440×900 | Existing thread top: raw `● QUEUED` `[SUBAGENT_COMPLETE ...]` cards, uncollapsed thinking + tool calls, `977k` stale context readout |
| 03 | `02b-chat-thread-mid-1440x900.png` | 1440×900 | Existing thread mid: multi-step `Read a file, ran 2 tools` card interleaving `ThoughtStep`s, `Tool Search`, `spawn_subagents`, `Read Background Task` |
| 04 | `03-chat-thread-bottom-1440x900.png` | 1440×900 | Existing thread bottom: italicized stop warning, 4 stacked system notices, `Loaded skills 3 skills`, right-aligned user bubble, floating scroll-to-top button |
| 05 | `04-chat-thread-1280x800.png` | 1280×800 | Existing thread at default desktop window size (`1280×800`) |
| 06 | `05-chat-empty-1280x800.png` | 1280×800 | Empty thread at `1280×800` |
| 07 | `06-composer-model-menu-1440x900.png` | 1440×900 | Overloaded `ModelEffortMenu`: provider header, search, models, green-blue `Effort` slider, `Extended thinking`, `augustHarness` workspace chip, red `⚠ Full access` chip, and clipped `Context 4k 8k 16k 32k 64k [8192] [Apply]` row |
| 08 | `07-composer-workspace-popover-1440x900.png` | 1440×900 | Nested `Projects` workspace popover overlapping the upper half of `ModelEffortMenu` |
| 09 | `08-composer-access-popover-1440x900.png` | 1440×900 | Nested `Agent mode` access popover overlapping `ModelEffortMenu`, red `⚠ Full access` highlight |
| 10 | `09-composer-context-presets-1440x900.png` | 1440×900 | Clicking `977k` / `8k` context chip opens the same overloaded `ModelEffortMenu` instead of a dedicated context popover |
| 11 | `10-composer-actions-plus-1440x900.png` | 1440×900 | `+` (`Composer actions`) popover: Add (`Attach file`, `Mention skill / tool`), Input (`Camera capture`), Run (`Dispatch workstreams`, `Ask in parallel`, `Debate`) |
| 12 | `11-sidebar-bots-tab-1440x900.png` | 1440×900 | Left sidebar `Bots` tab empty state |
| 13 | `12-sidebar-footer-menu-1440x900.png` | 1440×900 | Left sidebar footer `UserDropdown` menu (`Account settings`, `Create account`, `Appearance`, `What's new?`, `Get help`) |
| 14 | `13-sidebar-more-menu-1440x900.png` | 1440×900 | Left sidebar `More` expanded to reveal `Docs`, plus `8k` context chip updated after `llama-servers` fetch |
| 15 | `14-sidebar-runs-page-1440x900.png` | 1440×900 | `/runs` page blocked on load by an undismissable blank `ConfirmDialog` |
| 16 | `15-settings-tab1-general-1440x900.png` | 1440×900 | Settings route `/settings/general` (Tab 1: General) |
| 17 | `16-settings-tab2-models-1440x900.png` | 1440×900 | Settings route `/settings/model-providers` (Tab 2: Models & Local Runtime) |
| 18 | `17-settings-tab3-memory-1440x900.png` | 1440×900 | Settings route `/settings/memory-knowledge` (Tab 3: Memory & Skills) |
| 19 | `18-settings-tab4-permissions-1440x900.png` | 1440×900 | Settings route `/settings/permissions-workspace` (Tab 4: Permissions & Workspace) |
| 20 | `19-settings-tab5-updates-1440x900.png` | 1440×900 | Settings route `/settings/app-updates` (Tab 5: App & Updates) |
| 21 | `20-sidebar-collapsed-1440x900.png` | 1440×900 | Collapsed sidebar state with topbar expand button and empty right `Workbench` drawer |
| 22 | `21-right-drawer-menu-1440x900.png` | 1440×900 | Right drawer `"Open tab"` 10-tile grid (`Canvas`, `Preview`, `Diffs`, `Terminal`, `Plan`, `Browser`, `Notepad`, `Subagents`, `Artifacts`, `Circuit`, `Routines`, `Jobs`) |

---

## 2. Defect Table

| # | Screenshot(s) | What is wrong | Owning source file(s) | Target slice |
|---|---|---|---|---|
| D1 | `06`, `07`, `08`, `09` | **Overloaded model popover (P0):** `ModelEffortMenu` packs 6 distinct concerns into one `304px` panel: provider/model list, effort slider, extended thinking toggle, `ComposerWorkspaceChips`, `WorkbenchModeSelector`, and local-model context presets. Opening Workspace or Access spawns a second popover on top of the model menu. | `sections/chat/composer/ModelEffortMenu.tsx`, `sections/chat/ChatThreadComposer.tsx` | `composer` |
| D2 | `06`, `09` | **Cramped & clipped context row:** Inside `ModelEffortMenu`, the inline row `Context 4k 8k 16k 32k 64k [8192] [Apply]` overflows `304px` horizontally, clipping the `Apply` button on the right edge. Clicking `[model-context-chip]` outside opens the bloated model menu instead of a dedicated context popover. | `sections/chat/composer/ModelEffortMenu.tsx` | `composer` |
| D3 | `01`, `02`, `03`, `06` | **Footer row & stale `977k` token readout below composer:** `ChatThreadComposer` renders `composer-foot` below the pill (`977k LocalModels · LFM3 1.7B Q8...`), violating the "nothing below the composer" rule and displaying `selected.contextWindow` (`1,000,000` → `977k`) instead of the running `llama-server` `ctxSize` (`8k`) before the menu is opened. | `sections/chat/ChatThreadComposer.tsx`, `sections/chat/composer/ModelEffortMenu.tsx`, `styles/chat.css` | `composer` |
| D4 | `01`, `03`, `04`, `05` | **Single-row composer missing primary left/right controls:** `ComposerToolbar` is a single row (`+`, textarea, `Send`) with Workspace, Access, Model, and Context either hidden below the card or buried two clicks deep inside `ModelEffortMenu`. Placeholder reads `"Write a message…"` instead of `"Write a message, or tell August what to do..."`, radius is `1.625rem` (`26px`) instead of `14px`, and textarea padding doesn't match `14px 16px`. | `sections/chat/ChatThreadComposer.tsx`, `sections/chat/composer/ComposerToolbar.tsx`, `styles/chat.css`, `styles/signal-desk.css` | `composer` |
| D5 | `06`, `08` | **Alarm/colour noise on composer controls:** `model-effort-slider` paints a bright green-to-blue gradient (`#2ecc71 → #2bd6a3 → #37b7ff`), and `WorkbenchModeSelector` paints the resting `Full access` chip in red (`text-destructive` + `TriangleAlert`). Also `tailwind.config.cjs` still defines a terracotta focus `ring` (`rgb(217 119 87 / 0.20)`). | `styles/chat.css`, `components/chat/WorkbenchModeSelector.tsx`, `tailwind.config.cjs` | `composer` (+ `tokens`) |
| D6 | `01`, `02`, `03` | **Jarring white `+ New thread` CTA & redundant sidebar nav:** `+ New thread` uses a solid `#ffffff` background with dark text (loudest element in the rail). Below it sit 5 nav rows (`Threads 3`, `Runs`, `Artifacts`, `Settings`, `More` → `Docs`) AND a second `Sessions | Bots` segmented control below them, splitting threads/runs/bots across two separate mechanisms. | `components/sidebar/SessionListNav.tsx`, `components/sidebar/SessionList.tsx`, `styles/signal-desk.css` | `shell` |
| D7 | `01`, `02`, `03`, `04` | **Double-nested workspace tree & `Tasks` clutter in sidebar:** `Recent` renders a folder `augustHarness 1`, then a duplicate project-group subheader `augustHarness 1` indented inside it (`groupProjects` + `FolderHeader`), plus a separate `Tasks` section with nested `School 1` / `No project 1` sub-headers and inline `+ / edit / delete` icon clusters. | `components/sidebar/SessionList.tsx`, `components/sidebar/FolderTree.tsx`, `components/sidebar/SessionRow.tsx` | `shell` |
| D8 | `01`, `02`, `12` | **Misaligned sidebar footer & background chrome:** Sidebar background (`#0a0a0a`) is darker than target (`#111113`), while canvas is `#0e0e10` instead of `#0a0a0b`. Footer shows `G Guest / Local workspace ···` with no direct Settings gear, and `Settings` is buried in the top nav stack. | `components/sidebar/SessionList.tsx`, `components/ui/user-dropdown.tsx`, `styles/tokens.css`, `styles/signal-desk.css` | `shell` |
| D9 | `01`, `02`, `20`, `21` | **Tall `56px` titlebar & noisy header controls:** `ChatTitlebar` is `56px` (`--shell-titlebar-h: 56px`) instead of `40px`. Title opens a dropdown menu instead of click-to-edit inline rename; right side uses a generic `workbench-launcher` (`44×44`) opening a 12-tile `"Open tab"` drawer with no direct Artifacts / Docs compact buttons and no artifact-presence highlight rule. | `components/shell/ChatTitlebar.tsx`, `components/shell/RightDrawerLauncher.tsx`, `components/shell/RightDrawer.tsx`, `styles/tokens.css`, `styles/signal-desk.css` | `shell` |
| D10 | `14` | **P0 bug — `/runs` locked by an undismissable blank modal:** `AutomationsSection` passes `open={confirmState !== null}` to `<ConfirmDialog>`, but `useConfirmDialog()` initializes `state` to `{ open: false, ... }` (never `null`). Visiting `/runs` locks the entire app behind a blank `Cancel / Confirm` modal that cannot be closed. | `sections/runs/AutomationsSection.tsx` | `shell` |
| D11 | `02` | **Raw `[SUBAGENT_COMPLETE]` & `● QUEUED` bubbles in transcript:** `makeStreamHandlers.onUserMessageInjected` lacks the `parseSubagentReceipt` filter that `session-subscriber.ts` and `session-history.ts` have, and `UserMessageBubble` renders persisted `queued: true` `[SUBAGENT_COMPLETE ...]` payloads verbatim as right-aligned user bubbles with `● QUEUED` labels. | `sections/chat/makeStreamHandlers.ts`, `sections/chat/message/UserMessageBubble.tsx`, `sections/chat/stream/session-history.ts` | `canvas` |
| D12 | `02`, `03` | **Fragmented thinking / tool / subagent blocks & `"queued"` status bug:** `AssistantBlockTimeline` breaks tool runs around `thinking` blocks and keeps subagent rows separate, producing a tall multi-card stack (`Read a file, ran 2 tools` → `ThoughtStep` → `Tool Search` → `ThoughtStep` → `spawn_subagents` → `ThoughtStep` → `Read Background Task` → `Finished with errors`). Also `SubagentDelegateRow.tsx:125` falls back to `'queued'` for completed/done workers, and `ActivityGroup` lacks the `#121215` vertical-timeline inset styling. | `sections/chat/message/AssistantBlockTimeline.tsx`, `components/chat/ActivityGroup.tsx`, `components/chat/SubagentDelegateRow.tsx`, `lib/tool-summary.ts`, `styles/signal-desk.css` | `canvas` |
| D13 | `03`, `04` | **Noisy post-tool notices & receipt chip mismatch:** Four stacked system/status lines, italic stop message, and `Loaded skills 3 skills` card add vertical clutter. Receipt chips (`ModelContextChip`, `BotReceiptChip`, `AutomationReceiptChip`) render as collapsed `.memory-card` accordions with `Undo` hidden inside the body and no `Edit` button on the 1-line summary row. | `sections/chat/message/AssistantMessageContent.tsx`, `components/chat/ModelContextChip.tsx`, `components/chat/BotReceiptChip.tsx`, `components/chat/AutomationReceiptChip.tsx`, `components/chat/TurnProvenanceChip.tsx` | `canvas` |
| D14 | `01`, `05` | **Empty state lacks monochrome starter chips:** `ChatEmptyState` shows `"New thread / What are we building today?"` and `useRecentOutputs()` (empty when no artifacts exist), leaving a blank gap above the composer instead of 3–4 starter chips (`Research Paper Studio`, `Analyze codebase`, `Create a routine bot`). | `sections/chat/ChatEmptyState.tsx`, `styles/signal-desk.css` | `canvas` |
| D15 | `02`, `03` (code) | **Floating `WakeUpBriefCard` toast over chat stream & user bubble width/fill:** `ChatLayout` mounts `WakeUpBriefCard` as `fixed left-1/2 top-[3.75rem] z-[80]` floating over the transcript. `UserMessageBubble` uses `max-w-[70%]` (instead of `max-w-[75%]`) and `--panel-2` (`#1c1c1f`) instead of `#18181b`. `ScrollToTopButton` floats on the right edge (`chat-scroll-chrome`) over the scrollbar track. | `components/shell/ChatLayout.tsx`, `components/shell/WakeUpBriefCard.tsx`, `sections/chat/message/UserMessageBubble.tsx`, `sections/chat/ChatThreadMessagePane.tsx`, `styles/signal-desk.css` | `canvas` (bubble/toast) + `shell` (`ChatLayout` mount removal) |
| D16 | `15`–`19` | **Settings visual tokens & rail truncation:** Settings rail truncates `"Models & Local Runti..."` and `"Permissions & Works..."` at `204px`, and `.page-title` serif `"Settings"` + `"Workspace / preferences"` eyebrow visually compete with the section `h2` right underneath. Note: Settings is a full-screen route (`/settings/:section`), not a modal, and the green `"Up to date"` / `"Backend: up"` indicators remain in `UpdateSection`. | `components/workspace/WorkspaceShell.tsx`, `sections/settings/SettingsPage.tsx`, `styles/settings.css` | `shell` (optional polish, keep Settings rail/sections functional) |

---

## 3. Target Layout & Concrete Acceptance Criteria

### 3.1 `composer` Slice
1. **Two-row floating composer pill:**
   - Centered inside `august-composer-slot`, `max-width: 760px` (`47.5rem`), surface `var(--dt-card)` (`#161618`), border `1px solid var(--dt-line)` (`rgba(255, 255, 255, 0.08)`), border-radius `14px` (replacing `1.625rem`).
   - Top row: auto-growing `<textarea>` with placeholder `"Write a message, or tell August what to do..."` (when idle in default agent mode), padding `14px 16px` (symmetric vertical growth up to `240px`).
   - Bottom bar (inside the pill, below the textarea, `px-3 pb-2.5 pt-1 flex items-center justify-between gap-2`):
     - **LEFT group:**
       1. `[+]` Attach / Actions button (`28×28`px / `size-7`, monochrome hover; opens `ComposerActionsMenu`).
       2. `[folder Workspace ▾]` pill (`ComposerWorkspaceChips`): displays folder basename only (e.g. `augustHarness` or `No workspace`) — never the full path on the pill; opens the `Projects` switcher popover anchored above the pill.
       3. `[Access: Standard ▾]` monochrome pill (`#222226` bg via `--dt-pill-bg`, `#a1a1aa` text via `--dt-fg-3`; never orange/red warning colour or `TriangleAlert` on the resting pill!). Displays `Access: Read-only`, `Access: Standard`, or `Access: Full access`.
          - **Access popover contents:** Primary 3-option radio group:
            - `Read-only` (sets `sandboxMode = 'read-only'`, `guardMode = 'ask'`)
            - `Standard` (sets `sandboxMode = 'workspace-write'`, `guardMode = 'edit'` or `'ask'` — default `Standard`)
            - `Full access` (sets `sandboxMode = 'danger-full-access'`, `guardMode = 'full'`; danger tone appears *only* inside the open menu row if needed, never on the resting pill)
          - Secondary compact rows below a hairline inside the Access popover preserve existing backend controls without leaking extra pills onto the composer bar: `Plan mode` toggle and `How I work` (`Agent` / `Orchestrator` / `Chat` / `Code`).
     - **RIGHT group:**
       1. Standalone `[16k ▾]` context pill (rendered **only** when the active model's provider is `LocalModels`): reads the live `llama-server` `ctxSize` from `listLlamaServers()` (e.g. `8k`, `16k` — **not** `selected.contextWindow`), shows an inline `Loader2` spinner while `server.status === 'starting'`, and opens a **dedicated Context popover** (`280px` wide) containing:
          - Preset chips: `4k`, `8k`, `16k`, `32k`, `64k` (using `LOCAL_CTX_CHOICES`, check whether `64k` = `65536` is included in `LOCAL_CTX_CHOICES`)
          - Custom token number `<input>` + `Apply` button with plenty of horizontal room so `Apply` is never clipped.
       2. `[Model ▾]` pill (`ModelEffortMenu` trigger): opens the **Models popover** containing **providers + models ONLY** as its primary section, plus **Effort & Thinking** as a clean secondary section below a hairline (proposed home below).
       3. `[↑ Send]` / `[■ Stop]` button (`28×28`px / `size-7`, circular): `#ffffff` bg with `#09090b` icon when ready (`opacity-30` when disabled), `#27272a` (`--dt-stop-bg`) bg with `#ffffff` square icon when streaming.
2. **Proposed clean home for Effort & Extended Thinking:**
   - Keep `Effort` (4-step segmented/slider `Low / Medium / High / Max` with `Ctrl+T` hint) and `Extended thinking` (`ThinkingSwitch`) as a **compact, monochrome secondary footer section inside the `[Model ▾]` popover**, separated by a `1px` hairline (`border-border/40`).
   - Remove the green-to-blue gradient from `.model-effort-slider` in `styles/chat.css` and style the filled track `#ffffff` (`var(--dt-primary)`) over `#27272a` (`var(--dt-elevated)`).
   - Remove `workspaceControl`, `modeControl`, and `localServerRow` (Context presets) completely from `ModelEffortMenu`.
3. **Nothing below the composer pill:**
   - Delete `<div className="composer-foot" data-testid="composer-footer-row">` from `ChatThreadComposer.tsx`. Zero token %, zero model text, zero disclaimers below the pill.

### 3.2 `shell` Slice
1. **Left sidebar (`240–260px`, default `252px`, bg `#111113` via `--dt-sidebar`, collapsible to `0`):**
   - **Header row (`40px` height matching topbar):** `"August"` (left, clean sans semibold, no subtitle) + collapse sidebar button (right).
   - **Primary action:** Full-width `New thread` button (quiet monochrome surface `#1c1c1f` with `1px` `rgba(255,255,255,0.08)` border and `#f4f4f5` text — **not** solid `#ffffff` slab) that **always** opens a brand-new empty thread (`createEmptySession(...)`).
   - **2-tab switcher directly below `New thread`:**
     - `Threads` (active by default) | `Bots & Runs`
     - Remove the legacy 5-item nav stack (`Threads`, `Runs`, `Artifacts`, `Settings`, `More` → `Docs`) and remove the duplicate `Sessions | Bots` strip.
   - **`Threads` tab content:**
     - Inline search box (`Search threads…`) at the top of the tab.
     - Flat list with **two** sections only: `Pinned` (shown when ≥1 thread is pinned) and `Recent` (all remaining threads sorted by `updatedAt` desc).
     - **Zero nested workspace folder tree:** remove `FolderHeader`, `UncategorizedHeader` (`Tasks`), `School 1` / `No project 1`, and `ProjectGroupHeader` nesting from the default thread list. Each `SessionRow` shows a muted folder basename tag (`augustHarness`) right-aligned or on hover (from `session.workspacePath`).
   - **`Bots & Runs` tab content:**
     - Combines `BotsRail` (bots list + create/chat/retire) and active/recent runs + scheduled automations with inline **Undo** receipts (absorbed from `RunsPage` / `AutomationsSection`, with the `AutomationsSection` `open={confirmState.open}` P0 modal bug fixed). Full `/runs` route remains deep-linkable if navigated to, without locking up.
   - **Sidebar footer (`44px` height, `border-top: 1px solid var(--dt-line)`):**
     - Profile name left (compact avatar + display name opening account switcher) + `Settings` gear icon button right (navigates to `/settings`), plus optional subtle update dot when an update is ready. Zero `"Last 30 days · 0 tokens"` text.
2. **Top header (`40px` height, `#0a0a0b` via `--dt-background`, `1px` bottom hairline):**
   - **Left:** sidebar-expand icon button (visible **only** when sidebar is collapsed) + thread title (`max-width: 420px`, truncated, **click to edit inline** via `<input>` on click/Enter — replacing the `DropdownMenu` trigger). No `Live` pill noise unless subtle dot on titlebar.
   - **Right:** compact icon buttons for **Artifacts** & **Docs** + **Artifacts drawer toggle** (highlighted with `--dt-primary` / indicator dot **only** if the current thread has produced/attached files) + native Tauri window controls (`Minimize`, `Maximize/Restore`, `Close`).
   - **Removed from top header:** breadcrumbs, search box, `Canvas` button, theme toggle, GPU/VRAM text, and `BackgroundJobsButton` clutter when idle.
3. **Settings polish (non-breaking):**
   - Ensure rail width in `WorkspaceShell.tsx` accommodates `"Models & Local Runtime"` and `"Permissions & Workspace"` cleanly (e.g. `220px` instead of `204px`) without ellipsis truncation at `1440×900`.

### 3.3 `canvas` Slice
1. **Chat column:**
   - Centered `max-width: 760px` (`47.5rem`, replacing `48rem` / `790px` variance).
   - Remove floating `WakeUpBriefCard` overlay (`"Welcome back"` toast over the stream).
2. **Empty state (`ChatEmptyState.tsx`):**
   - Calm greeting (`"What are we building today?"` or `"Good afternoon"` / `"What can I help with?"` using clean `--font-editorial` or `--font-sans` display scale) + **3–4 monochrome starter chips** in a centered wrap/row:
     - `Research Paper Studio`
     - `Analyze codebase`
     - `Create a routine bot`
     (Optional 4th: `Explain a concept` / `Review changes` — clicking seeds or submits the prompt cleanly).
   - Remove `Recent outputs` artifact list from the empty state.
3. **User bubbles (`UserMessageBubble.tsx`):**
   - Right-aligned, `max-width: 75%`, background `#18181b` (`--dt-user-bubble`), padding `12px 16px`, `14px` radius, no border.
   - **Never render `[SUBAGENT_COMPLETE ...]` or right-aligned `● QUEUED` machine-envelope cards:** add `parseSubagentReceipt` filtering in `makeStreamHandlers.onUserMessageInjected` AND a defensive render guard in `UserMessageBubble.tsx` / `ChatThreadMessagePane.tsx` so previously-persisted `[SUBAGENT_COMPLETE ...]` messages in localStorage never paint in the transcript.
4. **Collapsed process & subagent timeline (`AssistantBlockTimeline.tsx`, `ActivityGroup.tsx`, `SubagentDelegateRow.tsx`):**
   - Fold all consecutive/interleaved thinking blocks, tool calls, file reads, and settled subagent runs in an assistant turn into **ONE single-line collapsible pill** (e.g. `Read 3 files · Ran 2 sub-agents ▾`, `·`-separated clauses in `summarizeToolRun`).
   - Clicking expands to a vertical timeline inside a `#121215` (`--dt-timeline-inset`) inset card with `1px` `rgba(255,255,255,0.08)` border and continuous left timeline rail.
   - Fix `SubagentDelegateRow.tsx:125` so settled workers display `'done'` / `'completed'` instead of `'queued'`.
   - Suppress noisy post-turn status prose stacking (`Loaded skills` collapses or merges cleanly; `TurnProvenanceChip` stays collapsed/quiet; preserve `docs/agent/product.md` failure/approval rules — see §6).
5. **Receipt cards (`ModelContextChip.tsx`, `BotReceiptChip.tsx`, `AutomationReceiptChip.tsx`):**
   - Uniform horizontal receipt card: surface `#141417` (`--dt-receipt-bg`), `1px solid var(--dt-line)` hairline, `10px` radius, left status dot (`6px`), 1-line summary (`Context 8k → 16k · LFM3 1.7B`, `Bot "Digest" ready`, `Automation "Daily brief" · 09:00`), right-aligned compact `Edit` + `Undo` buttons (`Edit` opens the relevant quick-edit door — context popover / bot prompt / automation edit; `Undo` triggers the rollback/retire/delete mutation).

---

## 4. Palette & Design Tokens (`check:design` Compliant)

> **Constraint:** `npm run check:design` (`scripts/check-design.mjs`) ratchets raw hex literals (`hex-color`), `text-[Npx]` (`px-type`), `<button` outside `components/ui/` (`raw-button`), `style={{` (`inline-style`), and warm Tailwind classes (`raw-status-color`). All builders **must** use CSS custom properties in `frontend/desktop/src/styles/tokens.css` and Tailwind registrations in `frontend/desktop/tailwind.config.cjs`, plus `<Button>` from `@/components/ui/button`.

### Tokens to Update / Add in `styles/tokens.css` (`.dark` block)

| Token | Current value | Target value | Purpose |
|---|---|---|---|
| `--dt-background` / `--dt-background-hsl` | `#0e0e10` / `240 6.7% 5.9%` | `#0a0a0b` / `240 5.3% 4.1%` | Main canvas & topbar background |
| `--dt-sidebar` / `--dt-sidebar-rgb` | `#0a0a0a` / `10 10 10` | `#111113` / `17 17 19` | Left sidebar background |
| `--dt-card` / `--dt-card-hsl` | `#161618` / `240 3.7% 8.8%` | `#161618` / `240 4.3% 9.0%` | Composer pill & card surface |
| `--dt-elevated` | `#242424` | `#1f1f23` | Elevated cards / track / hover surface |
| `--dt-popover` / `--dt-popover-hsl` | `#1a1a1a` / `0 0% 10.2%` | `#18181b` / `240 5.9% 10%` | Popovers & menus |
| `--dt-user-bubble` | `#1e1e21` | `#18181b` | Right-aligned user message bubble |
| `--dt-fg-1` / `--dt-foreground` | `#ffffff` | `#f4f4f5` | Primary body text (keep `--dt-primary: #ffffff` as accent) |
| `--dt-fg-2` | `#d4d4d8` | `#a1a1aa` | Secondary text |
| `--dt-fg-3` / `--dt-muted-foreground` | `#a1a1aa` | `#71717a` | Tertiary / muted metadata & placeholders |
| `--dt-border` / `--dt-line` | `#202023` / `rgb(255 255 255 / 0.08)` | `rgba(255, 255, 255, 0.08)` (HSL/border synced) | 1px hairline border everywhere |
| `--dt-pill-bg` *(new)* | — | `#222226` | Resting composer pill bg (`Access: Standard ▾`, `Workspace ▾`) |
| `--dt-stop-bg` *(new)* | — | `#27272a` | Streaming Stop button (`28×28`) background |
| `--dt-timeline-inset` *(new)* | — | `#121215` | Expanded vertical activity timeline inset surface |
| `--dt-receipt-bg` *(new)* | — | `#141417` | Inline receipt card surface (model-context / bot / automation) |
| `--dt-header-h` / `--shell-titlebar-h` | `56px` | `40px` | Top header & sidebar header height |

### Other Token / Stylesheet Fixes
- `frontend/desktop/tailwind.config.cjs`:
  - Replace `ring: '0 0 0 4px rgb(217 119 87 / 0.20)'` (line 174, stale terracotta halo!) with monochrome `'0 0 0 4px rgb(255 255 255 / 0.14)'` (or `rgb(var(--dt-ring-rgb) / 0.16)`).
  - Register `pill`, `stop-bg`, `timeline-inset`, and `receipt-bg` under `theme.extend.colors` if used as Tailwind classes.
- `frontend/desktop/src/styles/chat.css`:
  - Remove `#2ecc71 / #2bd6a3 / #37b7ff` gradient in `.model-effort-slider` (lines 172, 199); replace with solid monochrome `var(--dt-primary)` (`#ffffff`) over `var(--dt-stop-bg)` (`#27272a`).
  - Remove `.activity-dots .dot` warm tint if `--dt-warning` is anything other than monochrome zinc; keep neutral `#a1a1aa` pulse.
  - Update `.august-message-list, .august-composer-slot, .august-empty-state-content, .august-transcript-column` `max-width` from `48rem` (`768px`) to `47.5rem` (`760px`), and `.august-composer` `border-radius` from `1.625rem` to `14px`.

---

## 5. Disjoint File Ownership Map (3 Parallel Builders)

To let all three builders (`composer`, `shell`, `canvas`) work in parallel with **zero merge conflicts**, every file in `frontend/desktop/src` is owned by **at most one** slice. Shared files are split by assigning a single owner (or deferred integration) below.

### Shared-File Contention & Resolution

| Shared File | Slices needing changes | Assigned Owner | How other slices integrate cleanly |
|---|---|---|---|
| `styles/tokens.css` & `tailwind.config.cjs` | All 3 (`palette` tokens) | **shell** (or coordinator pre-step) | Coordinator/shell lands the token table in §4 first, OR `shell` owns `tokens.css` + `tailwind.config.cjs` and defines `--dt-pill-bg`, `--dt-stop-bg`, `--dt-timeline-inset`, `--dt-receipt-bg`, `--shell-titlebar-h: 40px` upfront. |
| `styles/chat.css` & `styles/signal-desk.css` | All 3 | **Avoid CSS cross-edits:** `shell` owns `signal-desk.css` (shell/sidebar/titlebar rules) and `chat.css` layout rules; `composer` styles its new pill/popovers via Tailwind token classes in `composer/*` (and only removes `.composer-foot` / `.model-effort-slider` gradient if coordinated, or `shell` drops those 2 blocks); `canvas` styles timeline/receipts/bubbles via Tailwind token classes in `message/*` & `components/chat/*`. | If both `composer` and `canvas` avoid editing `chat.css` / `signal-desk.css` directly (or `shell` alone cleans up `.sd-app .composer` / `.sd-app .user-bubble` / `.sd-app .ledger` overrides in `signal-desk.css` + `chat.css`), there are **zero** CSS file collisions. |
| `sections/chat/ChatThread.tsx` | `composer`, `canvas` | **canvas** | `canvas` owns `ChatThread.tsx` (empty state props, column width, `ChatThreadMessagePane` wiring). `composer` exposes any new props on `ChatThreadComposer` with backwards-compatible defaults (or `ChatThreadComposer` reads `localServerRow` / `useWorkspacesStore` / `setWorkbenchMode` directly so `ChatThread.tsx` needs zero prop changes from `composer`). |
| `components/shell/ChatLayout.tsx` | `shell`, `canvas` (`WakeUpBriefCard`) | **shell** | `shell` owns `ChatLayout.tsx` (sidebar collapse, titlebar wiring, removing `<WakeUpBriefCard />` mount per owner spec). `canvas` does not touch `ChatLayout.tsx`. |
| `sections/chat/ChatThreadMessagePane.tsx` | `canvas` only | **canvas** | `canvas` owns `ChatThreadMessagePane.tsx` (removing `ScrollToTopButton` if desired, filtering `[SUBAGENT_COMPLETE]`, wiring timeline). |

### Disjoint Ownership Table

| Slice | Owned Files (exclusive write access) |
|---|---|
| **`composer`** | `src/sections/chat/ChatThreadComposer.tsx`<br>`src/sections/chat/ComposerControls.tsx`<br>`src/sections/chat/composer/ComposerToolbar.tsx`<br>`src/sections/chat/composer/ModelEffortMenu.tsx`<br>`src/sections/chat/composer/ComposerWorkspaceChips.tsx`<br>`src/sections/chat/composer/ComposerActionsMenu.tsx`<br>`src/sections/chat/composer/ContextLengthMenu.tsx` *(new dedicated `[16k ▾]` popover)*<br>`src/sections/chat/composer/useComposerPopovers.ts`<br>`src/components/chat/WorkbenchModeSelector.tsx`<br>`src/components/chat/SandboxModeSelector.tsx`<br>`src/components/chat/HarnessModeChip.tsx` |
| **`shell`** | `src/styles/tokens.css`<br>`tailwind.config.cjs`<br>`src/styles/chat.css` *(removes `.model-effort-slider` gradient, updates `--shell-titlebar-h` / `.august-composer` radius / `47.5rem` column)*<br>`src/styles/signal-desk.css` *(updates `.sd-app` shell/sidebar/titlebar/composer/user-bubble/ledger overrides)*<br>`src/components/shell/ChatLayout.tsx` *(removes `WakeUpBriefCard` mount too)*<br>`src/components/shell/ChatTitlebar.tsx`<br>`src/components/shell/RightDrawerLauncher.tsx`<br>`src/components/shell/RightDrawer.tsx`<br>`src/components/shell/SessionSidebar.tsx`<br>`src/components/sidebar/SessionList.tsx`<br>`src/components/sidebar/SessionListNav.tsx`<br>`src/components/sidebar/SessionRow.tsx`<br>`src/components/sidebar/FolderTree.tsx`<br>`src/components/sidebar/BotsRail.tsx`<br>`src/components/ui/user-dropdown.tsx`<br>`src/sections/runs/AutomationsSection.tsx` *(fix `open={confirmState.open}` P0 bug + integrate into `Bots & Runs`)*<br>`src/components/workspace/WorkspaceShell.tsx` *(settings rail width tweak)* |
| **`canvas`** | `src/sections/chat/ChatThread.tsx`<br>`src/sections/chat/ChatEmptyState.tsx`<br>`src/sections/chat/ChatThreadMessagePane.tsx`<br>`src/sections/chat/MessageBubble.tsx`<br>`src/sections/chat/makeStreamHandlers.ts` *(add `parseSubagentReceipt` guard)*<br>`src/sections/chat/stream/session-history.ts`<br>`src/sections/chat/message/UserMessageBubble.tsx`<br>`src/sections/chat/message/AssistantMessageContent.tsx`<br>`src/sections/chat/message/AssistantBlockTimeline.tsx`<br>`src/components/chat/ActivityGroup.tsx`<br>`src/components/chat/SubagentDelegateRow.tsx`<br>`src/components/chat/DisclosureRow.tsx`<br>`src/components/chat/ModelContextChip.tsx`<br>`src/components/chat/BotReceiptChip.tsx`<br>`src/components/chat/AutomationReceiptChip.tsx`<br>`src/components/chat/TurnProvenanceChip.tsx`<br>`src/components/chat/ScrollToTopButton.tsx`<br>`src/lib/tool-summary.ts` |

---

## 6. Conflicts with `docs/agent/product.md` & Open Questions

The following rules in [docs/agent/product.md](../agent/product.md) conflict with or constrain the owner's spatial redesign spec. Each item lists the exact conflict and a proposed resolution for `@coordinator`:

1. **Subagent launches inside the single collapsible pill (`product.md` § Chat surface vs owner spec):**
   - *Rule in `product.md`:* "A call that is waiting for approval, or carries an action widget, stays on its own (an interactive control must be visible and clickable) — and so do a subagent launch and `update_state`. A run of ONE is not a group."
   - *Owner spec:* "all tool calls/file reads/thinking/subagent runs fold into ONE single-line collapsible pill (`Read 3 files · Ran 2 sub-agents ▾`) expanding to a vertical timeline in a `#121215` inset".
   - *Proposed resolution:* Fold **settled** subagent runs (alongside thinking, file reads, commands, edits) into the single `ActivityGroup` pill (`"Read 3 files · Ran 2 sub-agents ▾"`), while keeping **approval-waiting** calls, interactive **action widgets** (`ClarifyTool`, `ProviderSetupWidget`, `IntegrationSetupWidget`, `SecretRequestWidget`), and **currently running/live-steerable** subagent launches visible outside (or auto-expanded) until settled. Update `product.md` § Chat surface to document that settled subagent runs join the run summary.

2. **Failed runs & `DisclosureRow` lazy mounting (`product.md` § Chat surface):**
   - *Rule in `product.md`:* "A run containing a FAILURE is still one line, but it **opens by default** and carries a 'failed' marker... A DISCLOSURE OWNS ITS BODY, and the body mounts lazily."
   - *Proposed resolution:* Keep both invariants intact inside the redesigned `ActivityGroup` and `#121215` timeline inset: a turn/run with any tool/subagent failure still renders one summary pill, carries the `failed` marker, and defaults to `expanded = true`; nested tool/subagent rows inside the timeline keep using `DisclosureRow` with lazy body mounting.

3. **Thinking + tool runs combined vs `A run of ONE is not a group`:**
   - *Rule in `product.md`:* "A run of ONE is not a group."
   - *Owner spec:* All tool calls / file reads / thinking / subagent runs fold into ONE single-line collapsible pill.
   - *Proposed resolution:* When a turn has `≥ 1` thinking block AND `≥ 1` tool/subagent call (total process steps ≥ 2), collapse them together into the single `ActivityGroup` pill. When a turn has *only* 1 tool call and no thinking (or *only* thinking), allow a single compact collapsible line so the transcript never shows an uncollapsed thinking wall.

4. **Top header `Click title to edit` vs `Session menu` (`Rename / Open workspace folder / Copy workspace path`):**
   - *Owner spec:* Top header thread title (`truncate 420px`) is click-to-edit; no breadcrumbs or dropdown clutter.
   - *Proposed resolution:* Make the titlebar thread title click-to-edit inline (replacing `DropdownMenu`), and ensure `Open workspace folder` + `Copy workspace path` remain accessible in the composer `[folder Workspace ▾]` popover header/footer so no desktop affordance is lost.

5. **Artifacts & Docs in top-right drawer toggle vs `/artifacts` and `/docs` routes:**
   - *Owner spec:* "Artifacts and Docs become compact icon buttons in the top-right drawer toggle".
   - *Proposed resolution:* Place compact `Artifacts` (highlighted when `artifactsCount > 0`) and `Docs` icon buttons beside the right-drawer toggle in `ChatTitlebar` (opening the Artifacts drawer section / `/artifacts` and `/docs` or docs drawer). Keep `/artifacts` and `/docs` routes working for deep links.

6. **Settings route vs modal & `LOCAL_CTX_CHOICES` `64k` preset:**
   - *Observed in live app:* Settings is a full-screen route (`/settings/:section`) with 5 visible rail items (`General`, `Models & Local Runtime`, `Memory & Skills`, `Permissions & Workspace`, `App & Updates`), not a modal. Keeping it as a route (with `220px` rail width so labels don't truncate) matches `ChatLayout.tsx`.
   - *Question for coordinator:* Verify `LOCAL_CTX_CHOICES` in `frontend/desktop/src/api/api-client/local-models.ts` includes `65536` (`64k`) — screenshot `06` showed `4k 8k 16k 32k 64k`, confirming `64k` is already in `LOCAL_CTX_CHOICES`, and confirm whether `Access: Standard` maps to `guardMode='edit'` + `sandboxMode='workspace-write'` or `guardMode='ask'` + `sandboxMode='workspace-write'` by default.
