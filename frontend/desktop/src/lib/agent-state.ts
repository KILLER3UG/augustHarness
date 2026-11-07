/* ── Agent status vocabulary ─────────────────────────────────────────── */
/* The vocabulary is epistemic on purpose: a glyph states what the UI can
 * PROVE, nothing more.
 *
 *  working     live and making progress (pulses)
 *  queued      accepted, not started
 *  attention   needs the user (approval, input)
 *  done        a proven good outcome
 *  failed      a proven bad outcome (transcript-derived, not a hunch)
 *  interrupted stopped on purpose
 *  unverified  live evidence went stale — proves only missing evidence,
 *              never idle/work/completion (tool `stalled`, worker `stalling`)
 *  unconfirmed the run settled without a clean verdict (`partial`) — the
 *              missing-evidence glyph, never a check
 *  idle        nothing running
 *
 * This module is status logic, not rendering, so the glyph component can
 * stay the only export of its own file (fast refresh).
 */

export type AgentDotState =
  | 'working'
  | 'queued'
  | 'attention'
  | 'done'
  | 'failed'
  | 'interrupted'
  | 'unverified'
  | 'unconfirmed'
  | 'idle';

const STATUS_MAP: Record<string, AgentDotState> = {
  // tool-call status (MessageBlockToolCall)
  running: 'working',
  done: 'done',
  error: 'failed',
  stalled: 'unverified',
  // subagent + run status (SubagentBlockState, subagent_runs)
  pending: 'queued',
  completed: 'done',
  recovered: 'done',
  failed: 'failed',
  cancelled: 'interrupted',
  partial: 'unconfirmed',
  blocked: 'attention',
  stalling: 'unverified',
  // workbench run status (RunsPage)
  streaming: 'working',
  'awaiting_approval': 'attention',
};

/** Map a raw status string onto the shared glyph state, or null when the
 * caller must decide (an unknown status is not a guessable one). */
export function agentDotState(raw: string | undefined | null): AgentDotState | null {
  if (!raw) return null;
  return STATUS_MAP[raw] ?? null;
}

export function agentStateLabel(state: AgentDotState): string {
  switch (state) {
    case 'working':
      return 'Working';
    case 'queued':
      return 'Queued';
    case 'attention':
      return 'Needs your input';
    case 'done':
      return 'Completed';
    case 'failed':
      return 'Failed';
    case 'interrupted':
      return 'Interrupted';
    case 'unverified':
      return 'No live evidence — last known: running';
    case 'unconfirmed':
      return 'Ended without a proven outcome';
    case 'idle':
      return 'Idle';
  }
}
