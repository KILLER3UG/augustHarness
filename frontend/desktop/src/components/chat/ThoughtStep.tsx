/**
 * Thought step — the model's reasoning rendered as activity rows on the
 * process rail.
 *
 * Layout: a clock icon on the threaded rail line, and beside it the
 * reference's thinking card — ONE rounded container whose hairline-divided
 * rows are each a distilled "what the model is doing" sentence. A row expands
 * in place to reveal that stretch's reasoning and nothing else; expanding one
 * row never expands another, and a collapsed row never shows raw reasoning.
 * This is the same visual language as the tool pack (ActivitySummary's
 * `activity-card-box`: border + `divide-y`), so a thought block and a command
 * block read as one row anatomy.
 *
 * The rail clock stays a static marker (the whole pack collapses via
 * ActivitySummary for compactness); a per-thought header would repeat the
 * run's duration on every block, and the activity line above already carries
 * it — "Thought for 49s" is the header's job, not each row's.
 *
 * Live vs settled (Claude-desktop activity rows, 2026-11-10): while the
 * thought is still streaming there is no settled sentence list to trace, so it
 * keeps the single rotating summary line (the shimmer follower) and shows no
 * disclosure — a chevron over nothing is a lie. It rests into the expandable
 * rows the moment it settles. Nothing auto-expands on the user's behalf.
 *
 * The operator can still opt out of the row layout entirely
 * (`august.collapseThinking = '0'`), which shows the raw prose clamped with a
 * "Show more" that unclamps it — the pre-row behaviour, kept for the plain
 * transcript the opt-out asks for.
 */

import { useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Clock, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import {
  distillThoughtSteps,
  summarizeThoughtHeader,
  type ThoughtStepLine,
} from '@/lib/process-summary';
import {
  THOUGHT_CLAMP_LINES,
  THOUGHT_CLAMP_CHARS,
  THOUGHT_SUMMARY_LINES_FULL,
} from '@/lib/thought-presentation';
import { Markdown } from '@/sections/chat/ChatMarkdown';

// Clamp constants live in lib/thought-presentation.ts — one owner shared
// with the plain (subagent) disclosure variant.

export function ThoughtStep({
  content,
  isGenerating = false,
  showFull = false,
  onToggle,
  collapsedDefault = false,
  className,
}: {
  content: string;
  isGenerating?: boolean;
  /** Prose shown in full (unclamped). Only the opt-out layout uses it: true
   *  unclamps the raw prose. The row layout expands per row and ignores it. */
  showFull?: boolean;
  onToggle?: () => void;
  /** Render the reference's expandable activity rows (the out-of-box
   *  default). Off means the opt-out layout: raw prose clamped with "Show
   *  more". */
  collapsedDefault?: boolean;
  className?: string;
}) {
  const reactId = useId();
  const panelId = `thought-step-panel-${reactId}`;
  const text = content.trim();
  const proseRef = useRef<HTMLDivElement>(null);
  const [overflowing, setOverflowing] = useState(false);
  // Which rows the user opened, keyed by row index. Independent per row: a
  // toggle writes only its own key, so opening one row never opens a sibling.
  // Nothing seeds it, so nothing auto-expands.
  const [openRows, setOpenRows] = useState<Record<number, boolean>>({});

  // Measure the natural prose height against the clamp. The inner prose is
  // never the clip container, so its scrollHeight is the full height whether
  // or not the wrapper is clamped — measuring it directly tracks root-font
  // scaling (line-height is resolved in px by the browser).
  useLayoutEffect(() => {
    const el = proseRef.current;
    if (!el) return;
    const measure = () => {
      const lh = parseFloat(getComputedStyle(el).lineHeight) || 24;
      setOverflowing(el.scrollHeight > lh * THOUGHT_CLAMP_LINES + 4);
    };
    measure();
    // ResizeObserver is absent in some test runtimes (jsdom); a single
    // measure is enough there since the prose never reflows.
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [text, showFull]);

  // The rotating live line. While the thought streams, distill the LATEST
  // sentence so the line advances with the model's progress; a settled thought
  // renders the distilled rows instead.
  // (Lives above the `!text && !isGenerating` early return — rules-of-hooks.)
  const summaryLine = useMemo(() => {
    if (isGenerating) {
      const sentences = text
        .replace(/```[\s\S]*?```/g, ' ')
        .split(/(?<=[.!?]["')\]]?)\s+|\n+/)
        .map((s) => s.trim())
        .filter(Boolean);
      const tail = sentences[sentences.length - 1] ?? '';
      return summarizeThoughtHeader(tail, true);
    }
    return summarizeThoughtHeader(text, false);
  }, [text, isGenerating]);

  // The reference's thinking rows: one per distilled sentence, each traced to
  // the reasoning it summarises (the same bytes), so a row opens onto its OWN
  // stretch without a second segmentation and without repeating the whole
  // thought under every row. A thought whose every sentence the distiller
  // filtered (pure netlist/code noise) still owes the card a row — an empty
  // body reads as a rendering bug — so it falls back to one row over the whole
  // trimmed text. A LIVE thought has no settled sentence list yet, so it
  // renders none of these; it keeps the rotating line until it settles.
  const rows = useMemo<ThoughtStepLine[]>(() => {
    if (isGenerating) return [];
    const steps = distillThoughtSteps(text);
    return steps.length > 0
      ? steps
      : [{ summary: summarizeThoughtHeader('', false), reasoning: text }];
  }, [text, isGenerating]);

  if (!text && !isGenerating) return null;

  // Opt-out layout only: clamp long thoughts whether streaming or settled —
  // once the reasoning passes the clamp height it is bounded with a fade +
  // Show more, and the live tail streams below the fold. `longThought` falls
  // back to a length check when the pixel measurement is unavailable or stale,
  // so a long thought never renders untruncated.
  const longThought = overflowing || text.length > THOUGHT_CLAMP_CHARS;
  const clamped = longThought && !showFull;
  // The single "Show more" that unclamps the raw prose belongs to the opt-out
  // layout. The row layout expands per row, so it advertises no whole-block
  // control here.
  const canReveal = !collapsedDefault && !!text && typeof onToggle === 'function';

  const clockIcon = isGenerating ? (
    <Loader2 className="process-thought-clock animate-spin" aria-hidden />
  ) : (
    <Clock className="process-thought-clock" aria-hidden />
  );

  return (
    <div
      className={cn('rail-row process-thought thought-enter', className)}
      data-slot="thought-step"
      data-generating={isGenerating ? 'true' : 'false'}
      data-expanded={showFull ? 'true' : 'false'}
      data-clamped={clamped ? 'true' : 'false'}
    >
      <span className="rail-line" aria-hidden />
      <div className="process-thought-axis" aria-hidden>
        <span className="rail-icon">{clockIcon}</span>
      </div>

      <div className="process-thought-body">
        {collapsedDefault ? (
          isGenerating ? (
            /* Live: one rotating summary line, no disclosure. Nothing behind
               it is settled enough to reveal, so there is no row to open and
               nothing is expanded on the user's behalf. */
            <div
              id={panelId}
              className="thought-summary thought-summary--live"
              data-slot="thought-summary"
            >
              <span className="thought-summary-text">
                <span className="thought-summary-line block">
                  {summaryLine || summarizeThoughtHeader('', true)}
                </span>
              </span>
            </div>
          ) : (
            /* Settled: the reference's activity card — one rounded container,
               a hairline-divided row per distilled sentence, each expanding
               its own reasoning in place. */
            <div
              id={panelId}
              className="thought-steps rounded-xl border border-border/40 bg-card/30 divide-y divide-border/20 text-xs overflow-hidden"
              data-slot="thought-steps"
            >
              {rows.slice(0, THOUGHT_SUMMARY_LINES_FULL).map((row, i) => {
                const open = !!openRows[i];
                const triggerId = `${panelId}-row-${i}-trigger`;
                const regionId = `${panelId}-row-${i}-region`;
                return (
                  <div key={i} className="thought-step-row" data-open={open}>
                    <Button
                      variant="bare"
                      type="button"
                      id={triggerId}
                      className="thought-step-row-head"
                      onClick={() => setOpenRows((v) => ({ ...v, [i]: !v[i] }))}
                      aria-expanded={open}
                      aria-controls={regionId}
                    >
                      <span className="thought-step-summary">{row.summary}</span>
                      <ChevronRight
                        className={cn('thought-step-chevron', open && 'is-open')}
                        aria-hidden
                      />
                    </Button>
                    <div
                      id={regionId}
                      role="region"
                      aria-labelledby={triggerId}
                      hidden={!open}
                      className="thought-step-detail"
                    >
                      {/* The row's own reasoning — the same bytes its summary
                          was distilled from, and only this row's. The region
                          stays mounted so `aria-controls` resolves while
                          closed, but the body mounts lazily: a collapsed row
                          carries no reasoning bytes in the DOM at all. Bounded
                          with the transcript's clamp/scroll convention so a
                          long stretch never blows up the rail. */}
                      {open ? (
                        <div className="thought-step-reasoning tool-result-scroll max-h-48 overflow-y-auto overscroll-contain">
                          <Markdown content={row.reasoning} />
                        </div>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          )
        ) : (
          <div className={cn('thought-clamp', clamped && 'is-clamped')}>
            <div
              ref={proseRef}
              id={panelId}
              className={cn(
                'process-thought-prose thought-content chat-thought-text',
                // The same one-shot fade the answer's live tail
                // uses, so streamed reasoning inks in instead of hard-popping.
                isGenerating && 'md-live-tail',
              )}
              aria-live={isGenerating ? 'polite' : undefined}
            >
              {text ? (
                <Markdown content={text} />
              ) : (
                <div className="process-thought-pending">Thinking…</div>
              )}
            </div>
            {clamped ? <div className="thought-fade" aria-hidden /> : null}
          </div>
        )}

        {canReveal ? (
          <Button
            variant="bare"
            type="button"
            className={cn('thought-toggle-btn', isGenerating && 'is-live')}
            onClick={onToggle}
            aria-expanded={showFull}
            aria-controls={panelId}
          >
            <span>{showFull ? 'Show less' : 'Show more'}</span>
            <ChevronDown
              className={cn('thought-toggle-chevron', showFull && 'is-open')}
              aria-hidden
            />
          </Button>
        ) : null}
      </div>
    </div>
  );
}
