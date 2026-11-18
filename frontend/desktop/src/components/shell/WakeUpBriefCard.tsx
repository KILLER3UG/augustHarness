// WakeUpBriefCard — the "pick up where you left off" note (Phase 4).
//
// Rendered inline inside ChatEmptyState (no longer floating fixed over the
// transcript). Normal mode shows the brief text in a quiet card; quiet mode
// shows a single compact inline row. Dismiss records the dismissal backend-side.

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
      <div
        className="w-full max-w-[47.5rem] rounded-xl border border-[var(--dt-line)] bg-[var(--dt-card)] px-3.5 py-2 text-left"
        data-testid="wake-up-brief-card"
      >
        <div className="flex items-center gap-2.5">
          <span className="size-1.5 shrink-0 rounded-full bg-[var(--dt-primary)]" aria-hidden />
          <span className="min-w-0 flex-1 truncate text-xs text-[var(--dt-fg-2)]">
            Wake-up brief ready
          </span>
          <Button
            variant="bare"
            size="icon-sm"
            onClick={onDismiss}
            aria-label="Dismiss the wake-up brief"
            className="-mr-1 shrink-0 rounded-md text-[var(--dt-fg-3)] hover:text-[var(--dt-fg-1)]"
          >
            <X className="size-3.5" aria-hidden />
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div
      className="w-full max-w-[47.5rem] rounded-xl border border-[var(--dt-line)] bg-[var(--dt-card)] px-4 py-3 text-left"
      data-testid="wake-up-brief-card"
    >
      <div className="flex items-start gap-2.5">
        <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-[var(--dt-primary)]" aria-hidden />
        <p className="min-w-0 flex-1 whitespace-pre-line text-sm leading-relaxed text-[var(--dt-fg-1)]">
          {brief.text}
        </p>
        <Button
          variant="bare"
          size="icon-sm"
          onClick={onDismiss}
          aria-label="Dismiss the wake-up brief"
          className="-mr-1 -mt-0.5 shrink-0 rounded-md text-[var(--dt-fg-3)] hover:text-[var(--dt-fg-1)]"
        >
          <X className="size-3.5" aria-hidden />
        </Button>
      </div>
    </div>
  );
}
