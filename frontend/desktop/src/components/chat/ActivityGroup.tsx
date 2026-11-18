/* ── ActivityGroup — one line for a run of tool calls ────────────────── */
/* The reference transcript collapses a run of tool calls into a single
 * activity line — a warm dot, one phrase, the elapsed time, a chevron — and
 * reveals the individual calls in a bordered card when it is opened.
 *
 * Every kind of tool work joins the line: reads, commands, edits, memory
 * writes, plain tool calls. A mixed run (a read, a command, a read) is one
 * thing that happened, and the phrase names each kind.
 *
 * A run that FAILED is grouped too, and the line is marked and OPENS: a
 * failure behind a collapsed line is one nobody sees.
 *
 * The grouping is a RENDER-TIME arrangement, not a new persisted block kind:
 * the transcript still stores one block per tool call, so a reload produces
 * the same grouping and nothing in the backend has to know about it.
 *
 * The card's first row IS the summary line. The children sit under it,
 * separated by hairlines, so a run reads as one thing with a list inside it
 * rather than as a stack of separate rows that happen to be adjacent. */

import { Children, useId, type ReactNode } from 'react';
import { AlertCircle, ChevronDown } from 'lucide-react';
import { cn, fmtElapsed } from '@/lib/utils';
import { Button } from '@/components/ui/button';

export function ActivityGroup({
  summary,
  elapsedMs,
  running,
  failed,
  expanded,
  onToggle,
  groupKey,
  children,
}: {
  /** The phrase for the whole run — "Read 3 files · Ran 2 sub-agents". */
  summary: string;
  /** Total time for the run, in ms. Hidden under a second: a sub-second
   *  duration reads as noise, and the reference shows whole seconds. */
  elapsedMs?: number;
  running: boolean;
  /** The run contains a failure. The line is MARKED, and the group opens
   *  on it: a failure behind a collapsed line is one nobody sees. */
  failed?: boolean;
  expanded: boolean;
  onToggle: (next: boolean) => void;
  /** Stable key for the manual expand override. */
  groupKey: string;
  children: ReactNode;
}) {
  // The collapsed group is the state a screen reader navigates, so the trigger
  // has to resolve to a region that exists in BOTH states — the pairing
  // DisclosureRow owns. The region wrapper stays mounted (empty while closed)
  // while the timeline body mounts lazily on expand.
  const uid = useId();
  const triggerId = `${uid}-group-trigger`;
  const regionId = `${uid}-group-region`;
  const elapsed =
    typeof elapsedMs === 'number' && elapsedMs >= 1000 ? fmtElapsed(elapsedMs) : null;
  const rows = Children.toArray(children);

  return (
    <div
      className="my-1 flex flex-col items-start"
      data-slot="activity-group"
      data-group-key={groupKey}
      data-open={expanded}
    >
      <Button
        variant="bare"
        type="button"
        className={cn(
          'inline-flex max-w-full items-center gap-2 rounded-full border border-[var(--dt-line)] bg-[var(--dt-pill-bg)] px-3 py-1 text-xs font-medium text-[var(--dt-fg-2)] transition-colors hover:border-[var(--dt-line-strong)] hover:text-[var(--dt-fg-1)]',
          failed && 'border-danger/40',
        )}
        onClick={() => onToggle(!expanded)}
        id={triggerId}
        aria-expanded={expanded}
        aria-controls={regionId}
        data-testid="activity-group-head"
      >
        <span
          className={cn(
            'size-1.5 shrink-0 rounded-full',
            failed
              ? 'bg-danger'
              : running
                ? 'animate-pulse bg-[var(--dt-primary)]'
                : 'bg-[var(--dt-fg-3)]',
          )}
          aria-hidden
        />
        <span
          className="min-w-0 truncate font-medium text-[var(--dt-fg-1)]"
          data-testid="activity-group-summary"
        >
          {summary}
        </span>
        {failed && (
          <span
            className="inline-flex shrink-0 items-center gap-1 text-2xs font-medium text-danger-fg"
            data-testid="activity-group-failed"
          >
            <AlertCircle className="size-3" aria-hidden />
            failed
          </span>
        )}
        {elapsed ? (
          <span
            className="shrink-0 font-mono text-2xs tabular-nums text-[var(--dt-fg-3)]"
            data-testid="activity-group-elapsed"
          >
            {elapsed}
          </span>
        ) : null}
        <ChevronDown
          className={cn(
            'size-3.5 shrink-0 text-[var(--dt-fg-3)] transition-transform duration-150',
            expanded && 'rotate-180',
          )}
          aria-hidden
        />
      </Button>

      <div id={regionId} role="region" aria-labelledby={triggerId} className="w-full">
        {expanded && rows.length > 0 ? (
          <div
            className="mt-2 rounded-xl border border-[var(--dt-line)] bg-[var(--dt-timeline-inset)] px-3.5 py-2.5"
            data-testid="activity-group-body"
          >
            <div className="relative flex flex-col gap-2 pl-5 before:pointer-events-none before:absolute before:bottom-1.5 before:left-2 before:top-1.5 before:w-px before:bg-[var(--dt-line)]">
              {rows.map((row, i) => (
                <div key={i} className="relative min-w-0">
                  <span
                    className="pointer-events-none absolute -left-3.5 top-2.5 size-1.5 rounded-full bg-[var(--dt-fg-3)] opacity-60"
                    aria-hidden
                  />
                  {row}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
