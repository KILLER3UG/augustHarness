import { useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';
import { setLlamaServerContext } from '@/api/api-client/local-models';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { resolveActiveSession, useSessionsStore } from '@/store/sessions';
import type { ChatMessage } from '@/types/chat';

/**
 * ModelContextChip — single horizontal receipt row for a context-length
 * change the orchestrator made on this turn (`set_model_context`).
 *
 * Edit opens the composer's context menu when the active chat uses a local
 * model, or navigates to Model Providers settings otherwise. Undo rolls the
 * server back to `from` via `setLlamaServerContext`.
 */

type ContextReceipt = {
  serverId: string;
  name: string;
  from: number;
  to: number;
  effectiveCtx: number;
  changed?: boolean;
  warning?: string;
};

function parseReceipt(summary: string | undefined, kind: string): ContextReceipt | null {
  if (!summary) return null;
  try {
    const parsed = JSON.parse(summary) as Record<string, unknown>;
    if (parsed?.kind !== kind) return null;
    const serverId = typeof parsed.serverId === 'string' ? parsed.serverId : '';
    if (!serverId) return null;
    return {
      serverId,
      name: typeof parsed.name === 'string' ? parsed.name : 'local model',
      from: typeof parsed.from === 'number' ? parsed.from : 0,
      to: typeof parsed.to === 'number' ? parsed.to : 0,
      effectiveCtx:
        typeof parsed.effectiveCtx === 'number'
          ? parsed.effectiveCtx
          : typeof parsed.to === 'number'
            ? parsed.to
            : 0,
      changed: typeof parsed.changed === 'boolean' ? parsed.changed : undefined,
      warning: typeof parsed.warning === 'string' ? parsed.warning : undefined,
    };
  } catch {
    /* the receipt is JSON; a non-JSON summary is not a context change */
  }
  return null;
}

function kTokens(n: number): string {
  return n >= 1024 ? `${Math.round(n / 1024)}k` : String(n);
}

export function ModelContextChip({ tools }: { tools?: ChatMessage['tools'] }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { sessionId } = useParams<{ sessionId?: string }>();
  const sessions = useSessionsStore((s) => s.sessions);
  const [undone, setUndone] = useState(false);
  const activeSession = useMemo(
    () => resolveActiveSession(sessions, sessionId ?? null),
    [sessions, sessionId],
  );

  const receipt = useMemo(() => {
    for (const tool of tools ?? []) {
      const hit = parseReceipt(tool.summary, 'model_context_changed');
      if (hit) return hit;
    }
    return null;
  }, [tools]);

  const undo = useMutation({
    mutationFn: (r: ContextReceipt) => setLlamaServerContext(r.serverId, r.from),
    onSuccess: (_data, r) => {
      setUndone(true);
      toast.success(`${r.name} back to ${kTokens(r.from)} context — reloading`);
      void qc.invalidateQueries({ queryKey: ['llama-servers'] });
    },
    onError: (err) =>
      toast.error(`Undo failed: ${err instanceof Error ? err.message : String(err)}`),
  });

  if (!receipt || receipt.changed === false) return null;

  const fellBack = receipt.effectiveCtx !== receipt.to;
  const summaryText = `Context ${kTokens(receipt.from)} → ${kTokens(receipt.to)} · ${receipt.name}${
    fellBack ? ` (loaded ${kTokens(receipt.effectiveCtx)})` : ''
  }`;
  const isLocalActive =
    activeSession?.provider === 'LocalModels' ||
    activeSession?.workbenchProvider === 'LocalModels';

  const handleEdit = () => {
    if (isLocalActive) {
      window.dispatchEvent(
        new CustomEvent('august:open-context-menu', {
          detail: { serverId: receipt.serverId },
        }),
      );
      return;
    }
    void navigate('/settings/model-providers');
  };

  if (undone) {
    return (
      <div
        className="flex w-full min-w-0 items-center gap-2.5 rounded-[10px] border border-[var(--dt-line)] bg-[var(--dt-receipt-bg)] px-3 py-2 text-xs"
        data-slot="model-context-receipt"
        data-undone="true"
        data-testid="model-context-undone-chip"
      >
        <span className="size-1.5 shrink-0 rounded-full bg-[var(--dt-fg-3)]" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-[var(--dt-fg-2)]">
          Context reverted to {kTokens(receipt.from)} · {receipt.name}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleEdit}
            className="h-6 px-2 text-2xs text-[var(--dt-fg-2)] hover:text-[var(--dt-fg-1)]"
            data-testid="model-context-edit"
          >
            Edit
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div
      className="flex w-full min-w-0 items-center gap-2.5 rounded-[10px] border border-[var(--dt-line)] bg-[var(--dt-receipt-bg)] px-3 py-2 text-xs"
      data-slot="model-context-receipt"
      data-testid="model-context-receipt"
    >
      <span
        className={cn(
          'size-1.5 shrink-0 rounded-full',
          fellBack ? 'bg-warning' : 'bg-success',
        )}
        aria-hidden
      />
      <span
        className="min-w-0 flex-1 truncate text-[var(--dt-fg-1)]"
        title={receipt.warning ? `${summaryText} — ${receipt.warning}` : summaryText}
      >
        {summaryText}
      </span>
      <div className="flex shrink-0 items-center gap-1">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={handleEdit}
          className="h-6 px-2 text-2xs text-[var(--dt-fg-2)] hover:text-[var(--dt-fg-1)]"
          data-testid="model-context-edit"
        >
          Edit
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => undo.mutate(receipt)}
          disabled={undo.isPending}
          className="h-6 px-2 text-2xs text-[var(--dt-fg-2)] hover:text-[var(--dt-fg-1)]"
          title={`Back to ${kTokens(receipt.from)}`}
          data-testid="model-context-undo"
        >
          Undo
        </Button>
      </div>
    </div>
  );
}
