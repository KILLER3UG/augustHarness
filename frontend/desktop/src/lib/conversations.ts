/* ── Conversation scripts for the update animation ──────────────────── */
/* Pure data + a builder. The app no longer plays a blocking launch        */
/* conversation (the workspace renders immediately; the backend bootstrap  */
/* is reported by BackendStatusPill), so the only remaining script is the  */
/* post-update "what's new" chat, fed from the real changelog.             */

export type Beat =
  | { role: 'user'; text: string }
  | {
      role: 'assistant';
      text: string;
      /** Compact bullet list rendered under the text (update conversation). */
      bullets?: string[];
      /** Hold this beat until the backend is ready (the closing line). */
      gate?: 'ready';
    };

export interface ConversationScript {
  id: string;
  beats: Beat[];
}

/* ── Update conversation ────────────────────────────────────────────── */

const UPDATE_INTROS = [
  'what changed while I was away?',
  'give me the update',
  'what’s new?',
  'fill me in',
  'what did I miss?',
  'update me',
  'what’s different now?',
  'what landed?',
  'brief me',
  'anything new since last time?',
  'what got better?',
  'show me the changelog',
];

const UPDATE_LEADS = [
  (v: string) => `Updated to ${v}. The short version:`,
  (v: string) => `You’re on ${v} now — here’s what’s new:`,
  (v: string) => `${v} is in. Highlights:`,
  (v: string) => `Fresh build ${v}. The headlines:`,
  (v: string) => `Welcome back on ${v} — what changed:`,
  (v: string) => `${v} shipped. The quick tour:`,
];

const UPDATE_OUTROS = [
  'anything I need to do?',
  'do I have to do anything?',
  'what do I need to know?',
  'anything for me to do?',
  'should I change anything?',
  'any action needed on my end?',
];

const UPDATE_CLOSERS = [
  'Nothing — you’re on the new build. Open What’s New anytime for the full list.',
  'All set. Full release notes live in What’s New.',
  'No action needed. Tap What’s New for every detail.',
  'You’re good. The complete changelog is one click away in What’s New.',
];

function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

/** Build an update conversation from real changelog bullets (already */
/** cleaned, ≤ ~5 short lines). Framing lines are randomized. */
export function buildUpdateScript(version: string, bullets: string[]): ConversationScript {
  const v = version.startsWith('v') ? version : `v${version}`;
  const items = bullets.length ? bullets : ['Performance and reliability improvements.', 'A handful of bug fixes.'];
  return {
    id: `u-${Date.now()}`,
    beats: [
      { role: 'user', text: pick(UPDATE_INTROS) },
      { role: 'assistant', text: pick(UPDATE_LEADS)(v), bullets: items },
      { role: 'user', text: pick(UPDATE_OUTROS) },
      { role: 'assistant', text: pick(UPDATE_CLOSERS), gate: 'ready' },
    ],
  };
}
