/* ── Empty chat state ─────────────────────────────────────────────────── */

import { motion } from 'framer-motion';
import { Bot, FileText, FolderSearch, GitBranch, GitCompare, Send } from 'lucide-react';
import type { ComponentType, ReactNode } from 'react';
import { dispatchInsertComposerText } from '@/api/ui-events';
import { normalizeHarnessMode } from '@/components/chat/HarnessModeChip';
import { WakeUpBriefCard } from '@/components/shell/WakeUpBriefCard';
import { Button } from '@/components/ui/button';
import { t } from '@/lib/motion';
import { useWakeUpBrief, type WakeUpBrief } from '@/lib/useWakeUpBrief';

const STARTER_CHIPS: ReadonlyArray<{
  label: string;
  prompt: string;
  Icon: ComponentType<{ className?: string }>;
}> = [
  {
    label: 'Research Paper Studio',
    prompt: 'Help me outline and draft a structured research paper on ',
    Icon: FileText,
  },
  {
    label: 'Analyze codebase',
    prompt: 'Analyze this codebase architecture, key entry points, and potential risks.',
    Icon: FolderSearch,
  },
  {
    label: 'Create a routine bot',
    prompt: 'Create a routine bot or scheduled automation to ',
    Icon: Bot,
  },
  {
    label: 'Review changes',
    prompt: 'Review recent changes in this workspace and flag any regressions.',
    Icon: GitCompare,
  },
];

const ORCH_EXAMPLES = [
  {
    label: 'Explore → implement → review',
    hint: 'Three waves, each waits on the last',
    goals: 'explore: map the relevant files\nimplement after:explore: make the change\nreview after:implement: verify with tests',
  },
  {
    label: 'Investigate a bug',
    hint: 'Repro, patch, confirm',
    goals: 'repro: find a failing test or steps\nfix after:repro: patch the cause\nverify after:fix: confirm the repro is gone',
  },
  {
    label: 'Read-only survey',
    hint: 'Workers read; nothing writes',
    goals: 'explore: summarize architecture and risks',
  },
];

// Keep an active wake-up brief across empty-thread navigation until dismissed,
// because the backend emits the startup brief once per boot.
let cachedWakeUpBrief: WakeUpBrief | null = null;
let wakeUpBriefDismissed = false;

function Launchpad({
  children,
  brief,
  onDismissBrief,
}: {
  children: ReactNode;
  brief: WakeUpBrief | null;
  onDismissBrief: () => void;
}) {
  return (
    <motion.div
      key="launchpad"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0, y: 16 }}
      transition={t.smooth}
      className="august-empty-state flex-1 min-h-0 overflow-y-auto flex flex-col items-center justify-center px-4 py-8"
    >
      <div className="august-empty-state-content mx-auto flex w-full max-w-[47.5rem] flex-col items-center gap-6">
        {brief && <WakeUpBriefCard brief={brief} onDismiss={onDismissBrief} />}

        <div className="flex flex-col items-center gap-1.5 text-center">
          <span className="text-xs font-medium text-[var(--dt-fg-3)]">New thread</span>
          <h1 className="text-2xl font-medium tracking-tight text-[var(--dt-fg-1)] sm:text-3xl">
            What are we building today?
          </h1>
        </div>

        <div className="w-full">{children}</div>

        <div
          className="flex flex-wrap items-center justify-center gap-2"
          data-testid="empty-starter-chips"
        >
          {STARTER_CHIPS.map(({ label, prompt, Icon }) => (
            <Button
              key={label}
              type="button"
              variant="bare"
              onClick={() => dispatchInsertComposerText(prompt, { replace: true })}
              className="inline-flex h-8 items-center gap-1.5 rounded-full border border-[var(--dt-line)] bg-[var(--dt-card)] px-3 text-xs font-medium text-[var(--dt-fg-2)] transition-colors hover:border-[var(--dt-line-strong)] hover:bg-[var(--dt-elevated)] hover:text-[var(--dt-fg-1)]"
            >
              <Icon className="size-3.5 shrink-0 text-[var(--dt-fg-3)]" />
              <span>{label}</span>
            </Button>
          ))}
        </div>
      </div>
    </motion.div>
  );
}

export function ChatEmptyState({
  children,
  agentMode,
}: {
  workspacePath?: string | null;
  children: ReactNode;
  agentMode?: string | null;
}) {
  const harness = normalizeHarnessMode(agentMode);
  const { brief, dismiss } = useWakeUpBrief();
  if (brief && !wakeUpBriefDismissed) {
    cachedWakeUpBrief = brief;
  }
  const activeBrief = wakeUpBriefDismissed ? null : (brief ?? cachedWakeUpBrief);
  const handleDismissBrief = () => {
    wakeUpBriefDismissed = true;
    cachedWakeUpBrief = null;
    dismiss();
  };

  if (harness !== 'orchestrator') {
    return (
      <Launchpad brief={activeBrief} onDismissBrief={handleDismissBrief}>
        {children}
      </Launchpad>
    );
  }

  return (
    <motion.div
      key="centered-layout"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0, y: 16 }}
      transition={t.smooth}
      className="august-empty-state flex-1 min-h-0 overflow-y-auto flex flex-col items-center justify-center px-4 py-8 relative"
    >
      <div className="august-empty-state-content mx-auto relative w-full max-w-[47.5rem] flex flex-col items-center gap-6">
        {activeBrief && <WakeUpBriefCard brief={activeBrief} onDismiss={handleDismissBrief} />}
        <div className="flex flex-col items-center gap-3 text-center">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--dt-line)] bg-[var(--dt-card)] px-2.5 py-1 text-2xs font-medium tracking-wide text-[var(--dt-fg-2)]">
            <GitBranch className="size-3 text-[var(--dt-fg-3)]" />
            Orchestrator
          </span>
          <p className="august-empty-title text-[var(--dt-fg-1)]">Plan a wave, then dispatch</p>
        </div>
        <div className="flex flex-wrap justify-center gap-2" data-testid="orchestrator-empty-examples">
          {ORCH_EXAMPLES.map((ex) => (
            <Button
              key={ex.label}
              type="button"
              variant="ghost"
              size="sm"
              className="h-auto max-w-[210px] flex-col items-start justify-start gap-0 rounded-xl border border-[var(--dt-line)] bg-[var(--dt-card)] px-3.5 py-2.5 text-left font-normal whitespace-normal hover:border-[var(--dt-line-strong)] hover:bg-[var(--dt-elevated)]"
              onClick={() => {
                window.dispatchEvent(new CustomEvent('august:open-spawn', { detail: { goals: ex.goals } }));
              }}
            >
              <span className="block text-[0.78125rem] font-medium tracking-tight text-[var(--dt-fg-1)]">{ex.label}</span>
              <span className="mt-0.5 block text-2xs leading-snug text-[var(--dt-fg-3)]">{ex.hint}</span>
            </Button>
          ))}
        </div>
        <Button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
          onClick={() => {
            window.dispatchEvent(new CustomEvent('august:open-spawn'));
          }}
        >
          <Send className="size-3" />
          Dispatch
        </Button>

        <div className="w-full pt-1">{children}</div>
      </div>
    </motion.div>
  );
}
