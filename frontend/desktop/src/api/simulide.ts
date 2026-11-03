/* SimulIDE circuit viewer + simulator — parse/render .sim/.sim1/.sim2 and
 * run August's built-in digital simulation. */

import { api } from './client';

export interface SimulideInputComponent {
  id: string;
  label: string;
}

export interface SimulideParseResult {
  name: string;
  version: string;
  svg: string;
  description: string;
  componentCount: number;
  wireCount: number;
  counts: Record<string, number>;
  /** Toggleable sources (Fixed Voltage items) — the sim's inputs. */
  inputComponents: SimulideInputComponent[];
  simulated: boolean;
  settled: boolean;
  /** The composed 7-segment digit ('0'-'9', '·', or null). */
  display: string | null;
  /** Per-component live state keyed by component id. */
  states: Record<string, Record<string, unknown>>;
  notes: string[];
}

export interface SimulideSimResult {
  settled: boolean;
  display: string | null;
  states: Record<string, Record<string, unknown>>;
  nets: Record<string, number | null>;
  notes: string[];
}

export function parseSimulide(
  path: string,
  sessionId?: string | null,
  inputs: Record<string, boolean> = {},
): Promise<SimulideParseResult> {
  return api.post('/api/simulide/parse', { path, sessionId: sessionId ?? null, inputs });
}

export function simulateSimulide(
  path: string,
  sessionId?: string | null,
  inputs: Record<string, boolean> = {},
): Promise<SimulideSimResult> {
  return api.post('/api/simulide/simulate', { path, sessionId: sessionId ?? null, inputs });
}

/** Viewer-facing extension test (mirrors the backend's SIMULIDE_EXTS). */
export function isSimulidePath(path: string): boolean {
  return /\.(sim|sim1|sim2)$/i.test(path.replace(/\\/g, '/').split('/').pop() ?? '');
}
