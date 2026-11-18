import { useState, useEffect, useMemo, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { BookOpen, Layers } from "lucide-react";
import { isTauri } from "@/lib/tauri-detect";
import { renameSession, useSessionsStore } from "@/store/sessions";
import { routeLabelFor } from "@/lib/route-labels";
import { useBackgroundTasksStore } from "@/store/background-tasks";
import { RightDrawerDropdown } from "./RightDrawerLauncher";
import { BackgroundJobsButton } from "./BackgroundJobsButton";
import { MarqueeTitle } from "@/components/ui/MarqueeTitle";
import { Button } from "@/components/ui/button";
import { SignalIcon } from "@/components/ui/SignalIcon";
import { useSessionStream } from '@/sections/chat/hooks/useSessionStream';
import { collectArtifacts } from '@/lib/artifacts';
import { cn } from '@/lib/utils';
import type { Session } from "@/store/sessions";

interface ChatTitlebarProps {
  session: Session | null;
  sidebarCollapsed: boolean;
  rightDrawerOpen: boolean;
  onToggleSidebar: () => void;
  workersBadge?: number;
}

export function ChatTitlebar({
  session,
  sidebarCollapsed,
  rightDrawerOpen,
  onToggleSidebar,
  workersBadge = 0,
}: ChatTitlebarProps) {
  const [isMaximized, setIsMaximized] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const cancelledRef = useRef(false);

  const location = useLocation();
  const navigate = useNavigate();
  const stream = useSessionStream(session?.id ?? null);
  const artifactsCount = useMemo(
    () => collectArtifacts(stream?.messages ?? []).length,
    [stream?.messages],
  );
  const hasActiveJobs = useBackgroundTasksStore((s) =>
    s.tasks.some((t) => t.status === 'queued' || t.status === 'running'),
  );

  const sessionStatus = useSessionsStore((state) =>
    session ? state.sessionStates[session.id] : undefined,
  );
  const live =
    sessionStatus === 'streaming' ||
    sessionStatus === 'working' ||
    sessionStatus === 'awaiting';

  const isChatRoute =
    location.pathname === '/' || location.pathname.startsWith('/c/');
  const onArtifactsRoute = location.pathname === '/artifacts';
  const onDocsRoute = location.pathname === '/docs';

  const barTitle = useMemo(
    () => routeLabelFor(location.pathname, session?.title),
    [location.pathname, session?.title],
  );

  // Exit inline editing when switching sessions or leaving chat routes.
  useEffect(() => {
    setEditing(false);
  }, [session?.id, location.pathname]);

  const startEditing = () => {
    if (!session || !isChatRoute) return;
    cancelledRef.current = false;
    setDraft(session.title ?? '');
    setEditing(true);
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    });
  };

  const commitEdit = () => {
    if (cancelledRef.current) {
      cancelledRef.current = false;
      setEditing(false);
      return;
    }
    const trimmed = draft.trim();
    if (session && trimmed && trimmed !== session.title) {
      renameSession(session.id, trimmed);
    }
    setEditing(false);
  };

  const cancelEdit = () => {
    cancelledRef.current = true;
    setDraft(session?.title ?? '');
    setEditing(false);
  };

  // ── Window controls (Tauri only) ──
  useEffect(() => {
    if (!isTauri) return;
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void (async () => {
      try {
        const { getCurrentWindow } = await import("@tauri-apps/api/window");
        const win = getCurrentWindow();
        setIsMaximized(await win.isMaximized());
        unlisten = await win.onResized(async () => {
          if (disposed) return;
          try {
            setIsMaximized(await win.isMaximized());
          } catch { /* silent */ }
        });
      } catch { /* silent */ }
    })();
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, []);

  const handleMinimize = async () => {
    if (!isTauri) return;
    try {
      const { getCurrentWindow } = await import("@tauri-apps/api/window");
      await getCurrentWindow().minimize();
    } catch { /* silent */ }
  };

  const handleToggleMaximize = async () => {
    if (!isTauri) return;
    try {
      const { getCurrentWindow } = await import("@tauri-apps/api/window");
      const win = getCurrentWindow();
      await win.toggleMaximize();
      setIsMaximized(await win.isMaximized());
    } catch { /* silent */ }
  };

  const handleClose = async () => {
    if (!isTauri) return;
    try {
      const { getCurrentWindow } = await import("@tauri-apps/api/window");
      await getCurrentWindow().close();
    } catch { /* silent */ }
  };

  return (
    <header data-tauri-drag-region className="titlebar">
      <div className="titlebar-left" data-tauri-drag-region>
        {sidebarCollapsed && (
          <Button
            variant="bare"
            type="button"
            onClick={onToggleSidebar}
            className="icon-button"
            title="Show sidebar"
            aria-label="Show sidebar"
            data-testid="titlebar-expand-sidebar"
          >
            <SignalIcon name="panel" />
          </Button>
        )}

        {isChatRoute && session ? (
          editing ? (
            <input
              ref={inputRef}
              type="text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  commitEdit();
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  cancelEdit();
                }
              }}
              onBlur={commitEdit}
              aria-label="Thread title"
              data-testid="titlebar-title-input"
              className="titlebar-title-input h-7 w-full max-w-[420px] rounded-md border border-hairline-strong bg-elevated px-2 text-xs font-medium text-tier-1 outline-none"
            />
          ) : (
            <Button
              variant="bare"
              type="button"
              onClick={startEditing}
              className="titlebar-title-btn flex min-w-0 max-w-[420px] items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-xs font-medium text-tier-1 hover:bg-accent/50"
              title="Click to rename thread"
              aria-label={`Thread title: ${barTitle}. Click to rename`}
              data-testid="titlebar-session-trigger"
            >
              {live && (
                <span
                  className="size-1.5 shrink-0 rounded-full bg-primary animate-pulse"
                  aria-hidden="true"
                />
              )}
              <MarqueeTitle text={barTitle} data-testid="session-bar-title" className="min-w-0 flex-1" />
            </Button>
          )
        ) : (
          <div className="flex min-w-0 max-w-[420px] items-center gap-1.5 px-1.5">
            <h1 className="min-w-0 truncate text-xs font-medium text-tier-1" data-testid="session-bar-title">
              {barTitle}
            </h1>
          </div>
        )}
      </div>

      <div className="titlebar-actions">
        {hasActiveJobs && <BackgroundJobsButton />}

        <Button
          variant="bare"
          type="button"
          size="icon-sm"
          onClick={() => {
            void navigate(onArtifactsRoute ? (session ? `/c/${session.id}` : '/') : '/artifacts');
          }}
          className={cn(
            'relative size-7 rounded-md transition-colors',
            artifactsCount > 0 || onArtifactsRoute
              ? 'bg-elevated text-tier-1'
              : 'text-tier-3 hover:bg-accent/60 hover:text-tier-1',
          )}
          title={artifactsCount > 0 ? `Artifacts (${artifactsCount})` : 'Artifacts'}
          aria-label={artifactsCount > 0 ? `Artifacts (${artifactsCount} files)` : 'Artifacts'}
          aria-current={onArtifactsRoute ? 'page' : undefined}
          data-testid="titlebar-artifacts-button"
        >
          <Layers className="size-3.5" aria-hidden="true" />
          {artifactsCount > 0 && (
            <span
              className="absolute right-1 top-1 size-1.5 rounded-full bg-primary"
              aria-hidden="true"
              data-testid="titlebar-artifacts-dot"
            />
          )}
        </Button>

        <Button
          variant="bare"
          type="button"
          size="icon-sm"
          onClick={() => {
            void navigate(onDocsRoute ? (session ? `/c/${session.id}` : '/') : '/docs');
          }}
          className={cn(
            'size-7 rounded-md transition-colors',
            onDocsRoute
              ? 'bg-elevated text-tier-1'
              : 'text-tier-3 hover:bg-accent/60 hover:text-tier-1',
          )}
          title="Docs"
          aria-label="Docs"
          aria-current={onDocsRoute ? 'page' : undefined}
          data-testid="titlebar-docs-button"
        >
          <BookOpen className="size-3.5" aria-hidden="true" />
        </Button>

        <RightDrawerDropdown
          drawerOpen={rightDrawerOpen}
          workersBadge={workersBadge}
          artifactsCount={artifactsCount}
        />

        {isTauri && (
          <div className="window-controls">
            <Button variant="bare" onClick={() => { void handleMinimize(); }} aria-label="Minimize">
              <SignalIcon name="window-min" />
            </Button>
            <Button
              variant="bare"
              onClick={() => { void handleToggleMaximize(); }}
              aria-label={isMaximized ? 'Restore' : 'Maximize'}
            >
              <SignalIcon name="window-max" />
            </Button>
            <Button variant="bare" className="close" onClick={() => { void handleClose(); }} aria-label="Close">
              <SignalIcon name="close" />
            </Button>
          </div>
        )}
      </div>
    </header>
  );
}
