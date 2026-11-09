/* ── Explore — a live Hugging Face browser for local GGUF models ─────────
 *
 * Search hits the Hub through the local proxy (`/api/hf/*`), so the renderer
 * never talks to huggingface.co directly and the Tauri CSP, the per-install
 * token and the error shapes stay in one place. Selecting a repo reads its
 * real file list with exact sizes and its model card; a download is a
 * `/api/hf/downloads` job with progress, cancel and resume.
 *
 * The screen ends where the product actually ends: a downloaded file is
 * useless until it is served, so the primary action walks the real chain —
 * Download → runtime → Serve → Use in New Chat — and each label states the
 * step it performs. A "Downloaded" tick that led nowhere was the old
 * behaviour, and it is why a finished download looked like a failure.
 */

import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  AlertCircle,
  Check,
  Cpu,
  Download,
  ExternalLink,
  Loader2,
  MessageSquarePlus,
  RefreshCw,
  Search,
  Star,
  X,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Markdown } from '@/sections/chat/ChatMarkdown';
import { cn, timeAgo } from '@/lib/utils';
import { t } from '@/lib/motion';
import { useModels } from '@/hooks/useModels';
import { getOrCreateEmptySession, updateSessionModel } from '@/store/sessions';
import {
  getLlamaRuntime,
  hfCancelDownload,
  hfListDownloads,
  hfRepoFiles,
  hfRepoReadme,
  hfSearch,
  hfStartDownload,
  installLlamaRuntime,
  listLlamaServers,
  serveGguf,
  type HfDownload,
  type HfFile,
  type HfSort,
} from '@/api/api-client/local-models';

const SORTS: { value: HfSort; label: string }[] = [
  { value: 'recommended', label: 'Recommended' },
  { value: 'downloads', label: 'Most downloaded' },
  { value: 'likes', label: 'Most liked' },
  { value: 'recent', label: 'Recently updated' },
];

/** The context window a served GGUF starts with. One number in one place: it
 *  is what the VRAM margin below budgets the cache against, so it must not
 *  drift from the value actually passed to the backend. */
const SERVE_CTX_SIZE = 8192;

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`;
}

function formatCount(n: number): string {
  return new Intl.NumberFormat().format(n || 0);
}

/** The file stem llama.cpp serves under (`--alias path.stem`), which is
 *  therefore the id the model picker shows once the server is ready. */
function servedModelId(filename: string): string {
  const base = filename.split('/').pop() ?? filename;
  return base.replace(/\.gguf$/i, '');
}

type OffloadVerdict = {
  tone: 'ok' | 'partial' | 'unknown';
  label: string;
  detail: string;
};

/** GPU-offload estimate from the two numbers that decide it: the file size and
 *  the free VRAM this machine reports right now.
 *
 *  Deliberately not a claim about layer counts — August has not opened the
 *  GGUF. The badge prints the figures it used, so a reader can disagree with
 *  the arithmetic instead of trusting a green tick. */
function offloadVerdict(
  sizeBytes: number,
  gpu: { nvidia: boolean; gpuName?: string | null; vramFreeMb?: number | null },
): OffloadVerdict {
  if (!gpu.nvidia) {
    return {
      tone: 'unknown',
      label: 'No NVIDIA GPU detected',
      detail: 'Runs on the CPU unless a Vulkan runtime is installed.',
    };
  }
  const free = gpu.vramFreeMb ?? null;
  if (!free || free <= 0 || sizeBytes <= 0) {
    return {
      tone: 'unknown',
      label: 'GPU offload unknown',
      detail: `${gpu.gpuName ?? 'NVIDIA GPU'} — free memory not reported.`,
    };
  }
  // Keep 1 GB of the free pool for the KV cache at SERVE_CTX_SIZE plus the
  // desktop itself. A model needing more than what is left is a partial
  // offload, and calling it "full" would be a promise about a server August
  // has not started.
  const kvMarginMb = 1024;
  if (sizeBytes / 1e6 <= free - kvMarginMb) {
    return {
      tone: 'ok',
      label: 'Full GPU offload possible',
      detail: `${formatBytes(sizeBytes)} model · ${formatCount(Math.round(free))} MiB free on ${
        gpu.gpuName ?? 'NVIDIA GPU'
      }`,
    };
  }
  return {
    tone: 'partial',
    label: 'Partial GPU offload',
    detail: `${formatBytes(sizeBytes)} model · ${formatCount(Math.round(free))} MiB free — some layers stay on the CPU`,
  };
}

function ProgressBar({ value, total }: { value: number; total: number }) {
  const pct = total > 0 ? Math.min(100, Math.round((value / total) * 100)) : 0;
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-muted/80"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
    >
      {/* framer-motion owns the width: an inline style object here would
          bypass the theme tokens and trip the design-drift gate. */}
      <motion.div
        className="h-full rounded-full bg-primary"
        initial={false}
        animate={{ width: `${pct}%` }}
        transition={t.springSoft}
      />
    </div>
  );
}

function ModelRow({
  repoId,
  author,
  downloads,
  likes,
  lastModified,
  selected,
  job,
  onSelect,
}: {
  repoId: string;
  author: string;
  downloads: number;
  likes: number;
  lastModified: string;
  selected: boolean;
  job: HfDownload | null;
  onSelect: () => void;
}) {
  const name = repoId.split('/').slice(1).join('/') || repoId;
  const busy = job?.status === 'downloading';
  const done = job?.status === 'done';
  return (
    <Button
      type="button"
      variant="bare"
      onClick={onSelect}
      aria-current={selected ? 'true' : undefined}
      className={cn(
        'w-full gap-3 rounded-lg px-3 py-2.5 text-left',
        selected ? 'bg-primary text-primary-foreground' : 'hover:bg-muted/50',
      )}
      data-testid={`explore-row-${repoId}`}
    >
      <span
        aria-hidden
        className={cn(
          'grid size-9 shrink-0 place-items-center rounded-lg text-base',
          selected ? 'bg-primary-foreground/15' : 'bg-muted/60',
        )}
      >
        🤗
      </span>
      <span className="min-w-0 flex-1">
        <span
          className={cn(
            'block truncate text-sm font-medium',
            selected ? 'text-primary-foreground' : 'text-foreground',
          )}
        >
          {name}
        </span>
        <span
          className={cn(
            'block truncate text-2xs',
            selected ? 'text-primary-foreground/80' : 'text-muted-foreground',
          )}
        >
          {author}
        </span>
      </span>
      <span
        className={cn(
          'flex shrink-0 flex-col items-end gap-0.5 text-2xs tabular-nums',
          selected ? 'text-primary-foreground/85' : 'text-muted-foreground',
        )}
      >
        <span className="inline-flex items-center gap-1">
          <Star className="size-3" aria-hidden />
          {formatCount(likes)}
        </span>
        <span className="inline-flex items-center gap-1">
          <Download className="size-3" aria-hidden />
          {formatCount(downloads)}
        </span>
        <span className="inline-flex items-center gap-1">
          {busy ? <Loader2 className="size-3 animate-spin" aria-hidden /> : null}
          {done && !busy ? <Check className="size-3" aria-hidden /> : null}
          {lastModified ? timeAgo(lastModified) : '—'}
        </span>
      </span>
    </Button>
  );
}

export function ExploreSection() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const [sort, setSort] = useState<HfSort>('recommended');
  const [selectedRepo, setSelectedRepo] = useState('');
  const [fileChoice, setFileChoice] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);
  const [serving, setServing] = useState(false);

  useEffect(() => {
    const id = setTimeout(() => setDebounced(query.trim()), 300);
    return () => clearTimeout(id);
  }, [query]);

  const searchQ = useQuery({
    queryKey: ['hf-search', debounced, sort],
    queryFn: () => hfSearch(debounced, 40, 'gguf', sort),
    placeholderData: (prev) => prev,
    staleTime: 60_000,
    retry: 1,
  });

  const detailQ = useQuery({
    queryKey: ['hf-repo', selectedRepo],
    queryFn: () => hfRepoFiles(selectedRepo),
    enabled: !!selectedRepo,
    staleTime: 60_000,
    retry: 1,
  });

  const readmeQ = useQuery({
    queryKey: ['hf-readme', selectedRepo],
    queryFn: () => hfRepoReadme(selectedRepo),
    enabled: !!selectedRepo,
    staleTime: 5 * 60_000,
    retry: 1,
  });

  // ONE key, shared with Local Models and with the backend's own push
  // (`emit_invalidate("hf-downloads")`). This screen used to subscribe
  // `explore-downloads`, so a job that finished anywhere else never reached it
  // except by polling.
  const downloadsQ = useQuery({
    queryKey: ['hf-downloads'],
    queryFn: hfListDownloads,
    refetchInterval: (q) =>
      (q.state.data?.downloads ?? []).some((d) => d.status === 'downloading') ? 1000 : 10_000,
    retry: 1,
  });

  const serversQ = useQuery({
    queryKey: ['llama-servers'],
    queryFn: listLlamaServers,
    refetchInterval: (q) =>
      (q.state.data?.servers ?? []).some((s) => s.status === 'starting') ? 1500 : 8000,
  });

  const runtimeQ = useQuery({
    queryKey: ['llama-runtime'],
    queryFn: getLlamaRuntime,
    staleTime: 30_000,
  });

  const { models, refreshCatalog } = useModels();

  const results = useMemo(() => searchQ.data?.models ?? [], [searchQ.data?.models]);
  const downloads = useMemo(() => downloadsQ.data?.downloads ?? [], [downloadsQ.data?.downloads]);
  const servers = useMemo(() => serversQ.data?.servers ?? [], [serversQ.data?.servers]);
  const serveable: HfFile[] = useMemo(
    () => (detailQ.data?.files ?? []).filter((f) => f.serveable),
    [detailQ.data?.files],
  );

  // The file this repo is about: the caller's pick, else the repo's
  // recommended single-file quant.
  const chosenFile = useMemo(() => {
    if (!selectedRepo) return null;
    const name = fileChoice || detailQ.data?.recommended || '';
    return serveable.find((f) => f.filename === name) ?? serveable[0] ?? null;
  }, [selectedRepo, fileChoice, detailQ.data?.recommended, serveable]);

  const jobFor = useMemo(() => {
    if (!selectedRepo) return null;
    const rows = downloads.filter((d) => d.repoId === selectedRepo);
    if (!rows.length) return null;
    return rows.find((d) => d.filename === chosenFile?.filename) ?? rows[0] ?? null;
  }, [downloads, selectedRepo, chosenFile?.filename]);

  const offload = offloadVerdict(
    chosenFile?.size ?? jobFor?.size ?? 0,
    runtimeQ.data?.suggestion?.gpu ?? { nvidia: false },
  );

  const servedRow = servers.find(
    (s) => s.ggufPath && jobFor && s.ggufPath.replace(/\\/g, '/').endsWith(jobFor.filename),
  );
  const servedReady = servedRow?.status === 'ready';
  const runtimeInstalled = !!runtimeQ.data?.installed;

  const chatModel = useMemo(() => {
    const want = servedModelId(chosenFile?.filename ?? jobFor?.filename ?? '');
    const local = models.filter((m) => (m.provider || '').toLowerCase() === 'localmodels');
    return local.find((m) => m.id === want) ?? local[0] ?? null;
  }, [models, chosenFile?.filename, jobFor?.filename]);

  async function startDownload() {
    if (!selectedRepo || !chosenFile) return;
    setActionError(null);
    try {
      await hfStartDownload(selectedRepo, chosenFile.filename);
      toast.success(
        `Downloading ${chosenFile.filename.split('/').pop()} · ${formatBytes(chosenFile.size)}`,
      );
      await downloadsQ.refetch();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Download failed';
      setActionError(message);
      toast.error(message);
    }
  }

  async function cancelDownload() {
    if (!jobFor) return;
    try {
      await hfCancelDownload(jobFor.jobId);
      await downloadsQ.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not cancel download');
    }
  }

  async function installRuntime() {
    setActionError(null);
    try {
      await installLlamaRuntime();
      toast.success('Downloading the llama.cpp runtime — a one-time install.');
      await runtimeQ.refetch();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Runtime install failed';
      setActionError(message);
      toast.error(message);
    }
  }

  async function serve() {
    if (!jobFor?.destPath) return;
    setServing(true);
    setActionError(null);
    try {
      await serveGguf(jobFor.destPath, SERVE_CTX_SIZE);
      toast.success(`Loading ${servedModelId(jobFor.filename)}…`);
      await serversQ.refetch();
      await refreshCatalog();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not start the local server';
      setActionError(message);
      toast.error(message);
    } finally {
      setServing(false);
    }
  }

  function startChatWithModel() {
    if (!chatModel) {
      toast.error('The model is not in the catalog yet — it appears once the server is ready.');
      return;
    }
    const sess = getOrCreateEmptySession();
    updateSessionModel(sess.id, chatModel.id, chatModel.provider);
    void qc.invalidateQueries({ queryKey: ['aggregated-models'] });
    void navigate(`/c/${sess.id}`);
  }

  function selectRepo(repoId: string) {
    setSelectedRepo(repoId);
    setFileChoice('');
    setActionError(null);
  }

  /** One primary action that states the step it performs. The order is the
   *  real dependency chain: a file must be on disk before it can be served,
   *  and a runtime must exist before anything can be served. */
  const primary = (() => {
    if (!selectedRepo) return null;
    if (jobFor?.status === 'downloading') {
      return { kind: 'cancel' as const, label: 'Cancel', disabled: false };
    }
    if (jobFor?.status !== 'done') {
      return {
        kind: 'download' as const,
        label: chosenFile ? `Download ${formatBytes(chosenFile.size)}` : 'Download',
        disabled: !chosenFile || detailQ.isPending,
      };
    }
    if (!runtimeInstalled) {
      return { kind: 'runtime' as const, label: 'Install llama.cpp runtime', disabled: false };
    }
    if (servedReady) {
      return { kind: 'chat' as const, label: 'Use in New Chat', disabled: !chatModel };
    }
    if (servedRow?.status === 'starting') {
      return { kind: 'wait' as const, label: 'Loading model…', disabled: true };
    }
    if (servedRow?.status === 'failed') {
      return { kind: 'serve' as const, label: 'Serve again', disabled: serving };
    }
    return { kind: 'serve' as const, label: 'Serve locally', disabled: serving };
  })();

  const runPrimary = () => {
    if (!primary) return;
    if (primary.kind === 'download') void startDownload();
    else if (primary.kind === 'cancel') void cancelDownload();
    else if (primary.kind === 'runtime') void installRuntime();
    else if (primary.kind === 'serve') void serve();
    else if (primary.kind === 'chat') startChatWithModel();
  };

  const summaryOf = (field: 'downloads' | 'likes') =>
    detailQ.data?.[field] ?? results.find((r) => r.repoId === selectedRepo)?.[field] ?? 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="shrink-0 px-6 pb-3 pt-5">
        <h2 className="text-xl font-semibold tracking-tight text-foreground">Explore</h2>
        <div className="mt-3 flex items-center gap-2">
          <div className="relative min-w-0 flex-1">
            <Search
              className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              type="text"
              inputMode="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search Hugging Face — qwen, gemma, 4B, coding…"
              aria-label="Search Hugging Face models"
              className="h-9 pl-8 pr-8 text-xs"
              data-testid="explore-search"
            />
            {query ? (
              <Button
                type="button"
                size="icon-sm"
                variant="ghost"
                onClick={() => setQuery('')}
                className="absolute right-1 top-1/2 -translate-y-1/2"
                aria-label="Clear search"
              >
                <X className="size-3" aria-hidden />
              </Button>
            ) : null}
          </div>
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            disabled={searchQ.isFetching}
            onClick={() => void searchQ.refetch()}
            aria-label="Refresh results"
          >
            <RefreshCw
              className={cn('size-3.5', searchQ.isFetching && 'animate-spin')}
              aria-hidden
            />
          </Button>
        </div>
        <div className="mt-2 flex items-center justify-between gap-3 text-2xs text-muted-foreground">
          <span>
            {searchQ.isPending
              ? 'Searching Hugging Face…'
              : `${formatCount(results.length)} Hugging Face result${results.length === 1 ? '' : 's'}`}
            <span className="ml-1 opacity-70">(GGUF only — what August can serve)</span>
          </span>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as HfSort)}
            aria-label="Sort results"
            className="h-7 rounded-md border border-border/60 bg-background px-2 text-2xs text-foreground"
            data-testid="explore-sort"
          >
            {SORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
      </header>

      {searchQ.isError ? (
        <div className="mx-6 mb-2 flex items-center justify-between gap-2 rounded-lg border border-danger/40 bg-danger/5 px-3 py-2 text-xs text-danger-fg">
          <span className="inline-flex min-w-0 items-center gap-2">
            <AlertCircle className="size-3 shrink-0" aria-hidden />
            Hugging Face search failed — the Hub may be unreachable or rate-limiting.
          </span>
          <Button type="button" size="sm" variant="outline" onClick={() => void searchQ.refetch()}>
            Retry
          </Button>
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1">
        <div className="flex w-[22rem] shrink-0 flex-col border-r border-border/60">
          <div className="min-h-0 flex-1 space-y-0.5 overflow-auto px-2 pb-4">
            {results.map((m) => (
              <ModelRow
                key={m.repoId}
                repoId={m.repoId}
                author={m.author}
                downloads={m.downloads}
                likes={m.likes}
                lastModified={m.lastModified}
                selected={m.repoId === selectedRepo}
                job={downloads.find((d) => d.repoId === m.repoId) ?? null}
                onSelect={() => selectRepo(m.repoId)}
              />
            ))}
            {!searchQ.isPending && results.length === 0 ? (
              <p className="px-3 py-6 text-center text-xs text-muted-foreground">
                No GGUF repositories match “{debounced}”.
              </p>
            ) : null}
          </div>
        </div>

        <div className="min-w-0 flex-1 overflow-auto px-6 pb-8">
          {!selectedRepo ? (
            <div className="mt-16 text-center text-sm text-muted-foreground">
              Pick a model to see its files, sizes, capabilities and model card.
            </div>
          ) : (
            <div className="space-y-5">
              <div className="flex items-start gap-4">
                <span aria-hidden className="text-3xl leading-9">
                  🤗
                </span>
                <div className="min-w-0 flex-1">
                  <h3 className="truncate text-lg font-semibold text-foreground">
                    {(detailQ.data?.repoId ?? selectedRepo).split('/').slice(1).join('/') ||
                      selectedRepo}
                  </h3>
                  <p className="truncate text-xs text-muted-foreground">{selectedRepo}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-3 text-2xs tabular-nums text-muted-foreground">
                    <span className="inline-flex items-center gap-1">
                      <Download className="size-3" aria-hidden />
                      {formatCount(summaryOf('downloads'))}
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <Star className="size-3" aria-hidden />
                      {formatCount(summaryOf('likes'))}
                    </span>
                    {detailQ.data?.lastModified ? (
                      <span>Updated {timeAgo(detailQ.data.lastModified)}</span>
                    ) : null}
                  </div>
                </div>
                <a
                  href={`https://huggingface.co/${selectedRepo}`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border/60 px-2.5 py-1.5 text-2xs text-foreground hover:bg-accent"
                >
                  Open on Web <ExternalLink className="size-3" aria-hidden />
                </a>
              </div>

              <section>
                <div className="flex items-center justify-between gap-3">
                  <h4 className="text-2xs font-semibold uppercase tracking-widest text-muted-foreground">
                    Download Options
                  </h4>
                  <span className="text-2xs text-muted-foreground">
                    Download to <span className="font-medium text-foreground">This device</span>
                  </span>
                </div>
                {detailQ.isPending ? (
                  <p className="mt-2 inline-flex items-center gap-2 text-xs text-muted-foreground">
                    <Loader2 className="size-3 animate-spin" aria-hidden /> Loading download
                    options… <span className="text-2xs">Finding best options…</span>
                  </p>
                ) : detailQ.isError ? (
                  <p className="mt-2 inline-flex items-center gap-2 text-xs text-danger-fg">
                    <AlertCircle className="size-3" aria-hidden />
                    {detailQ.error instanceof Error
                      ? detailQ.error.message
                      : 'Could not read the file list.'}
                  </p>
                ) : serveable.length === 0 ? (
                  <p className="mt-2 text-xs text-muted-foreground">
                    No single-file GGUF here. Sharded files and safetensors are listed on Hugging
                    Face instead — August serves one file at a time.
                  </p>
                ) : (
                  <>
                    <div className="mt-2 overflow-hidden rounded-lg border border-border/60">
                      <ul className="divide-y divide-border/40" data-testid="explore-file-select">
                        {serveable.map((f) => {
                          const isRecommended = f.filename === detailQ.data?.recommended;
                          const active = f.filename === chosenFile?.filename;
                          return (
                            <li key={f.filename}>
                              <label
                                className={cn(
                                  'flex cursor-pointer items-center gap-3 px-3 py-2 text-xs',
                                  active ? 'bg-primary/10' : 'hover:bg-muted/30',
                                )}
                              >
                                <input
                                  type="radio"
                                  name="explore-gguf"
                                  className="size-3 shrink-0 accent-primary"
                                  checked={active}
                                  onChange={() => setFileChoice(f.filename)}
                                  aria-label={`Choose ${f.filename}`}
                                />
                                <span className="shrink-0 rounded border border-border/60 bg-muted/40 px-1.5 py-0.5 font-mono text-2xs text-muted-foreground">
                                  GGUF
                                </span>
                                <span className="min-w-0 flex-1 truncate text-foreground">
                                  {f.quantization || f.filename}
                                </span>
                                {isRecommended ? (
                                  <span className="shrink-0 rounded bg-primary/15 px-1.5 py-0.5 text-2xs font-medium text-primary">
                                    Recommended
                                  </span>
                                ) : null}
                                <span className="shrink-0 tabular-nums text-2xs text-muted-foreground">
                                  {formatBytes(f.size)}
                                </span>
                              </label>
                            </li>
                          );
                        })}
                      </ul>
                    </div>

                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <span
                        className={cn(
                          'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-2xs',
                          offload.tone === 'ok'
                            ? 'bg-success/15 text-success-fg'
                            : offload.tone === 'partial'
                              ? 'bg-warning/15 text-warning-fg'
                              : 'bg-muted/60 text-muted-foreground',
                        )}
                      >
                        <Cpu className="size-3" aria-hidden />
                        {offload.label}
                      </span>
                      <span className="text-2xs text-muted-foreground">{offload.detail}</span>
                    </div>

                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <Button
                        type="button"
                        size="sm"
                        disabled={primary?.disabled ?? true}
                        onClick={runPrimary}
                        data-testid="explore-primary-action"
                      >
                        {primary?.kind === 'download' ? (
                          <Download className="size-3" aria-hidden />
                        ) : null}
                        {primary?.kind === 'chat' ? (
                          <MessageSquarePlus className="size-3" aria-hidden />
                        ) : null}
                        {primary?.kind === 'serve' && serving ? (
                          <Loader2 className="size-3 animate-spin" aria-hidden />
                        ) : null}
                        {primary?.kind === 'wait' ? (
                          <Loader2 className="size-3 animate-spin" aria-hidden />
                        ) : null}
                        {primary?.label ?? 'Select a file'}
                      </Button>
                      {jobFor?.status === 'done' ? (
                        <span className="inline-flex items-center gap-1 text-2xs text-success-fg">
                          <Check className="size-3" aria-hidden /> Downloaded
                        </span>
                      ) : null}
                      {servedReady ? (
                        <span className="text-2xs text-muted-foreground">
                          Serving on port {servedRow?.port} as provider LocalModels
                        </span>
                      ) : null}
                      {runtimeQ.data?.install?.running ? (
                        <span className="text-2xs text-muted-foreground">
                          Runtime install: {formatBytes(runtimeQ.data.install.bytesDone)} /{' '}
                          {formatBytes(runtimeQ.data.install.totalBytes)}
                        </span>
                      ) : null}
                    </div>

                    {jobFor?.status === 'downloading' ? (
                      <div className="mt-3 space-y-1">
                        <div className="flex items-center justify-between text-2xs text-muted-foreground">
                          <span>{jobFor.filename.split('/').pop()}</span>
                          <span>
                            {formatBytes(jobFor.bytesDone)} / {formatBytes(jobFor.size)}
                            {jobFor.speedBps ? ` · ${formatBytes(jobFor.speedBps)}/s` : ''}
                          </span>
                        </div>
                        <ProgressBar value={jobFor.bytesDone} total={jobFor.size} />
                      </div>
                    ) : null}
                  </>
                )}
              </section>

              {actionError || jobFor?.error ? (
                <div className="flex items-center justify-between gap-2 rounded-md border border-danger/40 bg-danger/5 px-2.5 py-2 text-2xs text-danger-fg">
                  <span className="block min-w-0 truncate">{actionError ?? jobFor?.error}</span>
                  <Button type="button" size="sm" variant="ghost" onClick={runPrimary}>
                    Retry
                  </Button>
                </div>
              ) : null}

              <section>
                <h4 className="text-2xs font-semibold uppercase tracking-widest text-muted-foreground">
                  Details
                </h4>
                <div className="mt-2 rounded-lg border border-border/60 bg-card/40 p-3 text-xs">
                  <div className="flex flex-wrap items-center gap-1.5 text-2xs text-muted-foreground">
                    <span className="w-24 shrink-0">Formats</span>
                    {(detailQ.data?.formats ?? []).length ? (
                      (detailQ.data?.formats ?? []).map((f) => (
                        <span
                          key={f}
                          className="rounded border border-border/60 bg-muted/40 px-1.5 py-0.5 font-mono text-2xs text-foreground/80"
                        >
                          {f}
                        </span>
                      ))
                    ) : (
                      <span>—</span>
                    )}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 text-2xs text-muted-foreground">
                    <span className="w-24 shrink-0">Capabilities</span>
                    {(detailQ.data?.capabilities ?? []).length ? (
                      (detailQ.data?.capabilities ?? []).map((c) => (
                        <span
                          key={c}
                          className="rounded-full border border-border/60 bg-muted/40 px-2 py-0.5 text-2xs text-foreground/80"
                        >
                          {c}
                        </span>
                      ))
                    ) : (
                      <span className="rounded-full border border-border/60 bg-muted/40 px-2 py-0.5 text-2xs text-foreground/80">
                        Text
                      </span>
                    )}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 text-2xs text-muted-foreground">
                    <span className="w-24 shrink-0">Pipeline</span>
                    <span className="text-foreground/80">{detailQ.data?.pipelineTag || '—'}</span>
                    <span className="ml-4 w-24 shrink-0">License</span>
                    <span className="text-foreground/80">{detailQ.data?.license || '—'}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 text-2xs text-muted-foreground">
                    <span className="w-24 shrink-0">Saved to</span>
                    <span className="min-w-0 truncate text-foreground/80" title={jobFor?.destPath}>
                      {jobFor?.destPath ?? 'This device — your August data folder'}
                    </span>
                  </div>
                </div>
              </section>

              <section>
                <h4 className="text-2xs font-semibold uppercase tracking-widest text-muted-foreground">
                  README
                </h4>
                {readmeQ.isPending ? (
                  <p className="mt-2 inline-flex items-center gap-2 text-xs text-muted-foreground">
                    <Loader2 className="size-3 animate-spin" aria-hidden /> Loading the model card…
                  </p>
                ) : readmeQ.isError ? (
                  <p className="mt-2 text-xs text-muted-foreground">Model card unavailable.</p>
                ) : (readmeQ.data?.markdown ?? '').trim() ? (
                  <div className="mt-2 rounded-lg border border-border/60 bg-card/40 p-4">
                    <Markdown content={readmeQ.data?.markdown ?? ''} demoteHeadings />
                    {readmeQ.data?.truncated ? (
                      <p className="mt-2 text-2xs text-muted-foreground">
                        Model card truncated — open it on Hugging Face to read the rest.
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <p className="mt-2 text-xs text-muted-foreground">This repo has no README.</p>
                )}
              </section>

              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border/60 bg-muted/30 px-4 py-3 text-2xs text-muted-foreground">
                <span>
                  Adding a running server (Ollama, LM Studio, llama.cpp) or serving a GGUF you
                  already have? Those live in Local Models.
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="link"
                  onClick={() => void navigate('/settings/model-local')}
                >
                  Open Local Models
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
