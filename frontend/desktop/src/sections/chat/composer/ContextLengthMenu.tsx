/* ── ContextLengthMenu — dedicated local llama-server context pill ──────── */
/* Rendered only when the active model comes from LocalModels. Displays the
 * live server ctxSize from listLlamaServers() (never the stale catalog
 * placeholder like 977k), shows an inline spinner while reloading, and opens
 * a dedicated 280px popover with 4k–64k presets and custom token input. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import {
  LOCAL_CTX_CHOICES,
  listLlamaServers,
  setLlamaServerContext,
} from '@/api/api-client/local-models';
import type { ModelItem } from '../model-display';

export interface ContextLengthMenuProps {
  selectedModel: ModelItem | null;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
}

function formatCtxLabel(ctxSize: number): string {
  if (ctxSize <= 0) return 'ctx';
  if (ctxSize >= 1024) {
    return ctxSize % 1024 === 0
      ? `${ctxSize / 1024}k`
      : `${(ctxSize / 1024).toFixed(1)}k`;
  }
  return String(ctxSize);
}

export function ContextLengthMenu({
  selectedModel,
  open,
  onOpenChange,
  className,
}: ContextLengthMenuProps) {
  const isLocalModel =
    selectedModel?.provider === 'LocalModels' ||
    selectedModel?.provider === 'local-models';

  const [localOpen, setLocalOpen] = useState(false);
  const isControlled = open !== undefined;
  const isOpen = isControlled ? open : localOpen;
  const setIsOpen = (next: boolean) => {
    if (isControlled) onOpenChange?.(next);
    else setLocalOpen(next);
  };

  const [customCtx, setCustomCtx] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();

  const serversQ = useQuery({
    queryKey: ['llama-servers'],
    queryFn: listLlamaServers,
    enabled: isLocalModel,
    refetchInterval: (q) =>
      (q.state.data?.servers ?? []).some((s) => s.status === 'starting') ? 2000 : 15_000,
  });

  const localServerRow = useMemo(() => {
    const servers = serversQ.data?.servers ?? [];
    if (!isLocalModel) return null;
    return (
      servers.find((s) => s.status === 'ready') ??
      servers.find((s) => s.status === 'starting') ??
      servers[0] ??
      null
    );
  }, [serversQ.data, isLocalModel]);

  const readyLocalServer =
    localServerRow && localServerRow.status === 'ready' ? localServerRow : null;
  const isStarting = localServerRow?.status === 'starting';

  useEffect(() => {
    if (isControlled || !localOpen) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current?.contains(e.target as Node)) return;
      setLocalOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [isControlled, localOpen]);

  const applyCtx = useCallback(
    (ctx: number) => {
      const row = localServerRow;
      if (!row) {
        toast.error('No local model is served — start one in Settings → Local Models first.');
        return;
      }
      if (row.status !== 'ready') {
        toast.info(
          `${row.name} is still ${row.status} — the context control unlocks when it is ready.`,
        );
        return;
      }
      if (row.ctxSize === ctx) {
        toast.info(`${row.name} is already at ${formatCtxLabel(ctx)} context.`);
        return;
      }
      setLlamaServerContext(row.id, ctx)
        .then((res) => {
          if (res.warning) toast.warning(res.warning);
          else toast.success(`${row.name} → ${formatCtxLabel(ctx)} context — loading…`);
          void qc.invalidateQueries({ queryKey: ['llama-servers'] });
          void qc.invalidateQueries({ queryKey: ['models'] });
        })
        .catch((err: unknown) =>
          toast.error(err instanceof Error ? err.message : 'Context change failed'),
        );
    },
    [localServerRow, qc],
  );

  const submitCustom = () => {
    const n = Number(customCtx);
    if (Number.isFinite(n) && n >= 1024) {
      applyCtx(n);
      setCustomCtx('');
    } else {
      toast.error('Enter a context size of 1024 or more.');
    }
  };

  if (!isLocalModel) return null;

  const pillLabel = localServerRow
    ? formatCtxLabel(localServerRow.ctxSize)
    : serversQ.isLoading
      ? '…'
      : 'ctx';

  const pillTitle = localServerRow
    ? isStarting
      ? `${localServerRow.name} reloading (${localServerRow.ctxSize.toLocaleString()} tokens)…`
      : `Context length ${localServerRow.ctxSize.toLocaleString()} tokens — click to change`
    : 'Local model context length';

  return (
    <div
      ref={rootRef}
      className={cn('relative shrink-0', className)}
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
        title={pillTitle}
        aria-label={pillTitle}
        aria-expanded={isOpen}
        aria-haspopup="dialog"
        data-testid="model-context-chip"
        className={cn(
          'inline-flex h-7 shrink-0 items-center gap-1 rounded-full bg-[var(--dt-pill-bg,var(--dt-elevated))] px-2.5 text-xs font-medium text-[var(--dt-fg-3)] transition-colors hover:text-[var(--dt-fg-1)]',
          isOpen && 'text-[var(--dt-fg-1)]',
        )}
      >
        {isStarting && <Loader2 className="size-3 shrink-0 animate-spin" aria-hidden />}
        <span className="tabular-nums">{pillLabel}</span>
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
          role="dialog"
          aria-label="Context length"
          data-composer-popover=""
          data-testid="model-context-menu"
          className="absolute bottom-full right-0 z-50 mb-2 w-[280px] max-w-[calc(100vw-2rem)] rounded-xl border border-[var(--dt-line)] bg-popover p-3 shadow-2xl"
        >
          <div className="flex items-center justify-between gap-2 pb-2">
            <span className="text-xs font-semibold text-[var(--dt-fg-1)]">Context length</span>
            {isStarting ? (
              <span className="inline-flex items-center gap-1 text-2xs text-[var(--dt-fg-3)]">
                <Loader2 className="size-3 animate-spin" aria-hidden />
                Reloading…
              </span>
            ) : localServerRow ? (
              <span className="max-w-[140px] truncate text-2xs text-[var(--dt-fg-3)]">
                {localServerRow.name}
              </span>
            ) : null}
          </div>

          {!localServerRow && !serversQ.isLoading ? (
            <div className="rounded-md border border-[var(--dt-line)] bg-[var(--dt-background)] px-2.5 py-2 text-2xs text-[var(--dt-fg-3)]">
              No local llama-server is running — start one in Settings → Local Models.
            </div>
          ) : (
            <>
              <div className="grid grid-cols-5 gap-1.5" data-testid="model-context-presets">
                {LOCAL_CTX_CHOICES.map((c) => {
                  const active = localServerRow?.ctxSize === c;
                  return (
                    <Button
                      key={c}
                      type="button"
                      variant="bare"
                      onClick={() => applyCtx(c)}
                      disabled={!readyLocalServer}
                      title={
                        readyLocalServer
                          ? `Set context to ${c.toLocaleString()} tokens`
                          : localServerRow
                            ? `${localServerRow.name} is ${localServerRow.status}`
                            : 'Loading server status'
                      }
                      aria-pressed={active}
                      className={cn(
                        'flex h-7 items-center justify-center rounded-md border text-xs font-medium tabular-nums transition-colors',
                        active
                          ? 'border-[var(--dt-primary)] bg-[var(--dt-primary)] text-[var(--dt-primary-foreground)]'
                          : 'border-[var(--dt-line)] bg-[var(--dt-background)] text-[var(--dt-fg-2)] hover:border-[var(--dt-line-strong)] hover:text-[var(--dt-fg-1)]',
                      )}
                    >
                      {formatCtxLabel(c)}
                    </Button>
                  );
                })}
              </div>

              <div className="mt-2.5 flex items-center gap-1.5">
                <input
                  type="number"
                  min={1024}
                  step={1024}
                  disabled={!readyLocalServer}
                  value={customCtx}
                  onChange={(e) => setCustomCtx(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      submitCustom();
                    }
                  }}
                  placeholder={localServerRow ? String(localServerRow.ctxSize) : '8192'}
                  aria-label="Custom context size in tokens"
                  className="h-8 min-w-0 flex-1 rounded-md border border-[var(--dt-line)] bg-[var(--dt-background)] px-2.5 text-xs tabular-nums text-[var(--dt-fg-1)] outline-none placeholder:text-[var(--dt-fg-3)] focus:border-[var(--dt-line-strong)] disabled:opacity-50"
                />
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={submitCustom}
                  disabled={!readyLocalServer || !customCtx}
                  title="Apply the custom context size"
                  aria-label="Apply custom context size"
                  data-testid="model-context-apply"
                  className="h-8 shrink-0 px-3 text-xs font-medium"
                >
                  Apply
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
