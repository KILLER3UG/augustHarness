/**
 * Copy for the `subagentFanout` frame — the fleet-level receipt for one
 * sub-agent dispatch.
 *
 * The subagent LANE rows are per-worker. This is the one line that answers the
 * question the rows cannot: "why did only 6 of the 8 lanes I asked for start?"
 * It carries the children cap, the APPLIED concurrency (what the process pool
 * actually ran, which can be lower than what the parent asked for) and the
 * observed peak.
 *
 * Register matches the activity line and the recovery notice: plain,
 * user-facing, no internal token names, and never empty. A dispatch whose
 * numbers are missing still says what it did, because a silent drop is the
 * defect this replaces.
 */

/** One line for a dispatch that is still running. */
export function fanoutRunningMessage(
  requested?: number,
  concurrency?: number,
): string {
  const lanes = typeof requested === 'number' && requested > 0 ? `${requested} ` : '';
  const at = typeof concurrency === 'number' && concurrency > 0 ? `, ${concurrency} at a time` : '';
  return `Running ${lanes}sub-agent${typeof requested === 'number' && requested === 1 ? '' : 's'}${at}`;
}

/**
 * One line for a finished dispatch.
 *
 * The APPLIED concurrency is only worth naming when it differs from what was
 * asked for — that gap is the bound the receipt exists to expose, and printing
 * "4 at a time" when the parent asked for 4 is noise.
 */
export function fanoutDoneMessage(data: {
  requested?: number;
  childrenStarted?: number;
  childrenPeak?: number;
  maxConcurrent?: number;
  requestedConcurrency?: number;
  reason?: string;
}): string {
  const started = data.childrenStarted;
  const peak = data.childrenPeak;
  const count =
    typeof started === 'number' && started > 0
      ? `${started} sub-agent${started === 1 ? '' : 's'}`
      : typeof data.requested === 'number' && data.requested > 0
        ? `${data.requested} sub-agent${data.requested === 1 ? '' : 's'}`
        : 'Sub-agents';

  // Applied vs requested: only the gap is information.
  const applied = data.maxConcurrent ?? data.childrenPeak;
  const asked = data.requestedConcurrency;
  const capped =
    typeof applied === 'number' && typeof asked === 'number' && applied < asked
      ? ` — capped at ${applied} at a time (asked for ${asked})`
      : '';

  const peaked = typeof peak === 'number' && peak > 1 && !capped ? ` (peak ${peak})` : '';
  return `Ran ${count}${capped}${peaked}`;
}