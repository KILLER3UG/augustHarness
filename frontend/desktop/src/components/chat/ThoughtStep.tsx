/**
 * Thought step — the model's reasoning rendered as activity rows on the
 * process rail.
 *
 * Layout: a clock icon on the threaded rail line, and beside it the
 * reference's thinking card — ONE rounded container whose collapsed body is
 * the SUMMARY OF ALL THE THOUGHTS: a quiet line per segment of the
 * reasoning, distilled from the block's own paragraph breaks, never the raw
 * chain. The card is one disclosure — one chevron, one region — and opening
 * it reveals the block's full reasoning and nothing else. Nothing
 * auto-expands on the user's behalf.
 *
 * This is the same visual language as the tool pack (ActivitySummary's
 * `activity-card-box`: border + quiet stack), so a thought block and a
 * command block read as one row anatomy.
 *
 * The rail clock stays a static marker (the whole pack collapses via
 * ActivitySummary for compactness); a per-thought header would repeat the
 * run's duration on every block, and the activity line above already carries
 * it — "Thought for 49s" is the header's job, not each row's.
 *
 * Live vs settled (Claude-desktop activity rows, 2026-11-10): while the
 * thought is still streaming there is no settled sentence list to trace, so it
 * keeps the single rotating summary line (the shimmer follower) and shows no
 * disclosure — a chevron over nothing is a lie. It rests into the summary
 * card the moment it settles. Nothing auto-expands on the user's behalf.
 *
 * The operator can still opt out of the card layout entirely
 * (`august.collapseThinking = '0'`), which shows the raw prose clamped with a
 * "Show more" that unclamps it — the pre-card behaviour, kept for the plain
 * transcript the opt-out asks for.
 */

import { useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, Clock, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import {
  distillThoughtSummaries,
  splitThinkingTexts,
  summarizeThoughtHeader,
} from '@/lib/process-summary';
import {
  THOUGHT_CLAMP_LINES,
  THOUGHT_CLAMP_CHARS,
  THOUGHT_SUMMARY_PREVIEW_LINES,
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
  // The card is ONE disclosure (the reference's): collapsed it shows the
  // summary stack — a quiet line per segment of the reasoning, covering all
  // the thoughts — and the user's click reveals the raw chain. Nothing seeds
  // it open.
  const [cardOpen, setCardOpen] = useState(false);

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

  // The rotating live line. One summary for the WHOLE latest thought, never
  // one per sentence. While streaming, the in-flight `tail` is the newest
  // thought; once settled, the last whole thought is. A thought made of five
  // sentences still produces exactly one line here.
  // (Lives above the `!text && !isGenerating` early return — rules-of-hooks.)
  const summaryLine = useMemo(() => {
    const { thoughts, tail } = splitThinkingTexts(text, isGenerating);
    const latest = tail || thoughts[thoughts.length - 1] || '';
    return summarizeThoughtHeader(latest, isGenerating);
  }, [text, isGenerating]);

  // The reference's collapsed card: a summary line per segment of the
  // reasoning — ALL the thoughts, never the raw chain. A LIVE thought has no
  // settled segment list yet — it keeps the rotating line until it settles,
  // so partial thoughts never churn the card.
  const summaries = useMemo(() => {
    if (isGenerating) return { lines: [], omitted: 0 };
    return distillThoughtSummaries(text, THOUGHT_SUMMARY_PREVIEW_LINES);
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
            /* Settled: the reference's thinking card — ONE rounded container
               whose collapsed body is the SUMMARY OF ALL THE THOUGHTS: a
               quiet line per segment of the reasoning, never the raw chain.
               One chevron, one disclosure; a long block's unshown segments
               are counted in a "+N more" receipt instead of pretended away.
               Opening reveals the block's full reasoning. */
            <div
              id={panelId}
              className="thought-steps quiet-card text-xs overflow-hidden"
              data-slot="thought-steps"
              data-open={cardOpen}
            >
              <Button
                variant="bare"
                type="button"
                className="thought-step-head"
                onClick={() => setCardOpen((v) => !v)}
                aria-expanded={cardOpen}
                aria-controls={`${panelId}-chain`}
              >
                <span className="thought-summaries">
                  {summaries.lines.map((line, i) => (
                    <span key={i} className="thought-summary-line">
                      {line}
                    </span>
                  ))}
                  {summaries.omitted > 0 ? (
                    <span className="thought-summary-more">
                      +{summaries.omitted} more
                    </span>
                  ) : null}
                </span>
                <ChevronDown
                  className={cn('thought-step-chevron', cardOpen && 'is-open')}
                  aria-hidden
                />
              </Button>
              <div
                role="region"
                aria-label="Reasoning"
                hidden={!cardOpen}
                className="thought-step-detail"
              >
                {/* The block's raw chain — the same bytes every summary line
                    was distilled from. The region stays mounted so
                    `aria-controls` resolves while closed, but the body mounts
                    lazily: a collapsed card carries no reasoning bytes in the
                    DOM at all. Bounded with the transcript's clamp/scroll
                    convention so a long chain never blows up the rail. */}
                {cardOpen ? (
                  <div className="thought-step-reasoning tool-result-scroll max-h-48 overflow-y-auto overscroll-contain">
                    <Markdown content={text} />
                  </div>
                ) : null}
              </div>
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
