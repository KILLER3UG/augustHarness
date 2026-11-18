/* ── ToolBtn ─ small icon button for the composer toolbar ────────────── */
/* The rest of the legacy ComposerControls (ModelDropdown, EffortDropdown) */
/* was removed — the current pickers live in composer/ and AccessPill.    */

import type { LucideIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export function ToolBtn({
  Icon,
  label,
  onClick,
  className,
  buttonRef,
  triggerAttr,
}: {
  Icon: LucideIcon;
  label: string;
  onClick?: () => void;
  className?: string;
  buttonRef?: React.RefObject<HTMLButtonElement | null>;
  triggerAttr?: boolean;
}) {
  return (
    <Button
      ref={buttonRef ?? undefined}
      type="button"
      variant="bare"
      onClick={onClick}
      data-composer-trigger={triggerAttr ? '' : undefined}
      className={cn(
        'inline-flex h-7 w-7 items-center justify-center rounded-full p-0 text-[var(--dt-fg-3)] transition-colors hover:bg-[var(--dt-elevated)] hover:text-[var(--dt-fg-1)]',
        className,
      )}
      title={label}
      aria-label={label}
    >
      <Icon className="size-3.5" />
    </Button>
  );
}
