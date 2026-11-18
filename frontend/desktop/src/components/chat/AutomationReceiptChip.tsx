import { useMemo } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { deleteAutomation } from '@/api/api-client/automations';
import { Button } from '@/components/ui/button';
import type { ChatMessage } from '@/types/chat';

/**
 * AutomationReceiptChip — single horizontal receipt row for an automation the
 * orchestrator created, updated, or deleted on this turn.
 *
 * Edit navigates to `/runs` (where AutomationsSection lives); Undo deletes a
 * created automation via `deleteAutomation`.
 */

type AutomationReceipt = {
  jobId: string;
  name: string;
  schedule?: string;
  agentId?: string;
  nextRunAt?: string;
  updated?: string[];
};

function parseReceipt(summary: string | undefined, kinds: string[]): AutomationReceipt | null {
  if (!summary) return null;
  try {
    const parsed = JSON.parse(summary) as Record<string, unknown>;
    if (typeof parsed?.kind !== 'string' || !kinds.includes(parsed.kind)) return null;
    const jobId = typeof parsed.jobId === 'string' ? parsed.jobId : '';
    if (!jobId) return null;
    return {
      jobId,
      name: typeof parsed.name === 'string' ? parsed.name : 'automation',
      schedule: typeof parsed.schedule === 'string' ? parsed.schedule : undefined,
      agentId: typeof parsed.agentId === 'string' ? parsed.agentId : undefined,
      nextRunAt: typeof parsed.nextRunAt === 'string' ? parsed.nextRunAt : undefined,
      updated: Array.isArray(parsed.updated) ? (parsed.updated as string[]) : undefined,
    };
  } catch {
    /* the receipt is JSON; a non-JSON summary is not a receipt */
  }
  return null;
}

export function AutomationReceiptChip({ tools }: { tools?: ChatMessage['tools'] }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const created = useMemo(
    () => parseReceiptLast(tools, ['automation_created']),
    [tools],
  );
  const updated = useMemo(
    () => parseReceiptLast(tools, ['automation_updated']),
    [tools],
  );
  const deleted = useMemo(
    () => parseReceiptLast(tools, ['automation_deleted']),
    [tools],
  );

  const undo = useMutation({
    mutationFn: (jobId: string) => deleteAutomation(jobId),
    onSuccess: () => {
      toast.success('Automation deleted — creation undone');
      void qc.invalidateQueries({ queryKey: ['automations'] });
    },
    onError: (err) =>
      toast.error(`Undo failed: ${err instanceof Error ? err.message : String(err)}`),
  });

  if (deleted && !created && !updated) {
    return (
      <div
        className="flex w-full min-w-0 items-center gap-2.5 rounded-[10px] border border-[var(--dt-line)] bg-[var(--dt-receipt-bg)] px-3 py-2 text-xs"
        data-slot="automation-receipt"
        data-testid="automation-deleted-chip"
      >
        <span className="size-1.5 shrink-0 rounded-full bg-[var(--dt-fg-3)]" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-[var(--dt-fg-2)]">
          Automation &ldquo;{deleted.name}&rdquo; deleted
        </span>
      </div>
    );
  }
  if (!created && !updated) return null;

  const entry: AutomationReceipt = (created ?? updated) as AutomationReceipt;
  const detailParts: string[] = [];
  if (entry.schedule) detailParts.push(entry.schedule);
  if (updated?.updated?.length) detailParts.push(`changed: ${updated.updated.join(', ')}`);
  if (entry.nextRunAt) detailParts.push(`next run ${entry.nextRunAt}`);

  return (
    <div
      className="flex w-full min-w-0 items-center gap-2.5 rounded-[10px] border border-[var(--dt-line)] bg-[var(--dt-receipt-bg)] px-3 py-2 text-xs"
      data-slot="automation-receipt"
      data-testid="automation-receipt-chip"
    >
      <span className="size-1.5 shrink-0 rounded-full bg-success" aria-hidden />
      <span
        className="min-w-0 flex-1 truncate text-[var(--dt-fg-1)]"
        title={detailParts.length > 0 ? `${entry.name} · ${detailParts.join(' · ')}` : entry.name}
      >
        <span>
          {created
            ? `Automation "${entry.name}"`
            : `Automation "${entry.name}" updated`}
        </span>
        {entry.schedule ? (
          <span className="text-[var(--dt-fg-3)]"> · {entry.schedule}</span>
        ) : null}
      </span>
      <div className="flex shrink-0 items-center gap-1">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => void navigate('/runs')}
          className="h-6 px-2 text-2xs text-[var(--dt-fg-2)] hover:text-[var(--dt-fg-1)]"
          data-testid="automation-receipt-edit"
        >
          Edit
        </Button>
        {created && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => undo.mutate(created.jobId)}
            disabled={undo.isPending}
            className="h-6 px-2 text-2xs text-[var(--dt-fg-2)] hover:text-[var(--dt-fg-1)]"
            title="Undo (delete this automation)"
            data-testid="automation-receipt-undo"
          >
            Undo
          </Button>
        )}
      </div>
    </div>
  );
}

/** The LAST matching receipt in the turn: a turn that creates, then updates,
 * the same automation reports the final state. */
function parseReceiptLast(
  tools: ChatMessage['tools'],
  kinds: string[],
): AutomationReceipt | null {
  let hit: AutomationReceipt | null = null;
  for (const tool of tools ?? []) {
    const parsed = parseReceipt(tool.summary, kinds);
    if (parsed) hit = parsed;
  }
  return hit;
}
