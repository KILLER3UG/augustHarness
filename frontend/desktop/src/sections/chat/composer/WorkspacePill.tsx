/* ── WorkspacePill — workspace folder pill + Projects switcher ──────────── */
/* Displays only the bound folder basename (or "No workspace") in the composer
 * bottom bar. Hosts the Projects switcher plus "Open workspace folder" and
 * "Copy workspace path" (moved here when the titlebar dropdown became an
 * inline title editor). */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  Check,
  ChevronDown,
  Copy,
  ExternalLink,
  Folder,
  FolderMinus,
  FolderPlus,
  Search,
  X,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { api } from '@/api/client';
import { folderNameFromPath, openFolderViaTauri } from '@/api/folder';
import { revealInFolder } from '@/lib/tauri-shell';
import { cn, workspaceBaseName } from '@/lib/utils';
import { useMenuFit } from './useComposerPopovers';
import { useWorkspacesStore } from '@/store/workspaces';

export interface WorkspacePillProps {
  /** UI session id (sess_* / session_*) the folder gets bound to. */
  sessionId: string | null;
  /** Backend workbench id — the git endpoints resolve the workspace through it. */
  workbenchSessionId?: string | null;
  /** Folder this chat is currently bound to (null for a folderless task chat). */
  workspacePath?: string | null;
  /** Controlled open state from the composer popover coordinator. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
}

export function WorkspacePill({
  sessionId,
  workbenchSessionId,
  workspacePath,
  open,
  onOpenChange,
  className,
}: WorkspacePillProps) {
  const [localOpen, setLocalOpen] = useState(false);
  const isControlled = open !== undefined;
  const isOpen = isControlled ? open : localOpen;
  const setIsOpen = (next: boolean) => {
    if (isControlled) onOpenChange?.(next);
    else setLocalOpen(next);
  };

  const [picking, setPicking] = useState(false);
  const [query, setQuery] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const fit = useMenuFit(isOpen, rootRef, menuRef);
  const queryClient = useQueryClient();

  const workspaces = useWorkspacesStore((s) => s.workspaces);
  const current = (workspacePath || '').trim();
  const label = current
    ? workspaceBaseName(current) || folderNameFromPath(current)
    : 'No workspace';

  /** Recents first (most recently used), then the bound path if missing from recents. */
  const options = useMemo(() => {
    const sorted = [...workspaces].sort((a, b) =>
      (b.lastUsedAt || '').localeCompare(a.lastUsedAt || ''),
    );
    if (current && !sorted.some((w) => w.path === current)) {
      return [
        { id: '__current', name: folderNameFromPath(current), path: current, lastUsedAt: '' },
        ...sorted,
      ];
    }
    return sorted;
  }, [workspaces, current]);

  const filteredOptions = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter((w) => {
      const name = w.name || folderNameFromPath(w.path);
      return `${name} ${w.path}`.toLowerCase().includes(q);
    });
  }, [options, query]);

  // Outside-click fallback only when used uncontrolled outside ChatThreadComposer.
  useEffect(() => {
    if (isControlled || !localOpen) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current?.contains(e.target as Node)) return;
      setLocalOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [isControlled, localOpen]);

  useEffect(() => {
    if (!isOpen) setQuery('');
  }, [isOpen]);

  const bindFolder = async (path: string) => {
    const trimmed = path.trim();
    if (!trimmed || !sessionId) return;
    const normalized = trimmed.replace(/\\/g, '/');
    try {
      const [{ bindSessionToWorkspacePath }, { addWorkspace }] = await Promise.all([
        import('@/store/sessions'),
        import('@/store/workspaces'),
      ]);
      addWorkspace(normalized);
      const { session } = bindSessionToWorkspacePath(sessionId, normalized);
      const wbId = session.workbenchSessionId || workbenchSessionId || '';
      if (wbId) {
        try {
          await api.post('/api/workbench/sandbox-mode', {
            sessionId: wbId,
            workspacePath: session.workspacePath || normalized,
          });
        } catch {
          /* best-effort — the next send re-syncs the workspace */
        }
      }
      void queryClient.invalidateQueries({ queryKey: ['git'] });
      setIsOpen(false);
      toast.success(`Working in ${folderNameFromPath(normalized)}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not open that folder');
    }
  };

  const clearWorkspace = async () => {
    if (!sessionId) return;
    try {
      const {
        useSessionsStore,
        updateSessionWorkspace,
        moveSessionToFolder,
      } = await import('@/store/sessions');
      const row = useSessionsStore
        .getState()
        .sessions.find((s) => s.id === sessionId || s.workbenchSessionId === sessionId);
      const id = row?.id ?? sessionId;
      const wbId = row?.workbenchSessionId || workbenchSessionId || '';
      if (wbId) {
        await api.post('/api/workbench/sandbox-mode', {
          sessionId: wbId,
          workspacePath: '',
        });
      }
      updateSessionWorkspace(id, null);
      moveSessionToFolder(id, null);
      void queryClient.invalidateQueries({ queryKey: ['git'] });
      setIsOpen(false);
      toast.success('No workspace — commands run without a project folder');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not clear the project folder');
    }
  };

  const handlePick = async () => {
    setPicking(true);
    try {
      const result = await openFolderViaTauri();
      if (result.cancelled || !result.path) return;
      setIsOpen(false);
      await bindFolder(result.path);
    } catch {
      toast.error('Folder picker is only available in the desktop app.');
    } finally {
      setPicking(false);
    }
  };

  return (
    <div
      ref={rootRef}
      className={cn('relative min-w-0', className)}
      onKeyDown={(e) => {
        if (!isControlled && e.key === 'Escape' && isOpen) {
          e.preventDefault();
          setIsOpen(false);
        }
      }}
    >
      <Button
        type="button"
        variant="bare"
        data-composer-trigger=""
        onClick={() => setIsOpen(!isOpen)}
        disabled={!sessionId}
        aria-expanded={isOpen}
        aria-haspopup="menu"
        title={current || 'No workspace — commands run without a project folder'}
        data-testid="composer-folder-chip"
        className={cn(
          'inline-flex h-7 max-w-[180px] items-center gap-1.5 rounded-full bg-[var(--dt-pill-bg,var(--dt-elevated))] px-2.5 text-xs font-medium text-[var(--dt-fg-3)] transition-colors hover:text-[var(--dt-fg-1)]',
          isOpen && 'text-[var(--dt-fg-1)]',
          !sessionId && 'pointer-events-none opacity-50',
        )}
      >
        {current ? (
          <Folder className="size-3.5 shrink-0" aria-hidden />
        ) : (
          <FolderMinus className="size-3.5 shrink-0" aria-hidden />
        )}
        <span className="min-w-0 flex-1 truncate">{label}</span>
        <ChevronDown
          className={cn(
            'size-3 shrink-0 opacity-60 transition-transform duration-150',
            isOpen && 'rotate-180',
          )}
          aria-hidden
        />
      </Button>

      {isOpen && (
        <div
          ref={menuRef}
          role="menu"
          data-composer-popover=""
          data-testid="composer-folder-menu"
          className={cn(
            'absolute left-0 z-50 flex w-[304px] max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded-xl border border-[var(--dt-line)] bg-popover shadow-2xl',
            fit?.side === 'bottom' ? 'top-full mt-2' : 'bottom-full mb-2',
          )}
        >
          <div className="flex h-10 shrink-0 items-center gap-2 border-b border-[var(--dt-line)] px-3">
            <Folder className="size-3.5 shrink-0 text-[var(--dt-fg-3)]" aria-hidden />
            <span className="min-w-0 flex-1 truncate text-xs font-semibold text-[var(--dt-fg-1)]">
              Projects
            </span>
          </div>

          <div className="shrink-0 px-2 pb-1 pt-2">
            <div className="flex items-center gap-1.5 rounded-md border border-[var(--dt-line)] bg-[var(--dt-background)] px-2 py-1.5 transition-colors focus-within:border-[var(--dt-line-strong)]">
              <Search className="size-3 shrink-0 text-[var(--dt-fg-3)]" aria-hidden />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Escape' && query) {
                    e.preventDefault();
                    setQuery('');
                  }
                }}
                placeholder="Search projects"
                aria-label="Search projects"
                data-testid="project-search"
                className="min-w-0 flex-1 bg-transparent text-xs text-[var(--dt-fg-1)] outline-none placeholder:text-[var(--dt-fg-3)]"
              />
              {query ? (
                <Button
                  type="button"
                  variant="bare"
                  onClick={() => setQuery('')}
                  aria-label="Clear project search"
                  className="shrink-0 text-[var(--dt-fg-3)] hover:text-[var(--dt-fg-1)]"
                >
                  <X className="size-3" />
                </Button>
              ) : null}
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto py-1 chat-scroll">
            <Button
              type="button"
              variant="bare"
              role="menuitemradio"
              aria-checked={!current}
              onClick={() => void clearWorkspace()}
              data-testid="project-no-workspace"
              className={cn(
                'flex w-full items-start gap-2 rounded-none px-3 py-2 text-left transition-colors hover:bg-[var(--dt-elevated)]',
                !current ? 'bg-[var(--dt-elevated)] text-[var(--dt-fg-1)]' : 'text-[var(--dt-fg-2)]',
              )}
            >
              <FolderMinus className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block text-xs font-medium leading-5">No workspace</span>
                <span className="block text-2xs leading-4 text-[var(--dt-fg-3)]">
                  Commands run without a project folder
                </span>
              </span>
              {!current ? <Check className="mt-0.5 size-3.5 shrink-0" aria-hidden /> : null}
            </Button>

            <Button
              type="button"
              variant="bare"
              role="menuitem"
              onClick={() => void handlePick()}
              data-testid="composer-open-folder"
              className="flex w-full items-center justify-start gap-2 rounded-none px-3 py-2 text-left text-xs text-[var(--dt-fg-2)] transition-colors hover:bg-[var(--dt-elevated)] hover:text-[var(--dt-fg-1)]"
            >
              <FolderPlus className="size-3.5 shrink-0" aria-hidden />
              <span className="min-w-0 flex-1 truncate">
                {picking ? 'Choosing folder…' : 'Open folder…'}
              </span>
            </Button>

            <div className="px-3 pb-1 pt-2 text-3xs font-medium text-[var(--dt-fg-3)]">
              Recent projects
            </div>

            {filteredOptions.length === 0 ? (
              <div className="px-3 py-2 text-2xs text-[var(--dt-fg-3)]">
                {options.length === 0
                  ? 'No projects yet — open a folder to start.'
                  : 'No project matches that search.'}
              </div>
            ) : (
              filteredOptions.map((w) => {
                const active = w.path === current;
                return (
                  <Button
                    key={w.id}
                    type="button"
                    variant="bare"
                    role="menuitemradio"
                    aria-checked={active}
                    disabled={active}
                    onClick={() => {
                      setIsOpen(false);
                      void bindFolder(w.path);
                    }}
                    data-testid="composer-folder-pick"
                    className={cn(
                      'flex w-full items-start justify-start gap-2 rounded-none px-3 py-2 text-left transition-colors hover:bg-[var(--dt-elevated)]',
                      active && 'bg-[var(--dt-elevated)]',
                    )}
                  >
                    <Folder className="mt-0.5 size-3.5 shrink-0 text-[var(--dt-fg-3)]" aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs text-[var(--dt-fg-1)]">
                        {w.name || workspaceBaseName(w.path)}
                      </span>
                      <span className="block truncate font-mono text-2xs text-[var(--dt-fg-3)]">
                        {w.path}
                      </span>
                    </span>
                    {active ? <Check className="mt-0.5 size-3.5 shrink-0" aria-hidden /> : null}
                  </Button>
                );
              })
            )}
          </div>

          <div className="shrink-0 border-t border-[var(--dt-line)] py-1">
            <Button
              type="button"
              variant="bare"
              role="menuitem"
              disabled={!current}
              onClick={() => {
                if (!current) return;
                setIsOpen(false);
                void revealInFolder(current);
              }}
              data-testid="workspace-open-in-folder"
              title={current ? `Reveal ${current} in file manager` : 'Select a workspace folder first'}
              className="flex w-full items-center gap-2 rounded-none px-3 py-1.5 text-left text-xs text-[var(--dt-fg-2)] transition-colors hover:bg-[var(--dt-elevated)] hover:text-[var(--dt-fg-1)]"
            >
              <ExternalLink className="size-3.5 shrink-0 text-[var(--dt-fg-3)]" aria-hidden />
              <span className="min-w-0 flex-1 truncate">Open workspace folder</span>
            </Button>
            <Button
              type="button"
              variant="bare"
              role="menuitem"
              disabled={!current}
              onClick={() => {
                if (!current) return;
                void navigator.clipboard.writeText(current);
                toast.success('Workspace path copied');
                setIsOpen(false);
              }}
              data-testid="workspace-copy-path"
              title={current ? `Copy ${current}` : 'Select a workspace folder first'}
              className="flex w-full items-center gap-2 rounded-none px-3 py-1.5 text-left text-xs text-[var(--dt-fg-2)] transition-colors hover:bg-[var(--dt-elevated)] hover:text-[var(--dt-fg-1)]"
            >
              <Copy className="size-3.5 shrink-0 text-[var(--dt-fg-3)]" aria-hidden />
              <span className="min-w-0 flex-1 truncate">Copy workspace path</span>
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
