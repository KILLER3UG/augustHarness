/* SimulIDE circuit viewer — parse + SVG render of .sim/.sim1/.sim2 files. */

import { api } from './client';

export interface SimulideParseResult {
  name: string;
  version: string;
  svg: string;
  description: string;
  componentCount: number;
  wireCount: number;
  counts: Record<string, number>;
}

export function parseSimulide(path: string, sessionId?: string | null): Promise<SimulideParseResult> {
  return api.post('/api/simulide/parse', { path, sessionId: sessionId ?? null });
}

/** Viewer-facing extension test (mirrors the backend's SIMULIDE_EXTS). */
export function isSimulidePath(path: string): boolean {
  return /\.(sim|sim1|sim2)$/i.test(path.replace(/\\/g, '/').split('/').pop() ?? '');
}
