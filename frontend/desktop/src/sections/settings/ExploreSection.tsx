/* ── Explore — a browsable catalog of downloadable local models ────────
 *
 * The catalog is a static JSON file bundled with the desktop build (and
 * checked into this repository on GitHub), so browsing works with no network
 * at all. Downloads go through the existing local proxy routes — the renderer
 * never talks to GitHub or Hugging Face directly, which keeps the Tauri CSP,
 * the per-install token and the error shapes in one place.
 *
 * Search is client-side and debounced; every download is a real
 * `/api/hf/downloads` job with progress, cancel and error states. */

import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  AlertCircle,
  Check,
  Download,
  ExternalLink,
  Github,
  HardDrive,
  Loader2,
  RefreshCw,
  Search,
  X,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import { t } from '@/lib/motion';
import {
  hfCancelDownload,
  hfListDownloads,
  hfRepoFiles,
  hfStartDownload,
  type HfDownload,
} from '@/api/api-client/local-models';
import CATALOG from './model-catalog.json';

type CatalogModel = (typeof CATALOG.models)[number];

/** Searchable text for one entry. Kept here so the filter cannot drift from
 *  what the row actually shows. */
function searchable(model: CatalogModel): string {
  return [
    model.name,
    model.family,
    model.parameters,
    model.quantization,
    model.sizeLabel,
    model.sizeBucket,
    model.repoId,
    model.description,
    ...model.tags,
  ]
    .join(' ')
    .toLowerCase();
}

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

/** A catalog entry names a repo + a preferred quant, not a guaranteed
 *  filename — repos rename files. Ask the backend for the real file list and
 *  pick the closest single-file GGUF before starting a job. */
async function resolveFilename(model: CatalogModel): Promise<string> {
  const { files } = await hfRepoFiles(model.repoId);
  const single = files.filter(
    (f) => /\.gguf$/i.test(f.filename) && !/\d+-of-\d+/i.test(f.filename),
  );
  const exact = single.find((f) => f.filename === model.filename);
  const quant = single.find((f) =>
    f.filename.toUpperCase().includes(model.quantization.toUpperCase()),
  );
  const chosen = exact ?? quant ?? single[0];
  if (!chosen) {
    throw new Error(`No single-file GGUF found in ${model.repoId}`);
  }
  return chosen.filename;
}

function findJob(model: CatalogModel, downloads: HfDownload[]): HfDownload | null {
  const candidates = downloads.filter((d) => d.repoId === model.repoId);
  return (
    candidates.find((d) =>
      d.filename.toUpperCase().includes(model.quantization.toUpperCase()),
    ) ??
    candidates.find((d) => d.filename === model.filename) ??
    null
  );
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

interface ModelRowProps {
  model: CatalogModel;
  job: HfDownload | null;
  resolving: boolean;
  actionError: string | null;
  onDownload: () => void;
  onCancel: () => void;
}

function ModelRow({
  model,
  job,
  resolving,
  actionError,
  onDownload,
  onCancel,
}: ModelRowProps) {
  const downloading = job?.status === 'downloading';
  const done = job?.status === 'done';
  const failed = job?.status === 'failed' || job?.status === 'interrupted' || !!actionError;
  const errorText = actionError ?? job?.error ?? null;

  return (
    <div className="rounded-xl border border-border/60 bg-card/50 p-4">
      <div className="flex min-w-0 items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="block min-w-0 truncate text-sm font-semibold text-foreground">
              {model.name}
            </span>
            <span className="shrink-0 rounded border border-border/60 bg-muted/40 px-1.5 py-0.5 font-mono text-2xs text-muted-foreground">
              {model.quantization}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">{model.description}</p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-2xs text-muted-foreground">
            <span className="rounded bg-muted/60 px-1.5 py-0.5">{model.family}</span>
            <span className="rounded bg-muted/60 px-1.5 py-0.5">{model.parameters}</span>
            <span className="rounded bg-muted/60 px-1.5 py-0.5">{model.sizeLabel}</span>
            {model.tags.slice(0, 3).map((tag) => (
              <span key={tag} className="rounded bg-muted/60 px-1.5 py-0.5">
                {tag}
              </span>
            ))}
          </div>
          <div className="mt-2 flex min-w-0 items-center gap-2 text-2xs text-muted-foreground">
            <Github className="size-3 shrink-0" aria-hidden />
            <span className="block min-w-0 truncate">{model.repoId}</span>
            <a
              href={model.sourceUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex shrink-0 items-center gap-0.5 text-primary hover:underline"
              aria-label={`Open the upstream project for ${model.name}`}
            >
              Source <ExternalLink className="size-3" aria-hidden />
            </a>
          </div>
        </div>

        <div className="flex shrink-0 flex-col items-end gap-1.5">
          {downloading && job ? (
            <Button type="button" size="sm" variant="outline" onClick={onCancel}>
              <X className="size-3" aria-hidden />
              Cancel
            </Button>
          ) : done ? (
            <span className="inline-flex items-center gap-1 text-xs text-success-fg">
              <Check className="size-3" aria-hidden /> Downloaded
            </span>
          ) : (
            <Button
              type="button"
              size="sm"
              disabled={resolving}
              onClick={onDownload}
            >
              {resolving || failed ? (
                <RefreshCw className={cn('size-3', resolving && 'animate-spin')} aria-hidden />
              ) : (
                <Download className="size-3" aria-hidden />
              )}
              {resolving ? 'Resolving…' : failed ? 'Retry' : 'Download'}
            </Button>
          )}
          <span className="text-2xs text-muted-foreground">{model.license}</span>
        </div>
      </div>

      {downloading && job ? (
        <div className="mt-3 space-y-1">
          <div className="flex items-center justify-between text-2xs text-muted-foreground">
            <span>{job.filename.split('/').pop()}</span>
            <span>
              {formatBytes(job.bytesDone)} / {formatBytes(job.size)}
              {job.speedBps ? ` · ${formatBytes(job.speedBps)}/s` : ''}
            </span>
          </div>
          <ProgressBar value={job.bytesDone} total={job.size} />
        </div>
      ) : null}

      {failed && errorText ? (
        <div className="mt-3 flex items-center justify-between gap-2 rounded-md border border-danger/40 bg-danger/5 px-2.5 py-2 text-2xs text-danger-fg">
          <span className="block min-w-0 truncate">{errorText}</span>
          <Button type="button" size="sm" variant="ghost" onClick={onDownload}>
            Retry
          </Button>
        </div>
      ) : null}
    </div>
  );
}

export function ExploreSection() {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [resolvingId, setResolvingId] = useState<string | null>(null);
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({});

  // One debounce, one owner. Clearing the timer on every keystroke means a
  // fast typer issues exactly one filter pass, and there is no second fetch
  // path that can race it.
  useEffect(() => {
    const id = setTimeout(() => setDebouncedQuery(query.trim().toLowerCase()), 200);
    return () => clearTimeout(id);
  }, [query]);

  const filtered = useMemo(() => {
    const tokens = debouncedQuery.split(/\s+/).filter(Boolean);
    if (tokens.length === 0) return CATALOG.models;
    return CATALOG.models.filter((model) => {
      const haystack = searchable(model);
      return tokens.every((token) => haystack.includes(token));
    });
  }, [debouncedQuery]);

  const downloadsQ = useQuery({
    queryKey: ['explore-downloads'],
    queryFn: hfListDownloads,
    refetchInterval: (q) =>
      (q.state.data?.downloads ?? []).some((d) => d.status === 'downloading')
        ? 1000
        : 5000,
    retry: 1,
  });

  const downloads = downloadsQ.data?.downloads ?? [];

  async function handleDownload(model: CatalogModel) {
    setActionErrors((prev) => ({ ...prev, [model.id]: '' }));
    setResolvingId(model.id);
    try {
      const filename = await resolveFilename(model);
      await hfStartDownload(model.repoId, filename);
      toast.success(`Downloading ${model.name} · ${model.quantization}`);
      await downloadsQ.refetch();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Download failed';
      setActionErrors((prev) => ({ ...prev, [model.id]: message }));
      toast.error(message);
    } finally {
      setResolvingId(null);
    }
  }

  async function handleCancel(jobId: string) {
    try {
      await hfCancelDownload(jobId);
      await downloadsQ.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not cancel download');
    }
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-start justify-between gap-4 px-6 py-5">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold tracking-tight text-foreground">
            Explore models
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Pick a GGUF model to download and run locally. Search by name,
            family, size, quantization or tag.
          </p>
          <p className="mt-1 text-2xs text-muted-foreground">
            Catalog v{CATALOG.version} · bundled with August, so browsing works
            offline. Downloads need a connection.
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={downloadsQ.isFetching}
          onClick={() => void downloadsQ.refetch()}
        >
          <RefreshCw
            className={cn('size-3', downloadsQ.isFetching && 'animate-spin')}
            aria-hidden
          />
          Refresh
        </Button>
      </header>

      <div className="shrink-0 px-6 pb-3">
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            type="text"
            inputMode="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search models — qwen, 7B, Q4_K_M, small, coding…"
            aria-label="Search downloadable models"
            className="h-9 pl-8 pr-8 text-xs"
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
        <div className="mt-2 flex items-center justify-between text-2xs text-muted-foreground">
          <span>
            {filtered.length} of {CATALOG.models.length} models
          </span>
          <span className="inline-flex items-center gap-1">
            <HardDrive className="size-3" aria-hidden />
            Files are saved to your data folder and kept on update
          </span>
        </div>
      </div>

      {/* Network-dependent state: the catalog itself never needs the
          network, but the download list does. Give it an honest loading,
          error + retry, and empty surface. */}
      {downloadsQ.isPending ? (
        <div className="mx-6 mb-3 flex items-center gap-2 rounded-lg border border-border/60 bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          <Loader2 className="size-3 animate-spin" aria-hidden />
          Checking local downloads…
        </div>
      ) : null}
      {downloadsQ.isError ? (
        <div className="mx-6 mb-3 flex items-center justify-between gap-2 rounded-lg border border-danger/40 bg-danger/5 px-3 py-2 text-xs text-danger-fg">
          <span className="inline-flex items-center gap-2">
            <AlertCircle className="size-3 shrink-0" aria-hidden />
            Could not reach the local download service. The catalog is still
            browsable offline.
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void downloadsQ.refetch()}
          >
            Retry
          </Button>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-auto px-6 pb-6">
        {filtered.length === 0 ? (
          <div className="py-10 text-center text-sm text-muted-foreground">
            <p>No models match &ldquo;{debouncedQuery}&rdquo;.</p>
            <Button
              type="button"
              size="sm"
              variant="link"
              className="mt-2"
              onClick={() => setQuery('')}
            >
              Clear search
            </Button>
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map((model) => (
              <ModelRow
                key={model.id}
                model={model}
                job={findJob(model, downloads)}
                resolving={resolvingId === model.id}
                actionError={actionErrors[model.id] || null}
                onDownload={() => void handleDownload(model)}
                onCancel={() => {
                  const job = findJob(model, downloads);
                  if (job) void handleCancel(job.jobId);
                }}
              />
            ))}
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border/60 bg-muted/30 px-4 py-3 text-2xs text-muted-foreground">
          <span>
            Adding a running server, installing the llama.cpp runtime, or
            serving a GGUF you already have? Those live in Local Models.
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
    </div>
  );
}
