/* Local models — detect LM Studio / Ollama / llama.cpp-class servers on this
 * machine and offer them as providers, plus (Phase 2) on-disk GGUF files,
 * the llama.cpp runtime, and Hugging Face downloads.
 *
 * The scan is read-only; the only write is the explicit "Add", which goes
 * through the same provider-create path as a hand-typed provider.
 */

import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Loader2, Plus, RefreshCw, Server } from 'lucide-react';
import { toast } from 'sonner';
import {
  addLocalServer,
  scanLocalServers,
  type LocalServer,
} from '@/api/api-client/local-models';
import { Button } from '@/components/ui/button';
import { refreshProviderCatalog } from '@/lib/provider-catalog';

const MODEL_CHIPS_SHOWN = 6;

function ServerCard({ server }: { server: LocalServer }) {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);

  const kindHint =
    server.kind === 'comfyui'
      ? 'Image generation server — configure it under Settings → ComfyUI.'
      : server.detail || '';

  async function add() {
    setAdding(true);
    try {
      const res = await addLocalServer({
        baseUrl: server.baseUrl,
        name: server.name,
        apiFormat: server.apiFormat,
      });
      const found = res.discovery.added.length;
      toast.success(
        found > 0
          ? `${server.name} added — ${found} model${found === 1 ? '' : 's'} discovered`
          : `${server.name} added — no models discovered yet (is a model loaded?)`,
      );
      await refreshProviderCatalog(qc);
      // Re-scan so the card flips to "Added" from the store's answer.
      await qc.invalidateQueries({ queryKey: ['local-models-scan'] });
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Could not add the server');
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="rounded-lg border border-border/60 bg-black/10 p-3 flex flex-col gap-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold flex items-center gap-1.5">
            <Server className="size-3.5 text-muted-foreground shrink-0" />
            {server.name}
            <span className="text-2xs font-normal text-muted-foreground">port {server.port}</span>
          </p>
          <p className="text-2xs text-muted-foreground font-mono truncate mt-0.5">{server.baseUrl}</p>
          {kindHint && <p className="text-2xs text-muted-foreground mt-1">{kindHint}</p>}
        </div>
        {server.kind === 'comfyui' ? (
          <Button size="sm" variant="outline" disabled>
            <Check className="size-3" /> Detected
          </Button>
        ) : server.added ? (
          <Button size="sm" variant="outline" disabled>
            <Check className="size-3 text-success-fg" /> Added
          </Button>
        ) : (
          <Button size="sm" onClick={() => void add()} disabled={adding}>
            {adding ? <Loader2 className="size-3 animate-spin" /> : <Plus className="size-3" />}
            Add as provider
          </Button>
        )}
      </div>
      {server.models.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {server.models.slice(0, MODEL_CHIPS_SHOWN).map((m) => (
            <span
              key={m}
              className="text-2xs px-1.5 py-0.5 rounded border border-border/60 bg-card/60 font-mono text-muted-foreground"
            >
              {m}
            </span>
          ))}
          {server.models.length > MODEL_CHIPS_SHOWN && (
            <span className="text-2xs text-muted-foreground self-center">
              +{server.models.length - MODEL_CHIPS_SHOWN} more
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export function LocalModelsTab() {
  const scanQ = useQuery({
    queryKey: ['local-models-scan'],
    queryFn: () => scanLocalServers(),
    staleTime: 30_000,
  });

  const servers = scanQ.data?.servers ?? [];
  const rescan = () => void scanQ.refetch();

  return (
    <div className="space-y-3 h-full flex flex-col">
      <div className="rounded-xl border border-border/60 bg-card/60 p-5 space-y-4 flex-1 overflow-auto">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm font-semibold">Local servers</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              Servers running on this machine — LM Studio, Ollama, llama.cpp, Jan, GPT4All, ComfyUI.
              Adding one creates a keyless provider; its model list is fetched automatically.
            </p>
          </div>
          <Button size="sm" variant="outline" onClick={rescan} disabled={scanQ.isFetching}>
            {scanQ.isFetching ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <RefreshCw className="size-3" />
            )}
            Scan
          </Button>
        </div>

        {scanQ.isFetching && servers.length === 0 ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground p-3">
            <Loader2 className="size-3.5 animate-spin" /> Probing loopback ports…
          </div>
        ) : scanQ.error ? (
          <div className="text-xs text-destructive p-3">
            Scan failed: {scanQ.error instanceof Error ? scanQ.error.message : 'unknown error'}
          </div>
        ) : servers.length === 0 ? (
          <div className="text-xs text-muted-foreground p-3 rounded-lg border border-border/60 bg-black/10">
            No local servers found. Start LM Studio (enable the local server), Ollama (
            <code className="font-mono">ollama serve</code>), or a llama.cpp server, then scan again.
          </div>
        ) : (
          <div className="grid gap-2">
            {servers.map((s) => (
              <ServerCard key={`${s.kind}-${s.port}`} server={s} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
