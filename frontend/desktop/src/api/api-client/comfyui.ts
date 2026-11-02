/* ComfyUI connection + image generation. */

import { api } from '../client';

export interface ComfyConfig {
  enabled: boolean;
  baseUrl: string;
  workflowJson: string;
  defaultCheckpoint: string;
  toolAdvertised?: boolean;
  note?: string;
}

export interface ComfyHealth {
  ok: boolean;
  error?: string;
  comfyuiVersion?: string;
  pythonVersion?: string;
  devices?: { name: string; type: string; vramTotal: number }[];
}

export function getComfyConfig(): Promise<ComfyConfig> {
  return api.get('/api/comfyui/config');
}

export function updateComfyConfig(patch: Partial<ComfyConfig>): Promise<ComfyConfig> {
  return api.put('/api/comfyui/config', patch);
}

export function comfyHealth(): Promise<ComfyHealth> {
  return api.get('/api/comfyui/health');
}

export function comfyCheckpoints(): Promise<{ checkpoints: string[] }> {
  return api.get('/api/comfyui/checkpoints');
}

export function comfyGenerate(body: {
  prompt: string;
  negativePrompt?: string;
  checkpoint?: string | null;
  width?: number;
  height?: number;
  steps?: number;
  cfg?: number;
  seed?: number | null;
  path?: string | null;
}): Promise<{ path: string; filename: string; seed: number }> {
  return api.post('/api/comfyui/generate', body);
}
