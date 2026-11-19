/* ── host.ts — which host mounted a learning surface ───────────────────── */
/* The Learning hub route is FOLDED INTO the Memory & Skills settings (Part D
 * diet), so every learning surface now has exactly one host. The type stays
 * because the sections' props still carry it — a second host cannot appear
 * by accident, and `inboxRoute` is the ONE link that leaves a surface. */

export type LearningHost = 'settings';

/** Where the Review Inbox lives. A cross-reference that leaves the surface
 * lands in the SAME host the reader is in — which is always the settings
 * overlay now. */
export function inboxRoute(_host: LearningHost): string {
  return '/settings/skills';
}
