import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, Pause, Play, Square, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import {
  deleteAutomation,
  getAutomations,
  patchAutomation,
  runAutomation,
  type AutomationJob,
} from '@/api/api-client/automations';
import { ConfirmDialog } from '@/components/overlays/ConfirmDialog';
import { useConfirmDialog } from '@/hooks/useConfirmDialog';
import { Button } from '@/components/ui/button';

/**
 * AutomationsSection — the Runs fold of the old /automations screen (Part D
 * diet). A MINIMAL view/undo list: one hairline row per automation with its
 * schedule, state and run/pause/delete actions. There is no create form —
 * creation is agent-driven (`create_automation` in chat, receipt card with
 * undo), which is the whole reason the GAP tools exist.
 */
export function AutomationsSection() {
  const qc = useQueryClient();
  const { state: confirmState, confirm: confirmStyled, handleConfirm, handleCancel } = useConfirmDialog();

  const automationsQ = useQuery({
    queryKey: ['automations'],
    queryFn: getAutomations,
    refetchInterval: (q) =>
      (q.state.data?.jobs ?? []).some((j) => j.status === 'running') ? 3_000 : 30_000,
  });
  const jobs = automationsQ.data?.jobs ?? [];

  const patch = useMutation({
    mutationFn: ({ id, paused }: { id: string; paused: boolean }) => patchAutomation(id, { paused }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['automations'] }),
    onError: (err) => toast.error(`Could not update: ${err instanceof Error ? err.message : String(err)}`),
  });

  const run = useMutation({
    mutationFn: (id: string) => runAutomation(id, true),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['automations'] }),
    onError: (err) => toast.error(`Run failed: ${err instanceof Error ? err.message : String(err)}`),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteAutomation(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['automations'] }),
    onError: (err) => toast.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`),
  });

  async function requestDelete(job: AutomationJob) {
    const ok = await confirmStyled({
      title: 'Delete this automation?',
      message: `"${job.name}" and its run history will be removed. This cannot be undone.`,
      confirmLabel: 'Delete',
      variant: 'destructive',
    });
    if (ok) remove.mutate(job.id);
  }

  return (
    <section className="mt-4" data-testid="runs-automations-section">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
          <CalendarClock className="size-3" aria-hidden /> Automations · {jobs.length}
        </h2>
        {/* Creation is the agent's job now — the manual form is gone. */}
        <span className="text-2xs text-muted-foreground">
          create by asking August in chat
        </span>
      </div>
      {jobs.length === 0 ? (
        <p className="mt-2 text-2xs text-muted-foreground">
          No automations yet. Ask August — “run a paper digest every morning at 9”.
        </p>
      ) : (
        <ul className="mt-1 divide-y divide-border/60">
          {jobs.map((job) => (
            <li
              key={job.id}
              className="group flex items-center gap-2 py-1.5 text-sm"
              data-testid={`automation-row-${job.id}`}
            >
              <span
                className="size-1.5 shrink-0 rounded-full bg-muted-foreground/60"
                aria-hidden
                data-running={job.status === 'running' || undefined}
                data-errored={job.status === 'error' || undefined}
              />
              <span className="min-w-0 flex-1 truncate">{job.name}</span>
              <span className="shrink-0 font-mono text-2xs text-muted-foreground">
                {job.schedule || 'manual'}
              </span>
              <span className="hidden shrink-0 text-2xs text-muted-foreground sm:inline">
                {job.paused ? 'paused' : job.enabled === false ? 'disabled' : job.nextRunAt || ''}
              </span>
              <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                <Button
                  variant="bare"
                  size="sm"
                  className="icon-button"
                  title={job.paused ? 'Resume' : 'Run now'}
                  aria-label={job.paused ? 'Resume automation' : 'Run automation now'}
                  onClick={() => (job.paused ? patch.mutate({ id: job.id, paused: false }) : run.mutate(job.id))}
                  disabled={run.isPending || patch.isPending}
                >
                  {job.paused ? <Play className="size-3" /> : <Square className="size-3" />}
                </Button>
                {!job.paused && (
                  <Button
                    variant="bare"
                    size="sm"
                    className="icon-button"
                    title="Pause schedule"
                    aria-label="Pause automation"
                    onClick={() => patch.mutate({ id: job.id, paused: true })}
                    disabled={patch.isPending}
                  >
                    <Pause className="size-3" />
                  </Button>
                )}
                <Button
                  variant="bare"
                  size="sm"
                  className="icon-button"
                  title="Delete automation"
                  aria-label="Delete automation"
                  onClick={() => void requestDelete(job)}
                  disabled={remove.isPending}
                >
                  <Trash2 className="size-3" />
                </Button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog
        open={confirmState !== null}
        title={confirmState?.title ?? ''}
        message={confirmState?.message ?? ''}
        confirmLabel={confirmState?.confirmLabel}
        variant={confirmState?.variant}
        onConfirm={handleConfirm}
        onCancel={handleCancel}
      />
    </section>
  );
}
