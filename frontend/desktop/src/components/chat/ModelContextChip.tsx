import { useId, useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, Undo2, Waypoints } from 'lucide-react';
import { toast } from 'sonner';
import { setLlamaServerContext } from '@/api/api-client/local-models';
import { Button } from '@/components/ui/button';
import type { ChatMessage } from '@/types/chat';

/**
 * ModelContextChip — the receipt for a context-length change the ORCHESTRATOR
 * made on this turn (`set_model_context`).
 *
 * Shape follows BotReceiptChip exactly: one quiet bordered card, collapsed by
 * default, body carries the detail plus the undo. Undo is the same change back
 * to the previous length — the API restarts the server with it, so the undo
 * also waits through a reload; the toast and the Local Models tab show it.
 *
 * `effectiveCtx` may differ from `to` when the model could not load at the
 * requested size and fell back — the card names that rather than pretending
 * the requested window is running.
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
      toast.success(`${r.name} back to ${kTokens(r.from)} context — reloading`);
      void qc.invalidateQueries({ queryKey: ['llama-servers'] });
    },
    onError: (err) =>
      toast.error(`Undo failed: ${err instanceof Error ? err.message : String(err)}`),
  });

  const [open, setOpen] = useState(false);
  const uid = useId();
  const headId = `ctx-receipt-head-${uid}`;
  const bodyId = `ctx-receipt-body-${uid}`;

  if (!receipt || receipt.changed === false) return null;

  const fellBack = receipt.effectiveCtx !== receipt.to;

  return (
    <div
      className="memory-card w-fit min-w-0 max-w-full"
      data-open={open}
      data-slot="model-context-receipt"
      data-testid="model-context-chip"
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
        <Waypoints className="memory-card-chevron size-3 shrink-0" aria-hidden />
        <strong>
          Context {kTokens(receipt.from)} → {kTokens(receipt.to)}
          {fellBack ? ` · loaded ${kTokens(receipt.effectiveCtx)}` : ''}
        </strong>
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
          <span className="memory-card-key">{receipt.name}</span>
          <span className="text-muted-foreground">
            {' '}
            · {fellBack
              ? `${kTokens(receipt.to)} failed to load — running ${kTokens(receipt.effectiveCtx)}`
              : 'reloaded with the new window'}
          </span>
        </div>
        {receipt.warning && (
          <div className="memory-card-entry text-muted-foreground">{receipt.warning}</div>
        )}
        <div className="mt-1.5">
          <Button
            type="button"
            variant="bare"
            onClick={() => receipt && undo.mutate(receipt)}
            disabled={undo.isPending}
            className="inline-flex items-center gap-1 text-2xs text-primary hover:underline"
            data-testid="model-context-undo"
          >
            <Undo2 className="size-3" aria-hidden /> Back to {kTokens(receipt.from)}
          </Button>
        </div>
      </div>
    </div>
  );
}
