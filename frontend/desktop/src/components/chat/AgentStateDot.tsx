/* ── AgentStateDot ─ one honest status glyph, every agent surface ────── */
/* The vocabulary (and the raw-status mapping) lives in lib/agent-state.ts;
 * this file renders it. Every glyph that is not a proven outcome renders as
 * the dashed ring, so a half-answer or a lost worker can never borrow a
 * success or failure color. */

import { cn } from '@/lib/utils';
import { agentStateLabel, type AgentDotState } from '@/lib/agent-state';

/** The dashed ring: missing evidence without claiming progress or completion. */
const MISSING_EVIDENCE_RING =
  'size-2 rounded-full border border-dashed border-warning bg-transparent';

const STATE_CLASS: Record<AgentDotState, string> = {
  working: 'size-2 rounded-full bg-info animate-pulse motion-reduce:animate-none',
  queued: 'size-2 rounded-full bg-muted-foreground/50',
  attention: 'size-2 rounded-full bg-warning',
  done: 'size-2 rounded-full bg-success',
  failed: 'size-2 rounded-full bg-danger',
  interrupted: 'size-2 rounded-full bg-muted-foreground/60',
  unverified: MISSING_EVIDENCE_RING,
  unconfirmed: MISSING_EVIDENCE_RING,
  idle: 'size-2 rounded-full bg-muted-foreground/40',
};

export function AgentStateDot({ state, className }: { state: AgentDotState; className?: string }) {
  const label = agentStateLabel(state);
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      data-agent-state={state}
      className={cn('inline-block shrink-0 align-middle', STATE_CLASS[state], className)}
    />
  );
}
