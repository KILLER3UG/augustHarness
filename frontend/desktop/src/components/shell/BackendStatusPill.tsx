/* ── BackendStatusPill — passive backend status affordance ──────────── */
/* The workspace renders IMMEDIATELY; the backend starts silently in the */
/* background. This pill is the one surface that reports that bootstrap: */
/* a quiet "Starting backend…" chip with the real phase while it runs,   */
/* nothing (a brief fade) once the supervisor reports ready, and on      */
/* failure a compact recovery card carrying the old bootstrap gate's     */
/* actions — Restart backend / Restart August — inside the pill's        */
/* expanded state instead of a full-screen wall.                         */
/*                                                                       */
/* Data source: useBackendSetup (`backend_setup_status` + the            */
/* `backend-setup` event) — unchanged. Readiness for SENDS is owned by   */
/* the gateway store's health poll + api/client discovery, not here.     */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { invoke } from '@tauri-apps/api/core';
import { AnimatePresence, motion } from 'framer-motion';
import { CircleX, Loader2, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useBackendSetup, type BackendSetupPhaseId } from '@/hooks/useBackendSetup';
import { isTauri } from '@/lib/tauri-detect';

/** Phases that mean the runtime is (re-)materializing. A settled backend
 *  that re-enters one of these (a manual deps sync, a post-update
 *  re-bootstrap) shows the pill again — the progress is real. */
const MATERIALIZING = new Set<BackendSetupPhaseId>(['copying', 'creating_venv', 'installing']);

/** The copy for each phase the pill can show. First-run install reads as
 *  real work ("Installing dependencies…"), not a stuck spinner. Keyed by
 *  string, not BackendSetupPhaseId, because the supervisor can also report
 *  the post-update 'updating' phase, which the TS union does not name. */
const PHASE_LABEL: Partial<Record<string, string>> = {
  copying: 'Preparing backend files…',
  creating_venv: 'Creating environment…',
  installing: 'Installing dependencies…',
  starting: 'Starting backend…',
  idle: 'Starting backend…',
  updating: 'Applying update…',
};

export function BackendStatusPill() {
  const { status: setup, refresh } = useBackendSetup();
  // Set once the supervisor has reported ready this mount. The pill then
  // stays out of the way until a materializing phase or an error returns.
  const [settled, setSettled] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [restartingApp, setRestartingApp] = useState(false);
  // Set once this mount has SEEN the backend booting (a real materializing
  // or starting phase from the supervisor). A warm start reports 'ready'
  // on the first poll and never sets it — surfaces loaded fine, so there is
  // nothing to heal.
  const sawBoot = useRef(false);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (setup.phase === 'ready') setSettled(true);
    else if (MATERIALIZING.has(setup.phase) || setup.phase === 'starting') sawBoot.current = true;
  }, [setup.phase]);

  // Surfaces that mounted while the backend was still booting may hold
  // failed or empty queries. The moment the supervisor reports ready after
  // such a boot, refetch them once — event-driven (the phase arrived via
  // the backend-setup event or the setup poll), never a new timer.
  useEffect(() => {
    if (setup.phase !== 'ready' || !sawBoot.current) return;
    sawBoot.current = false;
    void queryClient.invalidateQueries();
  }, [setup.phase, queryClient]);

  // The watchdog from the old bootstrap gate (be787900), kept whole: if the
  // supervisor has not become healthy shortly after launch, ask it to
  // restart ONCE. Deliberately no sync_backend_deps here — that raced with
  // the Rust startup thread and spawned a second visible console on Windows.
  useEffect(() => {
    if (!isTauri) return;
    let cancelled = false;
    void (async () => {
      await new Promise((r) => setTimeout(r, 8_000));
      if (cancelled) return;
      try {
        const status = await invoke<string>('proxy_status');
        if (status.startsWith('ok:')) return;
        await invoke<string>('restart_proxy');
      } catch {
        /* surfaces via the setup phase */
      }
      if (!cancelled) await refresh();
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  // The old gate's Retry setup action, verbatim: restart_proxy (the
  // recovery door that also clears the update holdoff) + a best-effort
  // deps materialize for packaged installs.
  const onRestartBackend = useCallback(async () => {
    setRetrying(true);
    try {
      await invoke<string>('restart_proxy');
      try {
        await invoke<string>('sync_backend_deps');
      } catch {
        /* restart_proxy already tried ensureRunning */
      }
      await refresh();
    } finally {
      setRetrying(false);
    }
  }, [refresh]);

  // "Restart August": the escalation the old full-screen card's copy asked
  // for ("If August still cannot reach it after a restart, restart August")
  // but never offered a button for. Relaunches the whole app so the
  // supervisor boots from scratch.
  const onRestartAugust = useCallback(async () => {
    setRestartingApp(true);
    try {
      const { relaunch } = await import('@tauri-apps/plugin-process');
      await relaunch();
    } catch {
      window.location.reload();
    }
  }, []);

  if (!isTauri) return null;

  const phase = setup.phase;
  const failed = phase === 'error';
  const busy = !settled && !failed && phase !== 'ready';
  const label = PHASE_LABEL[phase] ?? 'Starting backend…';
  const detail = setup.detail;

  return (
    // Anchored just under the 56px titlebar (--shell-titlebar-h): visible on
    // every surface, colliding with nothing persistent.
    <div className="pointer-events-none fixed left-1/2 top-[3.75rem] z-[90] -translate-x-1/2">
      <AnimatePresence>
        {busy && (
          <motion.div
            key="pill"
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0, transition: { duration: 0.2 } }}
            exit={{ opacity: 0, transition: { duration: 0.5 } }}
            role="status"
            aria-live="polite"
            data-testid="backend-status-pill"
            className="pointer-events-auto inline-flex items-center gap-2 rounded-full border border-border/60 bg-card/90 px-3 py-1.5 text-xs text-muted-foreground shadow-sm backdrop-blur"
          >
            <Loader2 className="size-3 shrink-0 animate-spin text-primary" aria-hidden />
            <span className="whitespace-nowrap">{label}</span>
            {detail && phase !== 'idle' ? (
              <span className="hidden max-w-40 truncate text-2xs text-muted-foreground/60 sm:inline">
                {detail}
              </span>
            ) : null}
          </motion.div>
        )}
        {failed && (
          <motion.div
            key="error"
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0, transition: { duration: 0.25 } }}
            exit={{ opacity: 0, transition: { duration: 0.3 } }}
            role="alert"
            data-testid="backend-status-error"
            className="pointer-events-auto w-[min(92vw,420px)] rounded-xl border border-danger/30 bg-card p-4 text-left shadow-lg"
          >
            <div className="flex items-start gap-2.5">
              <CircleX className="mt-0.5 size-4 shrink-0 text-danger-fg" aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-foreground">Backend setup failed</p>
                {detail ? (
                  <p className="mt-1 break-words whitespace-pre-wrap text-xs text-muted-foreground">
                    {detail}
                  </p>
                ) : null}
                {/* Hermes-parity reassurance, kept from the old recovery card:
                    a failed backend looks like a lost install, and the one
                    thing to know before retrying is that nothing the user owns
                    is at stake — onRestartBackend only restarts the process and
                    re-checks runtime files; {appData}/data is never re-staged. */}
                <p className="mt-1 text-2xs text-muted-foreground/70">
                  Nothing here is deleted — your memory, providers and settings stay untouched.
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <Button
                    size="sm"
                    disabled={retrying || restartingApp}
                    onClick={() => void onRestartBackend()}
                    data-testid="backend-restart-backend"
                  >
                    {retrying ? <RefreshCw className="mr-1.5 size-3 animate-spin" /> : null}
                    {retrying ? 'Retrying…' : 'Restart backend'}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={retrying || restartingApp}
                    onClick={() => void onRestartAugust()}
                    data-testid="backend-restart-august"
                  >
                    Restart August
                  </Button>
                </div>
                <p className="mt-1.5 text-2xs text-muted-foreground/60">
                  Restart backend re-runs the supervisor and re-checks its runtime files. If
                  August still cannot reach it after that, Restart August relaunches the app.
                </p>
                <details className="mt-2" data-testid="boot-error-log">
                  <summary className="cursor-pointer select-none text-2xs font-medium text-muted-foreground">
                    Common fixes
                  </summary>
                  <ul className="mt-1 list-inside list-disc space-y-0.5 text-2xs text-muted-foreground/80">
                    <li>Port 8085 already in use — close other apps using it</li>
                    <li>Python version mismatch — requires Python 3.12+</li>
                    <li>Antivirus blocking uvicorn — add an exception</li>
                    <li>First launch — dependency install can take 1-2 minutes</li>
                  </ul>
                </details>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
