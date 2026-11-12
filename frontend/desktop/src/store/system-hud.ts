/* ── system-hud — the titlebar's ambient workstation readout ───────────
 *
 * The Claude reference shows a muted mono strip in the titlebar ("FPS N/A |
 * GPU 0% | 61 °C | 2055 MHz | W | CPU 35% | LAT N/A"). August is a local-model
 * workstation, so the strip fits its identity — but only with data the backend
 * actually serves. What exists:
 *
 * - `/api/health`        → uptime + port (the 5s gateway poll)
 * - `/api/health/detailed` → backend status / degraded (the ~30s tick)
 * - `/api/local-models/runtime` → GPU name + total/free VRAM via nvidia-smi
 *
 * There is NO feed for CPU %, temperature, core clock or latency, so the HUD
 * renders those fields as a permanent, honest `N/A` rather than inventing a
 * fake feed. The sampling rides the gateway store's EXISTING interval (one
 * timer in the app, budgeted by check:render-ratchet) — this module defines
 * no clock of its own; `recordHealth`/`recordRuntime` are called from the
 * gateway poll in store/gateway.ts.
 */

import { create } from 'zustand';

export interface SystemHudGpu {
  /** GPU name from nvidia-smi, or null when there is no NVIDIA driver. */
  name: string | null;
  /** MiB. Null when nvidia-smi could not report it. */
  vramTotalMb: number | null;
  vramFreeMb: number | null;
}

export interface SystemHudSample {
  /** Backend liveness, from the plain poll. */
  open: boolean;
  uptimeSec: number | null;
  /** 'degraded' comes from the detailed probe — background services failed. */
  degraded: boolean;
  gpu: SystemHudGpu | null;
}

type SystemHudState = SystemHudSample;

const INITIAL: SystemHudState = {
  open: false,
  uptimeSec: null,
  degraded: false,
  gpu: null,
};

export const useSystemHudStore = create<SystemHudState>(() => INITIAL);

/** Nanostores-shaped shim for imperative callers (the gateway poll). */
export const $systemHud = {
  get: (): SystemHudState => useSystemHudStore.getState(),
};

/** Merge a sample from the plain /api/health poll (every 5s tick). */
export function recordHealth(open: boolean, uptimeSec: number | null): void {
  const prev = useSystemHudStore.getState();
  if (prev.open === open && prev.uptimeSec === uptimeSec) return;
  useSystemHudStore.setState({ open, uptimeSec });
}

/** Merge the detailed probe's degraded flag (every 6th tick, ~30s). A failed
 *  probe never calls this — the last good sample stays on screen rather than
 *  flapping to a false "ok". */
export function recordDetailed(degraded: boolean): void {
  if (useSystemHudStore.getState().degraded === degraded) return;
  useSystemHudStore.setState({ degraded });
}

/** Merge the runtime's GPU detection (same tick as the detailed probe). */
export function recordGpu(gpu: SystemHudGpu | null): void {
  const prev = useSystemHudStore.getState();
  if (gpuEqual(prev.gpu, gpu)) return;
  useSystemHudStore.setState({ gpu });
}

function gpuEqual(a: SystemHudGpu | null, b: SystemHudGpu | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return (
    a.name === b.name &&
    a.vramTotalMb === b.vramTotalMb &&
    a.vramFreeMb === b.vramFreeMb
  );
}

/** `8.1/24.0 GB` — one decimal, the way a workstation readout prints it. */
export function formatVram(freeMb: number | null, totalMb: number | null): string | null {
  if (!totalMb) return null;
  const total = (totalMb / 1024).toFixed(1);
  const free = freeMb ? (freeMb / 1024).toFixed(1) : null;
  return free ? `${free}/${total} GB` : `${total} GB`;
}
