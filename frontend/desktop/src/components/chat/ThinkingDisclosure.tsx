import { memo, useId, useRef, useEffect, useState, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { SignalIcon } from '@/components/ui/SignalIcon';

/**
 * ThinkingDisclosure — the body opens only when the user clicks it
 *
 * Collapsible "Thinking" section that:
 * - Never opens itself: the activity header answers "what is it doing";
 *   the body waits for a click (it used to auto-open while streaming and
 *   snap shut at the end, moving twice per turn on its own)
 * - Shows elapsed time inline while streaming
 * - Scroll-locks to bottom during live preview
 */
interface ThinkingDisclosureProps {
  children: ReactNode;
  pending?: boolean;
  duration?: number;
  elapsed?: number;
  icon?: ReactNode;
  label?: string;
  /** When true, skip the "Thought for Xs" suffix (the activity summary
   *  already counts thoughts in its own total). */
  omitDurationLabel?: boolean;
}

export const ThinkingDisclosure = memo(function ThinkingDisclosure({
  children,
  pending = false,
  duration,
  elapsed,
  icon: _icon,
  label,
  // Kept in the signature: callers still pass it. The block shows the raw
  // duration instead of a composed sentence, so there is nothing to omit.
  omitDurationLabel: _omitDurationLabel = false,
}: ThinkingDisclosureProps) {
  const uid = useId();
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  // Claude-parity: done reasoning clamps to a short window with Show more.
  const [expanded, setExpanded] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);

  // Opened only by the user. It used to default to `pending`, which meant every
  // thought flung itself open while the model was reasoning and then snapped
  // shut at the end — the block moved on its own twice per turn. The live
  // "what is it doing" line is the activity header's job; the thought body
  // waits for a click.
  const open = userOpen ?? false;

  useEffect(() => {
    setUserOpen(null);
  }, [pending]);

  const isPreview = pending && userOpen === null;


  // Pin scroll to bottom during live preview
  useEffect(() => {
    if (!isPreview) return;
    const el = scrollRef.current;
    const content = contentRef.current;
    if (!el || !content) return;

    const pin = () => {
      el.scrollTop = el.scrollHeight;
    };
    pin();
    const observer = new ResizeObserver(pin);
    observer.observe(content);
    return () => observer.disconnect();
  }, [isPreview, open, children]);

  const liveElapsed = pending && elapsed !== undefined ? fmtElapsed(elapsed) : null;
  const displayDuration =
    duration !== undefined ? fmtElapsed(duration) : liveElapsed;
  // Explicit multi-count label (e.g. "Thinking (3)") wins when provided.
  // Otherwise the header reads the reference's way: "Thinking" while the
  // model reasons, "Thought for 3s" once it is settled — whole seconds, the
  // duration composed into the header text instead of trailing it.
  const defaultLabel = pending
    ? 'Thinking'
    : displayDuration
      ? `Thought for ${fmtWholeSeconds(duration ?? elapsed ?? 0)}`
      : 'Thought';
  const composedLabel = label != null && label !== '' ? label : defaultLabel;
  // Trailing duration: while LIVE (the elapsed count is the "what is it
  // doing" signal) and under an explicit caller label (the composition above
  // only owns the default label's wording). A settled default label already
  // contains its duration — "Thought for 3s" — so nothing trails it.
  const showTrailingDuration =
    displayDuration != null && (pending || (label != null && label !== ''));
  const bodyId = `thinking-body-${uid}`;
  const headerId = `thinking-head-${uid}`;

  return (
    <div className="thinking-block" data-open={open} data-slot="thinking-disclosure">
      <Button
        variant="bare"
        id={headerId}
        type="button"
        className="thinking-head"
        onClick={() => setUserOpen(!open)}
        aria-expanded={open}
        aria-controls={bodyId}
      >
        <SignalIcon name="brain" className="brain" />
        <strong>{composedLabel}</strong>
        {showTrailingDuration ? <span className="duration">{displayDuration}</span> : null}
        <SignalIcon name="chev" className="chev" />
      </Button>

      <div
        className="thinking-body"
        id={bodyId}
        role="region"
        aria-labelledby={headerId}
        hidden={!open}
      >
        {/* Done reasoning clamps to a short window with Show more: long
            reasoning must never dominate the transcript. While streaming the
            live preview scroll-pins instead. */}
        {!pending && (
          <Button
            variant="bare"
            type="button"
            onClick={() => setExpanded((v) => !v)}
            data-testid="thinking-show-more"
            className="thinking-more"
          >
            {expanded ? 'Show less' : 'Show more'}
          </Button>
        )}
        <div
          ref={scrollRef}
          className={cn(
            'tool-result-scroll overscroll-contain thinking-scroll',
            pending ? 'max-h-36 overflow-y-auto' : expanded ? '' : 'relative max-h-16 overflow-hidden',
          )}
          onWheel={(e) => {
            if (e.currentTarget.scrollHeight > e.currentTarget.clientHeight) {
              e.stopPropagation();
            }
          }}
        >
          <div ref={contentRef}>{children}</div>
        </div>
      </div>
    </div>
  );
});

function fmtElapsed(sec: number): string {
  if (sec < 1) return `${Math.round(sec * 1000)}ms`;
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return s ? `${m}m ${s}s` : `${m}m`;
}

/** Whole seconds for the settled header — the reference prints "Thought for
 *  3s", not "3.2s". A sub-second thought still reports 1s (0s reads as a
 *  glitch, and the block did think). */
function fmtWholeSeconds(sec: number): string {
  const s = Math.max(1, Math.round(sec));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rest = s % 60;
  return rest ? `${m}m ${rest}s` : `${m}m`;
}
