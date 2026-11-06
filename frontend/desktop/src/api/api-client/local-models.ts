/* Local model servers — loopback scan and add-as-provider. */

import { api } from '../client';

export type LocalServerKind =
  | 'ollama'
  | 'lmstudio'
  | 'llamacpp'
  | 'jan'
  | 'gpt4all'
  | 'comfyui'
  | 'openai';

export interface LocalServer {
  kind: LocalServerKind;
  name: string;
  port: number;
  /** Chat base URL as it should be stored on a provider (never a leaf). */
  baseUrl: string;
  apiFormat: string;
  models: string[];
  detail?: string;
  /** True when a provider with this base URL already exists. */
  added?: boolean;
}

export interface LocalScanResponse {
  servers: LocalServer[];
}

export interface AddLocalServerResponse {
  provider: Record<string, unknown> & { id?: string; name?: string };
  discovery: { added: string[]; updated: string[]; removed: string[] };
}

export function scanLocalServers(ports?: number[]): Promise<LocalScanResponse> {
  return api.post<LocalScanResponse>('/api/local-models/scan', { ports: ports ?? [] });
}

export function addLocalServer(body: {
  baseUrl: string;
  name?: string;
  apiFormat?: string;
}): Promise<AddLocalServerResponse> {
  return api.post<AddLocalServerResponse>('/api/local-models/add', body);
}

/* ── llama.cpp runtime (download once, serve GGUFs) ─────────────────── */

export interface LlamaRuntimeStatus {
  installed: string | null;
  tag: string;
  install: {
    running: boolean;
    variant: string | null;
    asset: string | null;
    bytesDone: number;
    totalBytes: number;
    error: string | null;
    done: boolean;
  };
  suggestion: {
    variant: string;
    auto: boolean;
    reason: string;
    gpu: {
      nvidia: boolean;
      cudaVersion: string | null;
      gpuName: string | null;
      /** MiB. Null when nvidia-smi could not report it — never 0, because a
       *  zero would read as "no VRAM" to the offload badge. */
      vramTotalMb?: number | null;
      vramFreeMb?: number | null;
    };
  };
  variants: { name: string; label: string; requires: string; totalBytes: number }[];
}

export interface LlamaServerRow {
  id: string;
  ggufPath: string;
  name: string;
  port: number;
  pid: number;
  ctxSize: number;
  providerId: string | null;
  status: 'starting' | 'ready' | 'failed';
  detail?: string;
  startedAt?: string;
}

export function getLlamaRuntime(): Promise<LlamaRuntimeStatus> {
  return api.get<LlamaRuntimeStatus>('/api/local-models/runtime');
}

export function installLlamaRuntime(variant?: string): Promise<{ started: boolean }> {
  return api.post('/api/local-models/runtime/install', { variant: variant ?? null });
}

export function listLlamaServers(): Promise<{ servers: LlamaServerRow[] }> {
  return api.get('/api/local-models/servers');
}

export function serveGguf(path: string, ctxSize: number): Promise<LlamaServerRow> {
  return api.post('/api/local-models/gguf/serve', { path, ctxSize });
}

export function stopLlamaServer(serverId: string): Promise<{ stopped: string }> {
  return api.post('/api/local-models/servers/stop', { serverId });
}

/* ── GGUF files on disk ─────────────────────────────────────────────── */

export interface GgufEntry {
  path: string;
  name: string;
  size: number;
  split: boolean;
  shards?: number;
}

export function scanGgufFolder(path: string): Promise<{
  root: string;
  models: GgufEntry[];
  truncated: boolean;
}> {
  return api.post('/api/local-models/gguf/scan', { path });
}

/* ── Hugging Face ───────────────────────────────────────────────────── */

export interface HfModel {
  repoId: string;
  /** First path segment of the repo id — the Hub has no separate author field. */
  author: string;
  downloads: number;
  likes: number;
  /** ISO stamp; the search endpoint only carries it when `expand` asked. */
  lastModified: string;
  /** '' when the repo has no pipeline tag. */
  pipelineTag: string;
}

export interface HfFile {
  filename: string;
  size: number;
  /** Quant token parsed from the filename ('' when the name has none). */
  quantization: string;
  sharded: boolean;
  /** A single-file GGUF August can serve. */
  serveable: boolean;
}

export interface HfRepoDetail {
  repoId: string;
  lastModified: string;
  downloads: number;
  likes: number;
  gated: boolean;
  pipelineTag: string;
  license: string;
  files: HfFile[];
  /** The filename August defaults to (single-file Q4_K_M when present). */
  recommended: string;
  formats: string[];
  capabilities: string[];
}

export interface HfDownload {
  jobId: string;
  repoId: string;
  filename: string;
  destPath: string;
  size: number;
  bytesDone: number;
  status: 'downloading' | 'done' | 'failed' | 'cancelled' | 'interrupted';
  speedBps: number | null;
  error: string | null;
  startedAt: string | null;
}

export type HfSort = 'recommended' | 'downloads' | 'likes' | 'recent';

export function hfSearch(
  q: string,
  limit = 20,
  filter = '',
  sort: HfSort = 'recommended',
): Promise<{ query: string; sort: string; count: number; models: HfModel[] }> {
  return api.get(
    `/api/hf/search?q=${encodeURIComponent(q)}&limit=${limit}` +
      `&filter=${encodeURIComponent(filter)}&sort=${sort}`,
  );
}

export function hfRepoFiles(repoId: string): Promise<HfRepoDetail> {
  return api.get(`/api/hf/repos/${repoId}`);
}

export function hfRepoReadme(repoId: string): Promise<{
  repoId: string;
  markdown: string;
  truncated: boolean;
}> {
  return api.get(`/api/hf/repos/${encodeURIComponent(repoId)}/readme`);
}

export function hfListDownloads(): Promise<{ downloads: HfDownload[] }> {
  return api.get('/api/hf/downloads');
}

export function hfStartDownload(repoId: string, filename: string): Promise<HfDownload> {
  return api.post('/api/hf/downloads', { repoId, filename });
}

export function hfCancelDownload(jobId: string): Promise<{ cancelled: string; found: boolean }> {
  return api.post('/api/hf/downloads/cancel', { jobId });
}
