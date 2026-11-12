/**
 * One owner for how REASONING is presented, shared by the two disclosure
 * chrome variants (2026-10-03 phase 6):
 *
 *   • ThoughtStep          — rail variant, used in the main transcript
 *   • ThinkingDisclosure   — plain variant, used inside subagent panels
 *
 * Both use the same clamp window and the same collapsed-title rule
 * (a settled thought reads as "Thinking complete" + duration + activity
 * rollup; a running one carries the elapsed clock).
 * The chrome differs by context and is deliberately not merged — the
 * streaming auto-open/collapse behavior of the subagent variant is load
 * bearing there and unrelated to the rail's pack-level collapse
 * (ActivitySummary owns that).
 */

/** Lines of prose visible before the clamp + fade kick in. */
export const THOUGHT_CLAMP_LINES = 6;

/** Rough char equivalent of THOUGHT_CLAMP_LINES (~80 chars/line). The
 *  overflow measurement can read 0/stale when a whole-turn burst arrives
 *  at once, so a long thought must also truncate by length alone. */
export const THOUGHT_CLAMP_CHARS = 480;

/** Distilled summary rows a settled thought renders. The card is the
 *  reference's activity list — ONE row per whole thought, each collapsed to
 *  its summary line and expandable in place to that thought's own reasoning.
 *  This is the ceiling on rows, not a "show only one": a turn with many
 *  distinct thoughts still bounds the transcript to a readable stack.
 *  Expansion reveals the row's reasoning, never another row's and never the
 *  whole chain. */
export const THOUGHT_SUMMARY_LINES_FULL = 60;

/** Summary LINES the reference's collapsed thinking card shows — one line
 *  per segment of the block's reasoning, covering ALL the thoughts, never
 *  the raw reasoning itself. Claude's collapsed card stacks ~2-4 of these;
 *  the card's one disclosure owns the full chain. Segments past the cap are
 *  counted and named as a "+N more" receipt rather than pretended away. */
export const THOUGHT_SUMMARY_PREVIEW_LINES = 4;

/** "1m 06s" / "6s" — the elapsed formatter both variants render. */
export function formatThoughtDuration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const min = Math.floor(total / 60);
  const sec = total % 60;
  if (min === 0) return `${sec}s`;
  return `${min}m ${String(sec).padStart(2, '0')}s`;
}

/**
 * The collapsed title for a thought row.
 * - running: no suffix beyond the elapsed clock (the caller passes the
 *   live elapsed ms; omit it for the bare title)
 * - settled: "Thinking complete", with the measured duration kept after it
 *   when the caller has one. Kimi's wording for the settled state, Claude's
 *   title-plus-duration header.
 */
export function thoughtCollapsedTitle(opts: {
  generating?: boolean;
  elapsedMs?: number;
  durationMs?: number;
  omitDurationLabel?: boolean;
}): string {
  const { generating, elapsedMs, durationMs, omitDurationLabel } = opts;
  if (omitDurationLabel) return 'Thinking complete';
  if (generating) {
    return elapsedMs && elapsedMs >= 1000
      ? `Thinking · ${formatThoughtDuration(elapsedMs)}`
      : 'Thinking';
  }
  return durationMs && durationMs >= 1000
    ? `Thinking complete · ${formatThoughtDuration(durationMs)}`
    : 'Thinking complete';
}