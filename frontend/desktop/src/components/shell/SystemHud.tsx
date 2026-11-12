/* ── SystemHud — the titlebar's muted mono workstation strip ───────────
 *
 * The reference shows "FPS N/A | GPU 0% | 61 °C | 2055 MHz | W | CPU 35% |
 * LAT N/A" on the titlebar's right. August samples what its backend actually
 * serves (GPU name + VRAM from the llama runtime's nvidia-smi probe, backend
 * liveness from the gateway poll) and prints N/A for every field it has no
 * feed for — a number that can change later is better omitted, and an
 * invented one is worse than either.
 *
 * It renders NOTHING of its own timing: the store is fed by the gateway
 * poll's existing interval (store/gateway.ts → store/system-hud.ts), so this
 * component adds no listener and no timer (check:render-ratchet budgets).
 */

import { useSystemHudStore, formatVram } from '@/store/system-hud';

/** One readout cell: label and value stay inside one muted span. */
function Cell({ label, value }: { label: string; value: string }) {
  return (
    <span className="system-hud-cell" data-testid={`system-hud-${label.toLowerCase()}`}>
      {label} {value}
    </span>
  );
}

export function SystemHud() {
  const open = useSystemHudStore((s) => s.open);
  const degraded = useSystemHudStore((s) => s.degraded);
  const gpu = useSystemHudStore((s) => s.gpu);

  const gpuName = open ? (gpu?.name ?? 'N/A') : 'N/A';
  const vram = open && gpu ? (formatVram(gpu.vramFreeMb, gpu.vramTotalMb) ?? 'N/A') : 'N/A';

  return (
    <div
      className="system-hud"
      title="Local machine telemetry. Fields the backend has no feed for read N/A."
      aria-label="System telemetry"
      data-testid="system-hud"
    >
      {degraded && <span className="system-hud-degraded" aria-label="Backend degraded" />}
      <Cell label="GPU" value={gpuName} />
      <Cell label="VRAM" value={vram} />
      <Cell label="CPU" value="N/A" />
      <Cell label="TEMP" value="N/A" />
      <Cell label="LAT" value="N/A" />
    </div>
  );
}
