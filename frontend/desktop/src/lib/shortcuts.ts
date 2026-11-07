/**
 * The single source of truth for keyboard shortcuts.
 *
 * This file exists because the reference modal was a hand-maintained list that
 * drifted: Ctrl+N had worked for months and was never in it (found 2026-10-04).
 * App.tsx dispatches the app-level bindings through `resolveShortcut` (each
 * carries an `action`); ShortcutsModal renders the same array, so a binding
 * cannot ship undocumented. Composer- and surface-local keys are listed here
 * too (their handlers stay where they are — this is the registry, not a
 * dispatcher for those).
 *
 * `combo` is the normalized lookup key: ctrl/⌘ + alt + shift + the physical key,
 * e.g. 'ctrl+k', 'ctrl+shift+space'. ⌘ is normalized onto `ctrl` (they are
 * interchangeable primary modifiers). `comboFor` powers the modal's "press a
 * key" search; `resolveShortcut` is the app-level matcher.
 */

/** What App.tsx does when an app-level shortcut fires. */
export type AppShortcutAction =
  | 'palette'
  | 'new-chat'
  | 'toggle-sidebar'
  | 'toggle-right-drawer'
  | 'settings'
  | 'shortcuts';

export interface ShortcutBinding {
  /** Display chips, left to right (e.g. ['Ctrl', 'K']). */
  keys: string[];
  label: string;
  /** Normalized combo for lookup; omit for plain keys. */
  combo?: string;
  /** Windows uses Ctrl where macOS uses ⌘; the modal swaps the first chip. */
  macPrimary?: boolean;
  /** App-level handler id; present on every binding App.tsx dispatches. */
  action?: AppShortcutAction;
  /** Keep firing while focus is inside an input/textarea (palette and pane
   *  toggles do; Ctrl+N and the punctuation keys do not). */
  allowWhileTyping?: boolean;
  /** Where the binding fires. 'global' overlaps every surface; the others
   *  are surface-local, which is what makes two bindings with the same
   *  combo (composer Ctrl+Enter vs git-panel Ctrl+Enter) NOT a conflict. */
  scope?: ShortcutScope;
}

export type ShortcutScope = 'global' | 'composer' | 'transcript' | 'git' | 'approval';

export interface ShortcutGroup {
  heading: string;
  items: ShortcutBinding[];
}

/** App-level bindings: the registry IS the dispatcher table (App.tsx calls
 *  `resolveShortcut` and switches on `action`), so a binding cannot ship
 *  unwired or undocumented. */
export const APP_SHORTCUTS: ShortcutBinding[] = [
  { keys: ['Ctrl', 'K'], label: 'Command palette', combo: 'ctrl+k', action: 'palette', allowWhileTyping: true },
  { keys: ['Ctrl', 'P'], label: 'Command palette (alias)', combo: 'ctrl+p', action: 'palette', allowWhileTyping: true },
  { keys: ['Ctrl', 'N'], label: 'New chat', combo: 'ctrl+n', action: 'new-chat' },
  { keys: ['Ctrl', 'B'], label: 'Toggle sidebar', combo: 'ctrl+b', action: 'toggle-sidebar', allowWhileTyping: true },
  { keys: ['Ctrl', 'J'], label: 'Toggle right panel', combo: 'ctrl+j', action: 'toggle-right-drawer', allowWhileTyping: true },
  { keys: [','], label: 'Settings', combo: ',', action: 'settings', scope: 'global' },
  { keys: ['?'], label: 'Keyboard shortcuts', combo: '?', action: 'shortcuts', scope: 'global' },
];

/** Everything the reference modal documents, grouped. */
export const SHORTCUT_GROUPS: ShortcutGroup[] = [
  { heading: 'Global', items: APP_SHORTCUTS },
  {
    heading: 'Composer',
    items: [
      { keys: ['Enter'], label: 'Send message · steer the running turn', scope: 'composer' },
      { keys: ['Shift', 'Enter'], label: 'New line', scope: 'composer' },
      { keys: ['Ctrl', 'Enter'], label: 'Send (hard send) · while streaming: queue the draft to run after the turn', scope: 'composer' },
      { keys: ['Ctrl', 'Shift', 'Space'], label: 'Focus composer from anywhere', scope: 'composer' },
      { keys: ['Ctrl', 'Shift', 'P'], label: 'Toggle live markdown preview', scope: 'composer' },
      { keys: ['↑', '↓'], label: 'Navigate @mention / command list', scope: 'composer' },
      { keys: ['Esc'], label: 'Stop the running turn · close popovers', scope: 'composer' },
    ],
  },
  {
    heading: 'Approval cards',
    items: [{ keys: ['1', '2', '3'], label: 'Choose permission option', scope: 'approval' }],
  },
  {
    heading: 'Git panel',
    items: [{ keys: ['Ctrl', 'Enter'], label: 'Commit', scope: 'git' }],
  },
  {
    heading: 'Transcript',
    items: [
      { keys: ['Ctrl', 'F'], label: 'Find in conversation', scope: 'transcript' },
      { keys: ['PageUp'], label: 'Release the follow-scroll (read older turns)', scope: 'transcript' },
    ],
  },
];

/** Normalized combo string for a keyboard event: 'ctrl+shift+space', 'escape', ',' …
 *  ⌘ (metaKey) normalizes onto `ctrl`: the two are interchangeable primary
 *  modifiers (Ctrl chips render as ⌘ on macOS at display time). */
export function comboFor(e: KeyboardEvent): string {
  const parts: string[] = [];
  if (e.ctrlKey || e.metaKey) parts.push('ctrl');
  if (e.altKey) parts.push('alt');
  // Shift counts when it is a MODIFIER (Ctrl+Shift+Space differs from
  // Ctrl+Space). When the key itself is the shifted PRINT of a punctuation
  // character ('?', '!', '+'), shift is already baked into the key, so it
  // would only add a redundant segment.
  const key = e.key.toLowerCase();
  const shiftedPrint = e.shiftKey && key.length === 1 && !/[a-z0-9 ]/.test(key);
  if (e.shiftKey && !shiftedPrint) parts.push('shift');
  parts.push(key);
  return parts.join('+');
}

/** The combo with its shift segment dropped (for '?' arriving as shift+/). */
function withoutShift(combo: string): string | undefined {
  return combo.includes('+shift+') ? combo.replace('+shift+', '+') : undefined;
}

/** The app-level binding a keyboard event fires, or undefined. */
export function resolveShortcut(e: KeyboardEvent): ShortcutBinding | undefined {
  const combo = comboFor(e);
  return APP_SHORTCUTS.find((b) => b.combo === combo);
}

/** Find the binding a typed combo belongs to (keystroke search). */
export function findByCombo(combo: string): ShortcutBinding | undefined {
  const all = SHORTCUT_GROUPS.flatMap((g) => g.items);
  const direct = all.find((b) => b.combo === combo);
  if (direct) return direct;
  // Guard the loose compare: combo-less (surface-local) bindings have
  // b.combo === undefined, and `undefined === undefined` would match them.
  const bare = withoutShift(combo);
  if (bare === undefined) return undefined;
  return all.find((b) => b.combo === bare);
}

/** True on macOS — where the registry's Ctrl chip renders as ⌘. */
export function isMacPlatform(): boolean {
  if (typeof navigator === 'undefined') return false;
  return /Mac|iPhone|iPad/.test(navigator.userAgent);
}

/** Display chips for the running platform: 'Ctrl' becomes '⌘' on macOS.
 *  The registry stores the Windows spelling because every handler here
 *  normalizes ⌘ onto ctrl — the platform difference is display-only. */
export function platformKeys(keys: readonly string[]): string[] {
  if (!isMacPlatform()) return [...keys];
  return keys.map((k) => (k === 'Ctrl' ? '⌘' : k));
}

/**
 * Same combo bound where two bindings can fire together. A global binding
 * overlaps every scope; two surface-local bindings in DIFFERENT scopes
 * (composer Ctrl+Enter vs git-panel Ctrl+Enter) are deliberately disjoint.
 * Empty in a healthy registry — the ShortcutsModal surfaces it when not.
 */
export function findShortcutConflicts(): { combo: string; labels: string[] }[] {
  const all = SHORTCUT_GROUPS.flatMap((g) =>
    g.items.filter((b) => b.combo).map((b) => ({ combo: b.combo!, label: b.label, scope: b.scope ?? 'global' })),
  );
  const conflicts: { combo: string; labels: string[] }[] = [];
  const byCombo = new Map<string, typeof all>();
  for (const b of all) {
    const list = byCombo.get(b.combo) ?? [];
    list.push(b);
    byCombo.set(b.combo, list);
  }
  for (const [combo, list] of byCombo) {
    if (list.length < 2) continue;
    const scopes = new Set(list.map((b) => b.scope));
    const overlaps = list.length > 1 && (scopes.has('global') || list.some((b) => list.some((o) => o !== b && o.scope === b.scope)));
    if (overlaps) conflicts.push({ combo, labels: list.map((b) => b.label) });
  }
  return conflicts;
}
