/* ── About — app identity, update checks, and release notes ─────────── */
/* The download is automatic: once a newer release is detected, the
 * installer fetches itself in the background and this page narrates the
 * state machine — "Version X available — downloading… (n / m)" then
 * "Update ready — restart to apply" with exactly two choices (Restart
 * now / Not now). There is deliberately no manual download button: the
 * restart is the only click the user makes, and "Not now" is remembered
 * across restarts (the staged installer never runs by itself). */

import { useEffect, useState } from 'react';
import {
  CheckCircle,
  AlertTriangle,
  Database,
  DownloadCloud,
  ExternalLink,
  RefreshCw,
  Asterisk,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { SectionHeader } from '@/components/SectionHeader';
import { SettingsToggle } from '@/components/settings/SettingsToggle';
import { Markdown } from '@/sections/chat/ChatMarkdown';
import { UpdateProgressBar } from '@/components/ui/UpdateProgressBar';
import { useBackendStatus } from '@/hooks/useBackendStatus';
import { useAppUpdate } from '@/hooks/useAppUpdate';
import { isTauri } from '@/lib/tauri-detect';
import { openExternal } from '@/lib/tauri-shell';
import { usePreferencesStore } from '@/lib/preferences';
import {
  getUpdateNotNowVersion,
  setUpdateNotNowVersion,
  UPDATE_FAILURE_COPY,
} from '@/store/app-update-install';

const RELEASES_URL = 'https://github.com/KILLER3UG/augustHarness/releases';

export function UpdateSection() {
  const {
    available,
    checking,
    error,
    installing,
    progress,
    formatBytes,
    install,
    check,
    failure,
    cancelled,
    cancelDownload,
  } =
    useAppUpdate();
  const { status: backend, sync, isTauri: backendTauri } = useBackendStatus();
  const [currentVersion, setCurrentVersion] = useState<string>('…');
  const autoDownloadUpdates = usePreferencesStore((s) => s.autoDownloadUpdates);
  const setAutoDownloadUpdates = usePreferencesStore((s) => s.setAutoDownloadUpdates);

  useEffect(() => {
    if (!isTauri) {
      setCurrentVersion('web');
      return;
    }
    void (async () => {
      try {
        const { getVersion } = await import('@tauri-apps/api/app');
        setCurrentVersion(await getVersion());
      } catch {
        setCurrentVersion('unknown');
      }
    })();
  }, []);

  const ready = progress.phase === 'ready';
  const downloading = progress.phase === 'downloading';
  const targetVersion = available?.version ?? null;
  // The durable "Not now" for this version. It must survive remounts AND
  // app restarts (the staged installer stays inert until an explicit
  // restart click), so it is read from localStorage, never local state
  // alone. A NEW release re-prompts because the choice is per version.
  const [notNow, setNotNow] = useState(
    () => targetVersion != null && getUpdateNotNowVersion() === targetVersion,
  );
  useEffect(() => {
    setNotNow(targetVersion != null && getUpdateNotNowVersion() === targetVersion);
  }, [targetVersion]);

  const onNotNow = () => {
    if (targetVersion) setUpdateNotNowVersion(targetVersion);
    setNotNow(true);
  };

  return (
    <div className="px-8 py-6 space-y-6">
      {/* The app mark, BORDERLESS: icons are borderless unless they are a
          search or action pill. It is not in the title either — the shared
          header is a title and a subtitle, and the nav item in front of it
          carries the section's own icon. */}
      <Asterisk className="size-6 text-primary" />
      <SectionHeader
        title="August"
        subtitle={`Version ${currentVersion}`}
        className="mb-0"
      />

      {/* Updates card */}
      <div className="rounded-2xl border border-border/50 bg-card/60 p-5 shadow-xs">
        <div className="flex items-center gap-2 text-sm font-medium text-foreground">
          <RefreshCw className={checking ? 'size-4 animate-spin' : 'size-4'} />
          Updates
        </div>

        {!isTauri ? (
          <div className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
            <AlertTriangle className="size-4 shrink-0" />
            Check for updates is only available in the desktop app.
          </div>
        ) : (
          <>
            <div className="mt-3 min-h-[1.25rem] text-sm">
              {failure ? (
                <span className="flex items-start gap-2 text-danger-fg">
                  <AlertTriangle className="mt-0.5 size-3 shrink-0" />
                  {UPDATE_FAILURE_COPY[failure.kind]}{' '}
                  <span className="text-muted-foreground">{failure.message}</span>
                </span>
              ) : error ? (
                <span className="flex items-center gap-2 text-danger-fg">
                  <AlertTriangle className="size-3 shrink-0" />
                  Update check failed: {error.message || 'could not reach the release feed'}
                </span>
              ) : cancelled ? (
                <span className="text-muted-foreground">Download cancelled.</span>
              ) : available ? (
                downloading ? (
                  <span className="flex items-center gap-2 font-medium text-primary">
                    <DownloadCloud className="size-3 shrink-0" />
                    Version {available.version} available — downloading…{' '}
                    {progress.totalBytes != null && progress.totalBytes > 0
                      ? `(${formatBytes(progress.downloadedBytes)} / ${formatBytes(progress.totalBytes)})`
                      : `(${formatBytes(progress.downloadedBytes)})`}
                  </span>
                ) : ready ? (
                  <span className="flex items-center gap-2 font-medium text-success-fg">
                    <CheckCircle className="size-3 shrink-0" />
                    Update ready — restart to apply
                  </span>
                ) : (
                  <span className="flex items-center gap-2 font-medium text-warning-fg">
                    <DownloadCloud className="size-3 shrink-0" />
                    Version {available.version} available — the download starts automatically.
                  </span>
                )
              ) : checking ? (
                <span className="text-muted-foreground">Checking for updates…</span>
              ) : (
                <span className="flex items-center gap-2 text-success-fg">
                  <CheckCircle className="size-3 shrink-0" />
                  You&apos;re on the latest version
                </span>
              )}
            </div>

            {available?.date && (
              <p className="mt-1 text-xs text-muted-foreground">
                Released {new Date(available.date).toLocaleDateString()}
              </p>
            )}
            {available?.body && (
              /* Markdown, not a <pre> (spec §6.2): release notes are
               * headings + bullets, and the shared renderer is the one owner
               * for that (it also sanitizes raw HTML/links). */
              <div className="mt-2 max-h-32 overflow-y-auto rounded-lg bg-muted/40 p-3 text-xs text-muted-foreground">
                <Markdown content={available.body} demoteHeadings />
              </div>
            )}

            {/* The update contract (AGENTS.md): the download is automatic;
                the restart is the user's click. There is no manual download
                button — once a version is detected it downloads itself, and
                the ready state offers exactly two choices: Restart now, or
                Not now (which is remembered per version, across restarts —
                the staged installer stays inert on disk until Restart now
                runs it through the verified install path). */}
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => { void check(); }}
                disabled={checking || installing}
                data-testid="about-check-now"
              >
                <RefreshCw className={checking ? 'size-3 mr-1.5 animate-spin' : 'size-3 mr-1.5'} />
                Check now
              </Button>
              {ready && !notNow && (
                <>
                  <Button
                    size="sm"
                    onClick={() => { void install(); }}
                    data-testid="about-update-restart"
                  >
                    <CheckCircle className="size-3 mr-1.5" />
                    Restart now
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={onNotNow}
                    data-testid="about-update-not-now"
                  >
                    Not now
                  </Button>
                </>
              )}
              {downloading && (
                <>
                  <span className="text-xs font-medium text-primary">
                    {progress.percent != null
                      ? `Downloading ${progress.percent}%`
                      : 'Downloading…'}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={cancelDownload}
                    data-testid="about-update-cancel"
                  >
                    Cancel
                  </Button>
                </>
              )}
              {ready && notNow && (
                <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  Not now is remembered — the downloaded update stays on disk, inert, until you
                  restart into it.
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => { void install(); }}
                    data-testid="about-update-restart"
                  >
                    Restart now
                  </Button>
                </span>
              )}
              {progress.phase === 'restarting' && (
                <span className="text-xs font-medium text-primary">Launching installer…</span>
              )}
              {failure && (
                <>
                  <Button size="sm" onClick={() => { void install(); }} data-testid="about-update-retry">
                    <RefreshCw className="mr-1.5 size-3" /> Retry
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => { void openExternal(RELEASES_URL); }}
                  >
                    Get the release manually
                  </Button>
                </>
              )}
              <button
                type="button"
                onClick={() => { void openExternal(RELEASES_URL); }}
                className="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground transition hover:text-foreground"
                data-testid="about-release-notes"
              >
                <ExternalLink className="size-3" /> Release notes
              </button>
            </div>
          </>
        )}
      </div>

      {/* Automatic updates note */}
      {isTauri && (
        <div className="mt-4">
          <p className="text-sm font-medium text-foreground">Automatic updates</p>
          <p className="mt-1 text-xs text-muted-foreground">
            August checks for updates automatically in the background and lets you know when one
            is ready.
          </p>
          <div className="mt-3">
            <SettingsToggle
              checked={autoDownloadUpdates}
              onCheckedChange={setAutoDownloadUpdates}
              label="Download updates automatically"
              description="Fetch a new build in the background as soon as one is published, so it is ready when you want it. Applying it is still your call — August never restarts on its own."
            />
          </div>
        </div>
      )}

      {/* Install progress */}
      {installing && (
        <div className="mt-4 rounded-xl border border-border/60 bg-card/60 p-5 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-foreground">
                {ready
                  ? `${available?.version ?? 'Update'} is ready`
                  : progress.phase === 'restarting'
                    ? 'Restarting to apply the update…'
                    : 'Downloading update…'}
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {ready
                  ? 'The update is downloaded. Restart when you’re ready to apply it.'
                  : progress.phase === 'restarting'
                    ? 'August closes while the silent in-place update runs and reopens when it finishes.'
                    : 'The download runs by itself; nothing installs until you restart.'}
              </p>
            </div>
            <span className="shrink-0 text-sm font-semibold tabular-nums text-foreground">
              {ready || progress.phase === 'restarting'
                ? '100%'
                : progress.percent != null
                  ? `${progress.percent}%`
                  : '…'}
            </span>
          </div>
          <UpdateProgressBar progress={progress} />
          <p className="text-2xs tabular-nums text-muted-foreground">
            {progress.totalBytes != null && progress.totalBytes > 0
              ? `${formatBytes(progress.downloadedBytes)} / ${formatBytes(progress.totalBytes)}`
              : progress.downloadedBytes > 0
                ? `${formatBytes(progress.downloadedBytes)} downloaded`
                : 'Starting download…'}
          </p>
        </div>
      )}

      <div className="mt-4">
        <BackendDepsCard backend={backend} onSync={() => { void sync(); }} isTauri={backendTauri} />
      </div>
    </div>
  );
}

function BackendDepsCard({
  backend,
  onSync,
  isTauri,
}: {
  backend: ReturnType<typeof useBackendStatus>['status'];
  onSync: () => void;
  isTauri: boolean;
}) {
  if (!isTauri) return null;

  const proxyLabel =
    backend.proxy === 'up'
      ? 'Backend: up'
      : backend.proxy === 'down'
        ? 'Backend: down'
        : 'Backend: unknown';
  const proxyCls =
    backend.proxy === 'up'
      ? 'text-success-fg'
      : backend.proxy === 'down'
        ? 'text-danger-fg'
        : 'text-muted-foreground';

  // `unknown` is the initial value of BackendSync and nothing polls it, so this
  // used to fall through to the "up to date" default: a never-bootstrapped or
  // half-synced runtime told the user its dependencies were current. The
  // reassurance now requires the state that actually earned it.
  let syncLabel = 'Dependencies: not checked yet';
  let syncCls = 'text-muted-foreground';
  if (backend.sync === 'up-to-date') {
    syncLabel = 'Dependencies: up to date';
    syncCls = 'text-success-fg';
  } else if (backend.sync === 'syncing') {
    syncLabel = 'Syncing backend dependencies…';
    syncCls = 'text-warning-fg';
  } else if (backend.sync === 'needs_setup') {
    syncLabel = 'Backend needs first-launch setup';
    syncCls = 'text-warning-fg';
  } else if (backend.sync === 'error') {
    syncLabel = `Sync failed: ${backend.syncError ?? 'unknown error'}`;
    syncCls = 'text-danger-fg';
  }

  return (
    <div className="rounded-xl border border-border/60 bg-card/60 p-5">
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm font-medium text-foreground flex items-center gap-2">
            <Database className="size-4 shrink-0" />
            Backend (Python)
          </p>
          <p className={`mt-1 text-xs ${proxyCls}`}>{proxyLabel}</p>
          <p className={`mt-0.5 text-xs ${syncCls}`}>{syncLabel}</p>
          {backend.lastError && (
            <p className="mt-1 text-2xs text-danger-fg break-words">
              Last error: {backend.lastError}
            </p>
          )}
        </div>
        <Button variant="outline" size="sm" onClick={onSync}>
          Sync now
        </Button>
      </div>
    </div>
  );
}
