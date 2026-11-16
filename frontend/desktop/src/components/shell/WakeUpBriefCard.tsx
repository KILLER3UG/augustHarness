// WakeUpBriefCard — the "pick up where you left off" note (Phase 4).
//
// Rendered over the chat column in the same floating position as the
// backend status pill. Normal mode: a short card with the brief text.
// Quiet mode: a one-line chip that queues the brief instead of showing it —
// both dismiss with one click, which also records the dismissal backend-side.

import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { WakeUpBrief } from '@/lib/useWakeUpBrief';

export function WakeUpBriefCard({
  brief,
  onDismiss,
}: {
  brief: WakeUpBrief;
  onDismiss: () => void;
}) {
  if (brief.quiet) {
    return (
      <div className="pointer-events-none fixed left-1/2 top-[3.75rem] z-[80] -translate-x-1/2">
        <div className="pointer-events-auto inline-flex max-w-[92vw] items-center gap-2 rounded-full border border-border/60 bg-card/90 px-3 py-1.5 text-xs text-muted-foreground shadow-sm backdrop-blur">
          <span className="size-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
          <span className="whitespace-nowrap">Wake-up brief ready</span>
          <Button
            variant="bare"
            size="icon-sm"
            onClick={onDismiss}
            aria-label="Dismiss the wake-up brief"
            className="-mr-1 rounded-full text-muted-foreground hover:text-foreground"
          >
            <X aria-hidden />
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="pointer-events-none fixed left-1/2 top-[3.75rem] z-[80] -translate-x-1/2">
      <div className="pointer-events-auto w-[min(92vw,460px)] rounded-xl border border-border/60 bg-card/95 p-3.5 text-left shadow-lg backdrop-blur">
        <div className="flex items-start gap-2.5">
          <span className="mt-1 size-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
          <p className="min-w-0 flex-1 whitespace-pre-line text-sm leading-snug text-foreground">
            {brief.text}
          </p>
          <Button
            variant="bare"
            size="icon-sm"
            onClick={onDismiss}
            aria-label="Dismiss the wake-up brief"
            className="-mr-1 -mt-0.5 shrink-0 rounded-lg text-muted-foreground hover:text-foreground"
          >
            <X aria-hidden />
          </Button>
        </div>
      </div>
    </div>
  );
}
