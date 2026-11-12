import { create } from 'zustand';
import { toast } from 'sonner';
import { api } from '@/api/client';
import { recordHealth, recordDetailed, recordGpu } from '@/store/system-hud';

export type GatewayState =
  | { status: 'connecting' }
  | { status: 'open'; port: number; uptime: number; degraded?: boolean }
  | { status: 'closed'; reason?: string }
  | { status: 'error'; message: string };

interface GatewayStoreState {
  gateway: GatewayState;
}

export const useGatewayStore = create<GatewayStoreState>(() => ({
  gateway: { status: 'connecting' },
}));

/** Nanostores-shaped shim for imperative get/set callers. */
export const $gateway = {
  get: (): GatewayState => useGatewayStore.getState().gateway,
  set: (gateway: GatewayState): void => {
    useGatewayStore.setState({ gateway });
  },
  subscribe: (listener: (gateway: GatewayState) => void): (() => void) => {
    listener(useGatewayStore.getState().gateway);
    return useGatewayStore.subscribe((s) => listener(s.gateway));
  },
};

export interface GatewayHealth {
  port?: number;
  uptime?: number;
}

async function poll() {
  try {
    const data = await api.get<GatewayHealth>('/api/health');
    useGatewayStore.setState({
      gateway: { status: 'open', port: data.port ?? 0, uptime: data.uptime ?? 0 },
    });
    // The titlebar's system HUD rides this same clock — no timer of its own.
    recordHealth(true, data.uptime ?? null);
  } catch (e) {
    const current = useGatewayStore.getState().gateway.status;
    // Stay on "connecting" during first-launch bootstrap so the setup
    // overlay keeps animating instead of flashing "Proxy is offline".
    if (current === 'connecting') {
      return;
    }
    useGatewayStore.setState({
      gateway: { status: 'closed', reason: e instanceof Error ? e.message : String(e) },
    });
    recordHealth(false, null);
  }
}

if (typeof window !== 'undefined') {
  void (async () => {
    // Give the backend supervisor time to bootstrap on first launch.
    for (let i = 0; i < 60; i++) {
      await poll();
      if (useGatewayStore.getState().gateway.status === 'open') break;
      await new Promise((r) => setTimeout(r, 1000));
    }
    if (useGatewayStore.getState().gateway.status === 'connecting') {
      useGatewayStore.setState({
        gateway: { status: 'closed', reason: 'Backend did not become ready in time' },
      });
    }
  })();
  // Degraded watch rides the SAME clock as the plain poll (one interval, not
  // two — recurring timers are budgeted by check:render-ratchet): every 6th
  // tick (~30s) the detailed endpoint answers whether the backend is UP AND
  // WHOLE. One sticky toast per degraded episode; recovery dismisses it.
  const DEGRADED_TOAST_ID = 'session-degraded';
  let degradedAnnounced = false;
  let pollTicks = 0;
  interface DetailedHealth {
    status?: string;
  }
  /** The slice of `/api/local-models/runtime` the system HUD reads — the
   *  nvidia-smi detection (name + VRAM), not the llama install state. */
  interface RuntimeHealth {
    suggestion?: {
      gpu?: {
        nvidia?: boolean;
        gpuName?: string | null;
        vramTotalMb?: number | null;
        vramFreeMb?: number | null;
      };
    };
  }
  /** One sample per ~30s tick: the detailed health probe and the GPU
   *  detection ride the SAME existing interval — the HUD owns no clock. */
  async function pollDetailed(): Promise<void> {
    const gw = useGatewayStore.getState().gateway;
    if (gw.status !== 'open') return;
    try {
      const data = await api.get<DetailedHealth>('/api/health/detailed');
      const degraded = data.status === 'degraded';
      useGatewayStore.setState({
        gateway: { ...gw, degraded },
      });
      recordDetailed(degraded);
      if (degraded && !degradedAnnounced) {
        degradedAnnounced = true;
        toast.error('Session degraded', {
          id: DEGRADED_TOAST_ID,
          duration: Infinity,
          description: 'Some background services did not start. Changes may not save until the app restarts.',
          action: { label: 'Reload', onClick: () => window.location.reload() },
        });
      } else if (!degraded && degradedAnnounced) {
        degradedAnnounced = false;
        toast.dismiss(DEGRADED_TOAST_ID);
      }
    } catch {
      // The plain poll above owns offline reporting; a detailed-probe
      // failure adds nothing the user needs to act on. The HUD keeps its
      // last good sample rather than flapping to a false "ok".
    }
    // GPU name + VRAM, same tick. A machine without nvidia-smi answers with
    // `nvidia: false` — the HUD prints N/A, which is honest, not an error.
    try {
      const runtime = await api.get<RuntimeHealth>('/api/local-models/runtime');
      const g = runtime.suggestion?.gpu;
      recordGpu(
        g?.nvidia
          ? {
              name: g.gpuName ?? null,
              vramTotalMb: g.vramTotalMb ?? null,
              vramFreeMb: g.vramFreeMb ?? null,
            }
          : null,
      );
    } catch {
      /* no runtime endpoint — the GPU fields stay N/A */
    }
  }
  setInterval(() => {
    void poll();
    pollTicks += 1;
    if (pollTicks % 6 === 0) void pollDetailed();
  }, 5_000);
}
