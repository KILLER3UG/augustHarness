/* ── AccessPill — unified workspace/approval/harness access pill ──────── */
/* Resting pill stays monochrome (--dt-pill-bg / --dt-fg-3), with zero red
 * alarm tint or TriangleAlert. The open menu offers three primary levels
 * (Read-only, Standard, Full access) plus hairline-separated secondary
 * controls so every existing backend level remains one click away and every
 * label accurately states what the backend enforces. */

import { useEffect, useRef, useState } from 'react';
import {
  Bot,
  Check,
  ChevronDown,
  GitBranch,
  MessageSquare,
  Shield,
  ShieldCheck,
  Terminal,
  TriangleAlert,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { WorkbenchGuardMode } from '@/components/chat/WorkbenchModeSelector';
import type { WorkbenchSandboxMode } from '@/components/chat/SandboxModeSelector';
import { normalizeHarnessMode, type HarnessAgentMode } from '@/components/chat/HarnessModeChip';

export interface AccessPillProps {
  guardMode: WorkbenchGuardMode;
  onGuardChange: (mode: WorkbenchGuardMode) => void;
  sandboxMode: WorkbenchSandboxMode;
  onSandboxChange: (mode: WorkbenchSandboxMode) => void;
  /** Atomic combined updater when switching tiers touches both guard & sandbox. */
  onAccessChange?: (next: { guard?: WorkbenchGuardMode; sandbox?: WorkbenchSandboxMode }) => void;
  harnessMode?: string;
  onHarnessChange?: (mode: HarnessAgentMode) => void;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
}

const HARNESS_OPTIONS: {
  id: HarnessAgentMode;
  label: string;
  description: string;
  Icon: typeof Bot;
}[] = [
  { id: 'agent', label: 'Agent', description: 'Native tools in this chat.', Icon: Bot },
  {
    id: 'orchestrator',
    label: 'Orchestrator',
    description: 'Dispatch workstreams. Workers edit and shell.',
    Icon: GitBranch,
  },
  { id: 'chat', label: 'Chat', description: 'Text only — no tools.', Icon: MessageSquare },
  { id: 'code', label: 'Code', description: 'Fenced Python in the workspace.', Icon: Terminal },
];

type PrimaryTier = 'read-only' | 'standard' | 'full';

function CompactSwitch({
  checked,
  label,
  onToggle,
  testId,
}: {
  checked: boolean;
  label: string;
  onToggle: () => void;
  testId?: string;
}) {
  return (
    <Button
      type="button"
      variant="bare"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      data-testid={testId}
      onClick={onToggle}
      className={cn(
        'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors focus-visible:ring-2 focus-visible:ring-ring/40',
        checked ? 'bg-[var(--dt-primary)]' : 'bg-[var(--dt-elevated)]',
      )}
    >
      <span
        className={cn(
          'inline-block size-3.5 transform rounded-full shadow transition-transform',
          checked
            ? 'translate-x-[18px] bg-[var(--dt-primary-foreground)]'
            : 'translate-x-0.5 bg-[var(--dt-fg-2)]',
        )}
      />
    </Button>
  );
}

export function AccessPill({
  guardMode,
  onGuardChange,
  sandboxMode,
  onSandboxChange,
  onAccessChange,
  harnessMode = 'agent',
  onHarnessChange,
  open,
  onOpenChange,
  className,
}: AccessPillProps) {
  const [localOpen, setLocalOpen] = useState(false);
  const isControlled = open !== undefined;
  const isOpen = isControlled ? open : localOpen;
  const setIsOpen = (next: boolean) => {
    if (isControlled) onOpenChange?.(next);
    else setLocalOpen(next);
  };

  // Remember the last non-plan guard mode so toggling Plan mode off restores it.
  const lastNonPlanGuardRef = useRef<WorkbenchGuardMode>(
    guardMode === 'plan' ? 'ask' : guardMode,
  );
  if (guardMode !== 'plan') {
    lastNonPlanGuardRef.current = guardMode;
  }

  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isControlled || !localOpen) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current?.contains(e.target as Node)) return;
      setLocalOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [isControlled, localOpen]);

  const harness = normalizeHarnessMode(harnessMode);
  const harnessMeta = HARNESS_OPTIONS.find((h) => h.id === harness) ?? HARNESS_OPTIONS[0];
  const isReadOnly = sandboxMode === 'read-only';
  const isPlan = guardMode === 'plan';
  const isFull = !isReadOnly && guardMode === 'full';
  const isStandard = !isReadOnly && !isFull && !isPlan;
  const isWholeMachine = sandboxMode === 'danger-full-access';

  const apply = (next: { guard?: WorkbenchGuardMode; sandbox?: WorkbenchSandboxMode }) => {
    if (onAccessChange) {
      onAccessChange(next);
      return;
    }
    if (next.sandbox && next.sandbox !== sandboxMode) onSandboxChange(next.sandbox);
    if (next.guard && next.guard !== guardMode) onGuardChange(next.guard);
  };

  const selectTier = (tier: PrimaryTier) => {
    if (tier === 'read-only') {
      apply({ sandbox: 'read-only', guard: 'ask' });
    } else if (tier === 'standard') {
      apply({
        sandbox: 'workspace-write',
        guard: isStandard && guardMode === 'edit' ? 'edit' : 'ask',
      });
    } else {
      apply({
        guard: 'full',
        sandbox: isReadOnly ? 'workspace-write' : sandboxMode,
      });
    }
    setIsOpen(false);
  };

  const baseLabel = isReadOnly
    ? isPlan
      ? 'Read-only · plan'
      : 'Read-only'
    : isPlan
      ? isWholeMachine
        ? 'Plan mode · machine'
        : 'Plan mode'
      : isFull
        ? isWholeMachine
          ? 'Full access · machine'
          : 'Full access'
        : guardMode === 'edit'
          ? isWholeMachine
            ? 'Standard · auto-edits · machine'
            : 'Standard · auto-edits'
          : isWholeMachine
            ? 'Standard · machine'
            : 'Standard';

  const pillLabel =
    harness !== 'agent'
      ? `Access: ${baseLabel} · ${harnessMeta.label}`
      : `Access: ${baseLabel}`;

  const fullTitle = [
    pillLabel,
    `How I work: ${harnessMeta.label} (${harnessMeta.description})`,
    isReadOnly
      ? 'Read-only: inspects files without writing or network.'
      : isPlan
        ? 'Plan mode: proposes a plan before writing or running commands.'
        : isFull
          ? 'Full access: runs edits and commands without prompting.'
          : guardMode === 'edit'
            ? 'Standard · auto-edits: auto-approves file edits; asks before shell commands.'
            : 'Standard: asks before commands and file edits.',
    isWholeMachine
      ? 'Tool reach: whole machine and network.'
      : isReadOnly
        ? 'Tool reach: project read-only.'
        : 'Tool reach: project workspace.',
  ].join('\n');

  return (
    <div
      ref={rootRef}
      className={cn('relative min-w-0', className)}
      onKeyDown={(e) => {
        if (!isControlled && e.key === 'Escape' && isOpen) {
          e.preventDefault();
          setIsOpen(false);
        }
      }}
    >
      <Button
        type="button"
        variant="bare"
        data-composer-trigger=""
        onClick={() => setIsOpen(!isOpen)}
        aria-expanded={isOpen}
        aria-haspopup="menu"
        aria-label={pillLabel}
        title={fullTitle}
        data-testid="agent-mode-chip"
        className={cn(
          'inline-flex h-7 max-w-[240px] items-center gap-1.5 rounded-full bg-[var(--dt-pill-bg,var(--dt-elevated))] px-2.5 text-xs font-medium text-[var(--dt-fg-3)] transition-colors hover:text-[var(--dt-fg-1)]',
          isOpen && 'text-[var(--dt-fg-1)]',
        )}
      >
        <Shield className="size-3.5 shrink-0" aria-hidden />
        <span className="min-w-0 flex-1 truncate">{pillLabel}</span>
        <ChevronDown
          className={cn(
            'size-3 shrink-0 opacity-60 transition-transform duration-150',
            isOpen && 'rotate-180',
          )}
          aria-hidden
        />
      </Button>

      {isOpen && (
        <div
          role="menu"
          aria-label="Access"
          data-composer-popover=""
          data-testid="agent-mode-menu"
          className="absolute bottom-full left-0 z-50 mb-2 max-h-[min(24rem,calc(100vh-14rem))] w-[316px] max-w-[calc(100vw-2rem)] overflow-y-auto rounded-xl border border-[var(--dt-line)] bg-popover shadow-2xl"
        >
          <div className="py-1">
            <Button
              type="button"
              variant="bare"
              role="menuitemradio"
              aria-checked={isReadOnly && !isPlan}
              onClick={() => selectTier('read-only')}
              data-testid="agent-mode-row"
              data-mode="read-only"
              className={cn(
                'flex w-full items-start gap-2.5 rounded-none px-3 py-1 text-left transition-colors hover:bg-[var(--dt-elevated)]',
                isReadOnly && 'bg-[var(--dt-elevated)]',
              )}
            >
              <Shield className="mt-0.5 size-4 shrink-0 text-[var(--dt-fg-3)]" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block text-xs font-semibold leading-5 text-[var(--dt-fg-1)]">
                  Read-only
                </span>
                <span className="block text-2xs leading-snug text-[var(--dt-fg-3)]">
                  Inspect project only — no writes or network; asks before commands.
                </span>
              </span>
              {isReadOnly ? (
                <Check className="mt-0.5 size-3.5 shrink-0 text-[var(--dt-fg-1)]" aria-hidden />
              ) : (
                <span className="size-3.5 shrink-0" aria-hidden />
              )}
            </Button>

            <Button
              type="button"
              variant="bare"
              role="menuitemradio"
              aria-checked={isStandard}
              onClick={() => selectTier('standard')}
              data-testid="agent-mode-row"
              data-mode="standard"
              className={cn(
                'flex w-full items-start gap-2.5 rounded-none px-3 py-1 text-left transition-colors hover:bg-[var(--dt-elevated)]',
                isStandard && 'bg-[var(--dt-elevated)]',
              )}
            >
              <ShieldCheck className="mt-0.5 size-4 shrink-0 text-[var(--dt-fg-3)]" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block text-xs font-semibold leading-5 text-[var(--dt-fg-1)]">
                  Standard
                </span>
                <span className="block text-2xs leading-snug text-[var(--dt-fg-3)]">
                  {guardMode === 'edit' && !isReadOnly
                    ? 'Auto-approves file edits in project; shell commands still ask.'
                    : 'Asks before shell commands and file edits in project.'}
                </span>
              </span>
              {isStandard ? (
                <Check className="mt-0.5 size-3.5 shrink-0 text-[var(--dt-fg-1)]" aria-hidden />
              ) : (
                <span className="size-3.5 shrink-0" aria-hidden />
              )}
            </Button>

            <Button
              type="button"
              variant="bare"
              role="menuitemradio"
              aria-checked={isFull}
              onClick={() => selectTier('full')}
              data-testid="agent-mode-row"
              data-mode="full"
              className={cn(
                'flex w-full items-start gap-2.5 rounded-none px-3 py-1 text-left transition-colors hover:bg-destructive/10',
                isFull && 'bg-destructive/10',
              )}
            >
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block text-xs font-semibold leading-5 text-destructive">
                  Full access
                </span>
                <span className="block text-2xs leading-snug text-destructive/85">
                  {isWholeMachine
                    ? 'Never asks — edits files and runs commands across the whole machine.'
                    : 'Never asks — edits files and runs commands inside this project.'}
                </span>
              </span>
              {isFull ? (
                <Check className="mt-0.5 size-3.5 shrink-0 text-destructive" aria-hidden />
              ) : (
                <span className="size-3.5 shrink-0" aria-hidden />
              )}
            </Button>
          </div>

          <div className="space-y-2 border-t border-[var(--dt-line)] px-3 py-1.5">
            {isStandard && (
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-xs font-medium text-[var(--dt-fg-1)]">
                    Auto-approve file edits
                  </div>
                  <div className="text-2xs text-[var(--dt-fg-3)]">
                    Skip edit prompts; shell commands still ask.
                  </div>
                </div>
                <CompactSwitch
                  checked={guardMode === 'edit'}
                  label="Auto-approve file edits"
                  testId="access-auto-edits-toggle"
                  onToggle={() =>
                    apply({ guard: guardMode === 'edit' ? 'ask' : 'edit' })
                  }
                />
              </div>
            )}

            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="text-xs font-medium text-[var(--dt-fg-1)]">Plan mode</div>
                <div className="text-2xs text-[var(--dt-fg-3)]">
                  Nothing writes or runs until you approve the plan.
                </div>
              </div>
              <CompactSwitch
                checked={isPlan}
                label="Plan mode"
                testId="access-plan-toggle"
                onToggle={() =>
                  apply({ guard: isPlan ? lastNonPlanGuardRef.current || 'ask' : 'plan' })
                }
              />
            </div>

            {!isReadOnly && (
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div
                    className={cn(
                      'text-xs font-medium',
                      isWholeMachine ? 'text-destructive' : 'text-[var(--dt-fg-1)]',
                    )}
                  >
                    Whole-machine reach
                  </div>
                  <div className="text-2xs text-[var(--dt-fg-3)]">
                    Allow shell & edits outside project folder + network.
                  </div>
                </div>
                <CompactSwitch
                  checked={isWholeMachine}
                  label="Whole-machine reach"
                  testId="access-sandbox-toggle"
                  onToggle={() =>
                    apply({
                      sandbox: isWholeMachine ? 'workspace-write' : 'danger-full-access',
                    })
                  }
                />
              </div>
            )}

            <div className="border-t border-[var(--dt-line)] pt-2" data-testid="harness-mode-row">
              <div className="mb-1.5 flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-[var(--dt-fg-1)]">How I work</span>
                <span className="text-2xs text-[var(--dt-fg-3)]">{harnessMeta.label}</span>
              </div>
              <div
                role="radiogroup"
                aria-label="How I work"
                className="grid grid-cols-2 gap-1 rounded-lg bg-[var(--dt-background)] p-1"
              >
                {HARNESS_OPTIONS.map((option) => {
                  const selected = harness === option.id;
                  const Icon = option.Icon;
                  return (
                    <Button
                      key={option.id}
                      type="button"
                      variant="bare"
                      role="radio"
                      aria-checked={selected}
                      data-mode={option.id}
                      title={option.description}
                      onClick={() => onHarnessChange?.(option.id)}
                      className={cn(
                        'inline-flex h-7 items-center justify-start gap-1.5 rounded-md px-2 text-2xs font-medium transition-colors',
                        selected
                          ? 'bg-[var(--dt-elevated)] text-[var(--dt-fg-1)] border border-[var(--dt-line-strong)]'
                          : 'text-[var(--dt-fg-3)] hover:text-[var(--dt-fg-1)]',
                      )}
                    >
                      <Icon className="size-3 shrink-0" aria-hidden />
                      <span>{option.label}</span>
                    </Button>
                  );
                })}
              </div>
              <div className="mt-1.5 text-2xs leading-snug text-[var(--dt-fg-3)]">
                {harnessMeta.description}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
