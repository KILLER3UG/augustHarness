/* Local models — the desktop surface for everything that runs on this
 * machine: detected local servers (LM Studio / Ollama / llama.cpp-class),
 * the on-demand llama.cpp runtime (CUDA 12 / CUDA 13 / Vulkan / CPU) that
 * makes GGUFs runnable, GGUF files on disk, and Hugging Face downloads.
 *
 * Reads are polled (scan on open, servers/downloads on short intervals);
 * every write is an explicit user gesture, and provider creation goes
 * through the ordinary provider-create path server-side.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { t } from '@/lib/motion';
import {
  Check,
  Download,
  FolderOpen,
  HardDriveDownload,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  Search,
  Server,
  Square,
  X,
} from 'lucide-react';
import { toast } from 'sonner';
import {
  addLocalServer,
  getLlamaRuntime,
  hfCancelDownload,
  hfListDownloads,
  hfRepoFiles,
  hfSearch,
  hfStartDownload,
  installLlamaRuntime,
  listLlamaServers,
  scanGgufFolder,
  scanLocalServers,
  serveGguf,
  stopLlamaServer,
  type GgufEntry,
  type HfDownload,
  type HfFile,
  type HfModel,
  type LlamaServerRow,
  type LocalServer,
} from '@/api/api-client/local-models';
import { Button } from '@/components/ui/button';
import { openFolderViaTauri } from '@/api/folder';
import { refreshProviderCatalog } from '@/lib/provider-catalog';
import { cn } from '@/lib/utils';

const MODEL_CHIPS_SHOWN = 6;
const CTX_CHOICES = [4096, 8192, 16384, 32768];

function formatBytes(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—';
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`;
}

function Progress({ value, total }: { value: number; total: number }) {
  const pct = total > 0 ? Math.min(100, Math.round((value / total) * 100)) : 0;
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-muted/80"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
    >
      <motion.div
        className="h-full rounded-full bg-primary"
        initial={false}
        animate={{ width: `${pct}%` }}
        transition={t.springSoft}
      />
    </div>
  );
}

/* ── 1. Local servers (LM Studio, Ollama, llama.cpp, ComfyUI…) ──────── */

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

function ServersCard() {
  const scanQ = useQuery({
    queryKey: ['local-models-scan'],
    queryFn: () => scanLocalServers(),
    staleTime: 30_000,
  });

  const servers = scanQ.data?.servers ?? [];

  return (
    <Card
      title="Local servers"
      subtitle="Servers running on this machine — LM Studio, Ollama, llama.cpp, Jan, GPT4All, ComfyUI. Adding one creates a keyless provider; its model list is fetched automatically."
      action={
        <Button size="sm" variant="outline" onClick={() => void scanQ.refetch()} disabled={scanQ.isFetching}>
          {scanQ.isFetching ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
          Scan
        </Button>
      }
    >
      {scanQ.isFetching && servers.length === 0 ? (
        <Row muted>
          <Loader2 className="size-3.5 animate-spin" /> Probing loopback ports…
        </Row>
      ) : scanQ.error ? (
        <Row muted>
          Scan failed: {scanQ.error instanceof Error ? scanQ.error.message : 'unknown error'}
        </Row>
      ) : servers.length === 0 ? (
        <Row muted>
          No local servers found. Start LM Studio (enable the local server), Ollama (
          <code className="font-mono">ollama serve</code>), or a llama.cpp server, then scan again.
        </Row>
      ) : (
        <div className="grid gap-2">
          {servers.map((s) => (
            <ServerCard key={`${s.kind}-${s.port}`} server={s} />
          ))}
        </div>
      )}
    </Card>
  );
}

/* ── 2. llama.cpp runtime (CUDA 12 / 13 / Vulkan / CPU) ─────────────── */

function RuntimeCard() {
  const qc = useQueryClient();
  const [override, setOverride] = useState<string>('');
  const [installing, setInstalling] = useState(false);

  const runtimeQ = useQuery({
    queryKey: ['llama-runtime'],
    queryFn: () => getLlamaRuntime(),
    refetchInterval: (q) => (q.state.data?.install.running ? 1500 : 60_000),
  });
  const rt = runtimeQ.data;
  const install = rt?.install;

  async function startInstall() {
    setInstalling(true);
    try {
      await installLlamaRuntime(override || undefined);
      await qc.invalidateQueries({ queryKey: ['llama-runtime'] });
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Install failed to start');
    } finally {
      setInstalling(false);
    }
  }

  if (!rt) {
    return (
      <Card title="Local runtime" subtitle="llama.cpp engine for serving GGUF models on this machine.">
        <Row muted>
          <Loader2 className="size-3.5 animate-spin" /> Checking…
        </Row>
      </Card>
    );
  }

  if (install?.running) {
    return (
      <Card title="Installing local runtime" subtitle={install.asset ?? undefined}>
        <div className="space-y-1.5">
          <Progress value={install.bytesDone} total={install.totalBytes} />
          <p className="text-2xs text-muted-foreground">
            {formatBytes(install.bytesDone)} / {formatBytes(install.totalBytes)} — the download is
            verified against a pinned SHA256 before anything executes.
          </p>
        </div>
      </Card>
    );
  }

  if (rt.installed) {
    const label = rt.variants.find((v) => v.name === rt.installed)?.label ?? rt.installed;
    return (
      <Card
        title="Local runtime"
        subtitle={`llama.cpp ${rt.tag} · ${label} — installed. Serve a GGUF below; models appear in the composer as providers.`}
      >
        {install?.error && <Row muted>Last install attempt failed: {install.error}</Row>}
      </Card>
    );
  }

  const gpu = rt.suggestion.gpu;
  const suggested = rt.variants.find((v) => v.name === rt.suggestion.variant);

  return (
    <Card
      title="Local runtime"
      subtitle="August has no built-in inference engine — it downloads llama.cpp once (per your GPU) to serve GGUF files. Nothing is bundled in the installer."
    >
      <div className="space-y-3">
        <Row muted>
          {gpu.nvidia
            ? `${gpu.gpuName ?? 'NVIDIA GPU'} — driver supports CUDA ${gpu.cudaVersion ?? 'unknown'}`
            : 'No NVIDIA GPU detected'}
          {' · '}
          {rt.suggestion.auto
            ? `suggesting ${suggested?.label ?? rt.suggestion.variant} (${formatBytes(suggested?.totalBytes)})`
            : ''}
        </Row>
        <p className="text-2xs text-muted-foreground">{rt.suggestion.reason}.</p>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={override}
            onChange={(e) => setOverride(e.target.value)}
            className="h-7 rounded-md border border-border/60 bg-card/60 px-2 text-2xs"
            aria-label="Runtime variant"
          >
            <option value="">Auto ({suggested?.label ?? rt.suggestion.variant})</option>
            {rt.variants.map((v) => (
              <option key={v.name} value={v.name}>
                {v.label} — {formatBytes(v.totalBytes)}
              </option>
            ))}
          </select>
          <Button size="sm" onClick={() => void startInstall()} disabled={installing}>
            {installing ? <Loader2 className="size-3 animate-spin" /> : <HardDriveDownload className="size-3" />}
            Install {override ? rt.variants.find((v) => v.name === override)?.label : ''}
          </Button>
        </div>
        <p className="text-2xs text-muted-foreground">
          {override
            ? rt.variants.find((v) => v.name === override)?.requires
            : suggested
              ? `Requires: ${suggested.requires}. A CUDA install is ${formatBytes(rt.variants.find((v) => v.name === 'cuda-13.4')?.totalBytes)}–${formatBytes(rt.variants.find((v) => v.name === 'cuda-12.4')?.totalBytes)}; Vulkan is ${formatBytes(rt.variants.find((v) => v.name === 'vulkan')?.totalBytes)}.`
              : ''}
        </p>
      </div>
    </Card>
  );
}

/* ── 3. GGUF files on disk + serving ────────────────────────────────── */

function OnDiskCard({ runtimeInstalled }: { runtimeInstalled: boolean }) {
  const qc = useQueryClient();
  const [scanning, setScanning] = useState(false);
  const [entries, setEntries] = useState<GgufEntry[] | null>(null);
  const [root, setRoot] = useState<string>('');
  const [ctx, setCtx] = useState(8192);
  const [servingPath, setServingPath] = useState<string | null>(null);

  const serversQ = useQuery({
    queryKey: ['llama-servers'],
    queryFn: () => listLlamaServers(),
    refetchInterval: (q) =>
      (q.state.data?.servers ?? []).some((s) => s.status === 'starting') ? 2000 : 15_000,
  });
  const servers = useMemo(() => serversQ.data?.servers ?? [], [serversQ.data]);

  // A starting server flipping to ready means its models just became a
  // provider — refresh the catalog once per ready row.
  const refreshed = useRef<Set<string>>(new Set());
  useEffect(() => {
    for (const s of servers) {
      if (s.status === 'ready' && s.providerId && !refreshed.current.has(s.id)) {
        refreshed.current.add(s.id);
        void refreshProviderCatalog(qc);
      }
    }
  }, [servers, qc]);

  async function pickFolder() {
    setScanning(true);
    try {
      const pick = await openFolderViaTauri();
      if (!pick.path) return;
      const res = await scanGgufFolder(pick.path);
      setEntries(res.models);
      setRoot(res.root);
      if (res.models.length === 0) toast.info('No GGUF files found in that folder');
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Scan failed');
    } finally {
      setScanning(false);
    }
  }

  async function serve(entry: GgufEntry) {
    setServingPath(entry.path);
    try {
      await serveGguf(entry.path, ctx);
      toast.success(`Loading ${entry.name}… it appears in the model picker when ready`);
      await qc.invalidateQueries({ queryKey: ['llama-servers'] });
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Could not start the server');
    } finally {
      setServingPath(null);
    }
  }

  async function stop(row: LlamaServerRow) {
    try {
      await stopLlamaServer(row.id);
      toast.success(`${row.name} stopped`);
      await qc.invalidateQueries({ queryKey: ['llama-servers'] });
      await refreshProviderCatalog(qc);
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Could not stop the server');
    }
  }

  return (
    <Card
      title="GGUF files on this machine"
      subtitle="Pick a folder to find GGUF models; serving one starts llama-server on a free port and adds the model to your picker."
      action={
        <Button size="sm" variant="outline" onClick={() => void pickFolder()} disabled={scanning}>
          {scanning ? <Loader2 className="size-3 animate-spin" /> : <FolderOpen className="size-3" />}
          Pick folder
        </Button>
      }
    >
      <div className="flex items-center gap-2 pb-1">
        <span className="text-2xs text-muted-foreground">Context</span>
        <select
          value={ctx}
          onChange={(e) => setCtx(Number(e.target.value))}
          className="h-7 rounded-md border border-border/60 bg-card/60 px-2 text-2xs"
          aria-label="Context size"
        >
          {CTX_CHOICES.map((c) => (
            <option key={c} value={c}>
              {c / 1024}k
            </option>
          ))}
        </select>
      </div>

      {servers.length > 0 && (
        <div className="grid gap-2 pb-1">
          {servers.map((s) => (
            <div
              key={s.id}
              className="rounded-lg border border-border/60 bg-black/10 p-3 flex items-center justify-between gap-3"
            >
              <div className="min-w-0">
                <p className="text-xs font-semibold truncate">{s.name}</p>
                <p className="text-2xs text-muted-foreground">
                  port {s.port} · {s.status === 'starting' ? 'loading model…' : s.status === 'ready' ? 'serving' : 'failed'}
                  {s.status === 'failed' && s.detail ? ` — ${s.detail}` : ''}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {s.status === 'starting' && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
                <Button size="sm" variant="outline" onClick={() => void stop(s)}>
                  <Square className="size-3" /> Stop
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {entries === null ? (
        <Row muted>No folder scanned yet.</Row>
      ) : entries.length === 0 ? (
        <Row muted>No GGUF files under {root}.</Row>
      ) : (
        <div className="grid gap-1.5">
          {entries.map((e) => {
            const running = servers.find((s) => s.ggufPath.toLowerCase() === e.path.toLowerCase());
            return (
              <div
                key={e.path}
                className="rounded-lg border border-border/60 bg-black/10 px-3 py-2 flex items-center justify-between gap-3"
              >
                <div className="min-w-0">
                  <p className="text-xs truncate">
                    {e.name}
                    {e.split && (
                      <span className="ml-1.5 text-2xs text-muted-foreground">
                        split · {e.shards} shards
                      </span>
                    )}
                  </p>
                  <p className="text-2xs text-muted-foreground">{formatBytes(e.size)}</p>
                </div>
                {running ? (
                  <span className="text-2xs text-success-fg shrink-0 flex items-center gap-1">
                    {running.status === 'ready' ? <Check className="size-3" /> : <Loader2 className="size-3 animate-spin" />}
                    {running.status === 'ready' ? 'Serving' : 'Loading'}
                  </span>
                ) : (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void serve(e)}
                    disabled={!runtimeInstalled || servingPath === e.path}
                    title={runtimeInstalled ? undefined : 'Install the local runtime first'}
                  >
                    {servingPath === e.path ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : (
                      <Play className="size-3" />
                    )}
                    Serve
                  </Button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

/* ── 4. Hugging Face search + downloads ─────────────────────────────── */

function DownloadRow({ job }: { job: HfDownload }) {
  const qc = useQueryClient();
  const done = job.status === 'done';

  async function serve() {
    try {
      await serveGguf(job.destPath, 8192);
      toast.success(`Loading ${job.filename.split('/').pop()}…`);
      await qc.invalidateQueries({ queryKey: ['llama-servers'] });
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Could not start the server');
    }
  }

  async function cancel() {
    try {
      await hfCancelDownload(job.jobId);
      await qc.invalidateQueries({ queryKey: ['hf-downloads'] });
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Could not cancel');
    }
  }

  return (
    <div className="rounded-lg border border-border/60 bg-black/10 px-3 py-2 space-y-1.5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-2xs font-mono truncate" title={`${job.repoId}/${job.filename}`}>
          {job.filename.split('/').pop()}
        </p>
        <div className="flex items-center gap-1.5 shrink-0">
          {done ? (
            <>
              <span className="text-2xs text-success-fg flex items-center gap-1">
                <Check className="size-3" /> {formatBytes(job.size)}
              </span>
              {job.filename.toLowerCase().endsWith('.gguf') ? (
                <Button size="sm" variant="outline" onClick={() => void serve()}>
                  <Play className="size-3" /> Serve
                </Button>
              ) : (
                <span className="text-2xs text-muted-foreground">downloaded</span>
              )}
            </>
          ) : job.status === 'downloading' ? (
            <>
              <span className="text-2xs text-muted-foreground">
                {formatBytes(job.bytesDone)} / {formatBytes(job.size)} · {formatBytes(job.speedBps)}/s
              </span>
              <Button size="sm" variant="outline" onClick={() => void cancel()}>
                <X className="size-3" />
              </Button>
            </>
          ) : (
            <span className="text-2xs text-destructive" title={job.error ?? undefined}>
              {job.status}
              {job.error ? `: ${job.error}` : ''}
            </span>
          )}
        </div>
      </div>
      {job.status === 'downloading' && <Progress value={job.bytesDone} total={job.size} />}
    </div>
  );
}

function HuggingFaceCard({ runtimeInstalled }: { runtimeInstalled: boolean }) {
  const qc = useQueryClient();
  const [term, setTerm] = useState('');
  const [hfFilter, setHfFilter] = useState(''); // '' = ALL models on the Hub
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<HfModel[] | null>(null);
  const [openRepo, setOpenRepo] = useState<string | null>(null);
  const [repoFiles, setRepoFiles] = useState<HfFile[] | null>(null);
  const [loadingRepo, setLoadingRepo] = useState(false);
  const [pulling, setPulling] = useState<string | null>(null);

  const downloadsQ = useQuery({
    queryKey: ['hf-downloads'],
    queryFn: () => hfListDownloads(),
    refetchInterval: (q) =>
      (q.state.data?.downloads ?? []).some((d) => d.status === 'downloading') ? 1500 : 20_000,
  });
  const downloads = downloadsQ.data?.downloads ?? [];

  async function doSearch() {
    if (!term.trim()) return;
    setSearching(true);
    try {
      const res = await hfSearch(term.trim(), 20, hfFilter);
      setResults(res.models);
      if (res.models.length === 0) toast.info('Nothing matched on the Hub');
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Search failed');
    } finally {
      setSearching(false);
    }
  }

  async function openRepoFiles(repoId: string) {
    setOpenRepo(repoId === openRepo ? null : repoId);
    setRepoFiles(null);
    if (repoId === openRepo) return;
    setLoadingRepo(true);
    try {
      const res = await hfRepoFiles(repoId);
      setRepoFiles(res.files);
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Could not list files');
      setOpenRepo(null);
    } finally {
      setLoadingRepo(false);
    }
  }

  async function pull(repoId: string, filename: string) {
    setPulling(`${repoId}/${filename}`);
    try {
      await hfStartDownload(repoId, filename);
      toast.success('Download started');
      await qc.invalidateQueries({ queryKey: ['hf-downloads'] });
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Download failed to start');
    } finally {
      setPulling(null);
    }
  }

  const active = downloads.filter((d) => d.status === 'downloading');
  const settled = downloads.filter((d) => d.status !== 'downloading');

  return (
    <Card
      title="From Hugging Face"
      subtitle="Browse every model on the Hub, download any file with resume, and serve GGUFs with one click. Files live under your data folder (kept on update)."
    >
      <div className="flex gap-2">
        <select
          value={hfFilter}
          onChange={(e) => setHfFilter(e.target.value)}
          className="h-8 rounded-md border border-border/60 bg-card/60 px-2 text-2xs"
          aria-label="Model filter"
        >
          <option value="">All models</option>
          <option value="gguf">GGUF only</option>
        </select>
        <input
          value={term}
          onChange={(e) => setTerm(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void doSearch();
          }}
          placeholder="e.g. qwen3 8b instruct gguf"
          className="h-8 flex-1 rounded-md border border-border/60 bg-card/60 px-2.5 text-xs outline-none focus:border-primary"
          aria-label="Search Hugging Face"
        />
        <Button size="sm" onClick={() => void doSearch()} disabled={searching || !term.trim()}>
          {searching ? <Loader2 className="size-3 animate-spin" /> : <Search className="size-3" />}
          Search
        </Button>
      </div>

      {active.length > 0 && (
        <div className="grid gap-1.5 pt-1">
          {active.map((d) => (
            <DownloadRow key={d.jobId} job={d} />
          ))}
        </div>
      )}

      {results !== null && (
        <div className="grid gap-1.5">
          {results.length === 0 && <Row muted>Nothing matched.</Row>}
          {results.map((m) => (
            <div key={m.repoId} className="rounded-lg border border-border/60 bg-black/10 px-3 py-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void openRepoFiles(m.repoId)}
                className="w-full justify-start text-left h-auto px-0 py-0 hover:bg-transparent"
              >
                <span className="min-w-0">
                  <span className="block text-xs font-medium truncate">{m.repoId}</span>
                  <span className="block text-2xs text-muted-foreground">
                    {m.downloads.toLocaleString()} downloads · {m.likes.toLocaleString()} likes
                    {m.pipelineTag ? ` · ${m.pipelineTag}` : ''}
                    {openRepo === m.repoId ? ' · click to collapse' : ' · click for files'}
                  </span>
                </span>
              </Button>
              {openRepo === m.repoId && (
                <div className="mt-2 grid gap-1">
                  {loadingRepo && (
                    <Row muted>
                      <Loader2 className="size-3 animate-spin" /> Listing files…
                    </Row>
                  )}
                  {repoFiles?.length === 0 && <Row muted>No files listed in this repository.</Row>}
                  {repoFiles?.map((f) => {
                    const id = `${m.repoId}/${f.filename}`;
                    return (
                      <div
                        key={f.filename}
                        className="flex items-center justify-between gap-3 rounded border border-border/40 px-2 py-1.5"
                      >
                        <p className="text-2xs font-mono truncate" title={f.filename}>
                          {f.filename}
                        </p>
                        <div className="flex items-center gap-2 shrink-0">
                          {f.filename.toLowerCase().endsWith('.gguf') && (
                            <span className="text-2xs text-success-fg">servable</span>
                          )}
                          <span className="text-2xs text-muted-foreground">{formatBytes(f.size)}</span>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => void pull(m.repoId, f.filename)}
                            disabled={pulling === id}
                          >
                            {pulling === id ? (
                              <Loader2 className="size-3 animate-spin" />
                            ) : (
                              <Download className="size-3" />
                            )}
                            Download
                          </Button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {settled.length > 0 && (
        <div className="grid gap-1.5 pt-1">
          <p className="text-2xs text-muted-foreground">Downloaded files</p>
          {settled.map((d) => (
            <DownloadRow key={d.jobId} job={d} />
          ))}
        </div>
      )}

      {!runtimeInstalled && (results !== null || settled.length > 0) && (
        <Row muted>Install the local runtime above to serve what you download.</Row>
      )}
    </Card>
  );
}

/* ── shared bits ────────────────────────────────────────────────────── */

function Card({
  title,
  subtitle,
  action,
  children,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-border/60 bg-card/60 p-5 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold">{title}</p>
          {subtitle && <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>}
        </div>
        {action}
      </div>
      {children}
    </div>
  );
}

function Row({ children, muted }: { children: React.ReactNode; muted?: boolean }) {
  return (
    <div className={cn('flex items-center gap-2 text-xs p-3 rounded-lg border border-border/60 bg-black/10', muted && 'text-muted-foreground')}>
      {children}
    </div>
  );
}

export function LocalModelsTab() {
  const runtimeQ = useQuery({
    queryKey: ['llama-runtime'],
    queryFn: () => getLlamaRuntime(),
    refetchInterval: (q) => (q.state.data?.install.running ? 1500 : 60_000),
  });
  const runtimeInstalled = Boolean(runtimeQ.data?.installed);

  return (
    <div className="space-y-3 h-full flex flex-col overflow-auto">
      <ServersCard />
      <RuntimeCard />
      <OnDiskCard runtimeInstalled={runtimeInstalled} />
      <HuggingFaceCard runtimeInstalled={runtimeInstalled} />
    </div>
  );
}
