import { useId, useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, ChevronDown, Undo2 } from 'lucide-react';
import { toast } from 'sonner';
import { deleteAutomation } from '@/api/api-client/automations';
import { Button } from '@/components/ui/button';
import type { ChatMessage } from '@/types/chat';

/**
 * AutomationReceiptChip — the receipt for an automation the ORCHESTRATOR
 * created, changed or deleted on this turn (the Part D GAP tools).
 *
 * Same grammar as BotReceiptChip: one quiet bordered card reading the turn's
 * own tool summaries; undo is the human API door (deleteAutomation), not a
 * tool call. A deleted receipt reports what went away; an updated receipt
 * names the fields that changed.
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

  const [open, setOpen] = useState(false);
  const uid = useId();
  const headId = `automation-receipt-head-${uid}`;
  const bodyId = `automation-receipt-body-${uid}`;

  // A deleted receipt stands on its own: nothing to undo — say what went away.
  if (deleted && !created && !updated) {
    return (
      <div
        className="memory-card w-fit min-w-0 max-w-full"
        data-slot="automation-receipt"
        data-testid="automation-deleted-chip"
      >
        <div className="memory-card-head">
          <CalendarClock className="size-3 shrink-0" aria-hidden />
          <strong>Deleted {deleted.name}</strong>
        </div>
      </div>
    );
  }
  if (!created && !updated) return null;

  const head = created ? 'Automation ready' : 'Automation updated';
  const entry: AutomationReceipt = (created ?? updated) as AutomationReceipt;

  return (
    <div
      className="memory-card w-fit min-w-0 max-w-full"
      data-open={open}
      data-slot="automation-receipt"
      data-testid="automation-receipt-chip"
    >
      <Button
        variant="bare"
        type="button"
        id={headId}
        className="memory-card-head"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={bodyId}
      >
        <CalendarClock className="memory-card-chevron size-3 shrink-0" aria-hidden />
        <strong>{head}</strong>
        <ChevronDown className="memory-card-chevron size-3" aria-hidden />
      </Button>
      <div
        className="memory-card-body"
        id={bodyId}
        role="region"
        aria-labelledby={headId}
        hidden={!open}
      >
        <div className="memory-card-entry">
          <span className="memory-card-key">{entry.name}</span>
          {entry.schedule ? (
            <span className="text-muted-foreground"> · {entry.schedule}</span>
          ) : null}
        </div>
        {updated?.updated?.length ? (
          <div className="memory-card-entry text-muted-foreground">
            changed: {updated.updated.join(', ')}
          </div>
        ) : null}
        {entry.agentId ? (
          <div className="memory-card-entry text-muted-foreground">
            delivers to the bound bot's chat
          </div>
        ) : null}
        {entry.nextRunAt ? (
          <div className="memory-card-entry text-muted-foreground">
            next run {entry.nextRunAt}
          </div>
        ) : null}
        {created && (
          <div className="mt-1.5">
            <Button
              type="button"
              variant="bare"
              onClick={() => undo.mutate(created.jobId)}
              disabled={undo.isPending}
              className="inline-flex items-center gap-1 text-2xs text-primary hover:underline"
              data-testid="automation-receipt-undo"
            >
              <Undo2 className="size-3" aria-hidden /> Undo (delete this automation)
            </Button>
          </div>
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
