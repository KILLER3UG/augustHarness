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
