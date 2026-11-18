/* ── Session list ───────────────────────────────────────────────────── */
/* Top:   "August" + collapse · New thread · Threads | Bots & Runs     */
/* Middle: Flat Pinned (if any) + Recent OR Bots & Runs                */
/* Bottom: 44px Footer (avatar + profile name left, Settings gear right) */

import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { useQuery } from '@tanstack/react-query';
import { Settings } from "lucide-react";
import { AnimatePresence, LayoutGroup } from "framer-motion";
import { listBots } from '@/api/api-client';
import { openConversationSearch } from "@/store/conversation-search";
import { useNavigate } from "react-router-dom";
import { useAppUpdate } from "@/hooks/useAppUpdate";
import { useConfirmDialog } from "@/hooks/useConfirmDialog";
import { ConfirmDialog } from "@/components/overlays/ConfirmDialog";
import {
  useSessionsStore,
  renameSession,
  deleteSession,
  archiveSession,
  reconcileSessionsFromBackend,
  type Session,
  type SessionStatus,
} from "@/store/sessions";
import { useActiveChatStreamsStore, startChatActiveStreamsPoller } from "@/store/chat-active-streams";
import {
  useAccountStore,
  GUEST_ACCOUNT_VIEW,
  logoutAccount,
  setAccountStatus,
} from "@/store/account";
import { toast } from "sonner";
import { openExternal } from "@/lib/tauri-shell";
import { SessionListNav } from "./SessionListNav";
import { Button } from "@/components/ui/button";
import { SignalIcon } from "@/components/ui/SignalIcon";
import { SessionRow } from "./SessionRow";
import { BotsRail, RunsAndAutomationsRail } from "./BotsRail";
import { Section } from "./FolderTree";
import {
  UserDropdown,
  type UserDropdownAction,
  type UserStatus,
} from "@/components/ui/user-dropdown";
import { WhatsNewModal } from "@/components/overlays/WhatsNewModal";
import { NotificationsPanel } from "@/components/overlays/NotificationsPanel";
import { SwitchAccountModal } from "@/components/overlays/SwitchAccountModal";

const SESSIONS_KEY = "august-pinned-sessions";
const STORAGE = (() => {
  try {
    return JSON.parse(localStorage.getItem(SESSIONS_KEY) || "[]") as string[];
  } catch {
    return [];
  }
})();

const GROUP_RENDER_CAP = 60;
const capped = (list: Session[]): Session[] => list.slice(0, GROUP_RENDER_CAP);

function ShownOfTotal({ shown, total }: { shown: number; total: number }) {
  if (shown >= total) return null;
  return (
    <p
      className="px-2 pb-0.5 text-2xs tabular-nums text-sidebar-foreground/35"
      data-testid="group-shown-of-total"
    >
      {shown}/{total}
    </p>
  );
}

interface SessionRowHandlers {
  onClick: () => void;
  onTogglePin: () => void;
  onRename: (title: string) => void;
  onArchive: () => void;
  onDelete: () => void;
}

interface Props {
  activeId?: string;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onSelect: (s: Session) => void;
  onNew: () => void;
  onNavigate: (path: string) => void;
}

export function SessionList({
  activeId,
  onToggleCollapsed,
  onSelect,
  onNew,
  onNavigate,
}: Props) {
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(new Set(STORAGE));
  const [railTab, setRailTab] = useState<'threads' | 'bots-runs'>(() => {
    const saved = localStorage.getItem('august-sidebar-tab');
    return saved === 'bots' || saved === 'bots-runs' ? 'bots-runs' : 'threads';
  });
  const switchRailTab = (next: 'threads' | 'bots-runs') => {
    setRailTab(next);
    localStorage.setItem('august-sidebar-tab', next);
  };

  // Prefetch bots roster so switching to Bots & Runs is instant.
  useQuery({ queryKey: ['bots'], queryFn: listBots });

  const [sidebarWidth, setSidebarWidth] = useState(252);
  const [whatsNewOpen, setWhatsNewOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [switchAccountOpen, setSwitchAccountOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const [searchQuery, setSearchQuery] = useState("");

  const accounts = useAccountStore((s) => s.accounts);
  const activeAccountId = useAccountStore((s) => s.activeAccountId);
  const activeAccount =
    accounts.find((a) => a.id === activeAccountId) ?? null;
  const signedIn = activeAccount != null;
  const { available: updateAvailable } = useAppUpdate();
  const navigate = useNavigate();
  const dropdownUser = activeAccount
    ? {
        name: activeAccount.displayName,
        username: activeAccount.username,
        avatar: activeAccount.avatar,
        initials: activeAccount.initials,
        status: activeAccount.status,
      }
    : GUEST_ACCOUNT_VIEW;

  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const sync = () => setSidebarWidth(Math.round(el.getBoundingClientRect().width));
    sync();
    const ro = new ResizeObserver(sync);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const openSettingsSection = (section?: string) => {
    sessionStorage.setItem("pre-settings-path", window.location.pathname);
    onNavigate(section ? `/settings/${section}` : "/settings");
  };

  const handleUserAction = (action: UserDropdownAction) => {
    switch (action) {
      case "settings":
        openSettingsSection();
        break;
      case "appearance":
        openSettingsSection("appearance");
        break;
      case "notifications":
        setNotificationsOpen(true);
        break;
      case "update":
        openSettingsSection("app-updates");
        break;
      case "profile":
        openSettingsSection("account");
        break;
      case "create-account":
        setSwitchAccountOpen(true);
        break;
      case "whats-new":
        setWhatsNewOpen(true);
        break;
      case "help":
        void openExternal("https://github.com/KILLER3UG/augustHarness#readme");
        break;
      case "switch":
        if (accounts.length === 0) {
          openSettingsSection("account");
        } else {
          setSwitchAccountOpen(true);
        }
        break;
      case "logout":
        logoutAccount();
        toast.message("Signed out of local account");
        break;
      default:
        break;
    }
  };

  const sessions = useSessionsStore((s) => s.sessions);
  const sessionStates = useSessionsStore((s) => s.sessionStates);
  const activeChatSessions = useActiveChatStreamsStore((s) => s.active);

  useEffect(() => {
    startChatActiveStreamsPoller();
  }, []);

  const { state: confirmState, confirm: confirmStyled, handleConfirm, handleCancel } =
    useConfirmDialog();

  const latest = useRef({ onSelect, onNavigate, activeId, sessions, pinnedIds });
  latest.current = { onSelect, onNavigate, activeId, sessions, pinnedIds };

  const confirmDeleteSession = useCallback(async (s: Session) => {
    const ok = await confirmStyled({
      title: "Delete chat?",
      message:
        "This permanently deletes the chat, its history and anything still " +
        "unsent in its composer. This cannot be undone.",
      confirmLabel: "Delete",
      variant: "destructive",
    });
    if (!ok) return;
    const { pinnedIds, activeId, onSelect, onNavigate } = latest.current;
    if (pinnedIds.has(s.id)) {
      const next = new Set(pinnedIds);
      next.delete(s.id);
      setPinnedIds(next);
      localStorage.setItem(SESSIONS_KEY, JSON.stringify([...next]));
    }
    deleteSession(s.id);
    if (activeId === s.id || activeId === s.workbenchSessionId) {
      const fallback = useSessionsStore
        .getState()
        .sessions.find((x) => x.id !== s.id && !x.isArchived);
      if (fallback) onSelect(fallback);
      else onNavigate('/');
    }
  }, [confirmStyled]);

  const mergedSessionStates = useMemo(() => {
    const next: Record<string, SessionStatus> = { ...sessionStates };
    for (const [id, status] of Object.entries(activeChatSessions)) {
      next[id] = status;
    }
    return next;
  }, [sessionStates, activeChatSessions]);

  const statusFor = (s: { id: string; workbenchSessionId?: string; backgroundWork?: boolean }): SessionStatus | undefined =>
    mergedSessionStates[s.id] ??
    (s.workbenchSessionId ? mergedSessionStates[s.workbenchSessionId] : undefined) ??
    (s.backgroundWork ? 'working' : undefined);

  const visible = useMemo(() => sessions.filter((s) => !s.isArchived), [sessions]);

  const liveStatus = useCallback(
    (s: Session): SessionStatus | undefined =>
      mergedSessionStates[s.id] ??
      (s.workbenchSessionId ? mergedSessionStates[s.workbenchSessionId] : undefined),
    [mergedSessionStates],
  );
  const isLive = useCallback(
    (st: SessionStatus | undefined) => st === 'working' || st === 'streaming',
    [],
  );

  const [activeStamp, setActiveStamp] = useState<Record<string, number>>({});
  useEffect(() => {
    const now = Date.now();
    let next: Record<string, number> | null = null;
    for (const s of sessions) {
      if (!isLive(liveStatus(s))) continue;
      const fromBackend = Date.parse(s.updatedAt || '') || 0;
      const seen = activeStamp[s.id] ?? 0;
      if (seen && fromBackend <= seen) continue;
      (next ??= { ...activeStamp })[s.id] = Math.max(fromBackend, now);
    }
    if (next) setActiveStamp(next);
  }, [sessions, liveStatus, isLive, activeStamp]);

  const recencyOf = useCallback(
    (s: Session) =>
      Math.max(
        activeStamp[s.id] ?? 0,
        Date.parse(s.updatedAt || '') || 0,
        Date.parse(s.startedAt || '') || 0,
      ),
    [activeStamp],
  );

  const pinned = useMemo(
    () =>
      visible
        .filter((s) => pinnedIds.has(s.id))
        .sort((a, b) => recencyOf(b) - recencyOf(a)),
    [visible, pinnedIds, recencyOf],
  );

  const recent = useMemo(
    () =>
      visible
        .filter((s) => !pinnedIds.has(s.id))
        .sort((a, b) => recencyOf(b) - recencyOf(a)),
    [visible, pinnedIds, recencyOf],
  );

  const togglePin = useCallback((id: string) => {
    const next = new Set(latest.current.pinnedIds);
    void (next.has(id) ? next.delete(id) : next.add(id));
    setPinnedIds(next);
    localStorage.setItem(SESSIONS_KEY, JSON.stringify([...next]));
  }, []);

  const sessionRowHandlers = useMemo(() => {
    const map = new Map<string, SessionRowHandlers>();
    for (const s of visible) {
      map.set(s.id, {
        onClick: () => latest.current.onSelect(s),
        onTogglePin: () => togglePin(s.id),
        onRename: (newTitle: string) => renameSession(s.id, newTitle),
        onArchive: () => {
          archiveSession(s.id);
          const { activeId, onSelect, sessions } = latest.current;
          if (activeId === s.id) {
            const fallback = sessions.find(
              (x) => x.id !== s.id && !x.isArchived,
            );
            if (fallback) onSelect(fallback);
          }
        },
        onDelete: () => confirmDeleteSession(s),
      });
    }
    return map;
  }, [visible, togglePin, confirmDeleteSession]);

  const searchTrim = searchQuery.trim().toLowerCase();
  const searching = searchTrim.length > 0;
  const searchTokens = useMemo(
    () => searchTrim.split(/\s+/).filter(Boolean),
    [searchTrim],
  );
  const matchesSearch = useCallback(
    (s: Session) => {
      if (!searching) return true;
      const haystack = `${(s.title || "").toLowerCase()} ${s.id.toLowerCase()}`;
      return searchTokens.every((t) => haystack.includes(t));
    },
    [searching, searchTokens],
  );

  const visiblePinned = useMemo(
    () => (searching ? pinned.filter(matchesSearch) : pinned),
    [searching, pinned, matchesSearch],
  );
  const filteredRecent = useMemo(
    () => (searching ? recent.filter(matchesSearch) : recent),
    [searching, recent, matchesSearch],
  );
  const visibleRecent = useMemo(
    () => capped(filteredRecent),
    [filteredRecent],
  );
  const searchMatchCount = visiblePinned.length + filteredRecent.length;

  return (
    <div ref={rootRef} className="august-session-list flex h-full relative select-none">
      <div className="flex-1 flex flex-col min-w-0">
        <SessionListNav
          onNew={onNew}
          onToggleCollapsed={onToggleCollapsed}
        />

        {/* 2-tab switcher: Threads | Bots & Runs */}
        <div className="sd-rail-switch" role="tablist" aria-label="Sidebar view">
          <Button
            variant="bare"
            type="button"
            role="tab"
            aria-selected={railTab === 'threads'}
            onClick={() => switchRailTab('threads')}
            data-testid="sidebar-tab-threads"
            data-active={railTab === 'threads'}
          >
            Threads
          </Button>
          <Button
            variant="bare"
            type="button"
            role="tab"
            aria-selected={railTab === 'bots-runs'}
            onClick={() => switchRailTab('bots-runs')}
            data-testid="sidebar-tab-bots-runs"
            data-active={railTab === 'bots-runs'}
          >
            Bots &amp; Runs
          </Button>
        </div>

        {/* Inline search (Threads tab only) */}
        {railTab === 'threads' && (
          <div className="sd-rail-search-wrap">
            <div className="sd-rail-search">
              <SignalIcon name="search" />
              <input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setSearchQuery("");
                }}
                placeholder="Search threads…"
                aria-label="Search threads"
              />
              {searchQuery && (
                <Button
                  variant="bare"
                  type="button"
                  onClick={() => setSearchQuery("")}
                  title="Clear search"
                  aria-label="Clear search"
                >
                  <SignalIcon name="close" />
                </Button>
              )}
            </div>
          </div>
        )}

        {/* Threads tab: flat Pinned (if any) + Recent sorted by updatedAt */}
        {railTab === 'threads' && (
          <div className="sd-sidebar-list space-y-2">
            {searching && searchMatchCount === 0 && (
              <div className="py-4 text-center">
                <p className="text-xs text-tier-1">
                  No threads match “{searchQuery.trim()}”
                </p>
                <p className="mt-1 text-2xs text-tier-3" data-testid="search-empty-hint">
                  Search matches titles and thread ids · press Esc to clear
                </p>
              </div>
            )}

            {(visiblePinned.length > 0 || (searching && pinned.length > 0)) && (
              <Section
                title="Pinned"
                count={visiblePinned.length}
                empty={searching ? 'No pinned thread matches' : undefined}
              >
                <ShownOfTotal shown={visiblePinned.length} total={pinned.length} />
                <LayoutGroup id="pinned-sessions">
                  <AnimatePresence initial={false} mode="popLayout">
                    {visiblePinned.map((s) => (
                      <SessionRow
                        key={s.id}
                        session={s}
                        active={activeId === s.id}
                        pinned
                        status={statusFor(s)}
                        {...sessionRowHandlers.get(s.id)!}
                      />
                    ))}
                  </AnimatePresence>
                </LayoutGroup>
              </Section>
            )}

            <Section title="Recent" count={visibleRecent.length}>
              <ShownOfTotal shown={visibleRecent.length} total={filteredRecent.length} />
              <AnimatePresence initial={false} mode="popLayout">
                {visibleRecent.map((s) => (
                  <SessionRow
                    key={s.id}
                    session={s}
                    active={activeId === s.id}
                    pinned={false}
                    status={statusFor(s)}
                    {...sessionRowHandlers.get(s.id)!}
                  />
                ))}
              </AnimatePresence>
              {!searching && recent.length > GROUP_RENDER_CAP && (
                <div className="pt-2 pl-2 pb-1">
                  <Button
                    variant="bare"
                    type="button"
                    onClick={() => openConversationSearch()}
                    className="text-2xs text-tier-3 hover:text-tier-1 transition"
                  >
                    View all
                  </Button>
                </div>
              )}
            </Section>
          </div>
        )}

        {/* Bots & Runs tab */}
        {railTab === 'bots-runs' && (
          <div className="sd-sidebar-list">
            <BotsRail
              activeSessionId={
                sessions.find((s) => s.id === activeId || s.workbenchSessionId === activeId)
                  ?.workbenchSessionId
              }
              onOpenSession={(sid) => {
                const ui = sessions.find((s) => s.workbenchSessionId === sid);
                if (ui) {
                  onSelect(ui);
                } else {
                  void reconcileSessionsFromBackend();
                  onNavigate(`/c/${sid}`);
                }
              }}
              onGoToChat={() => navigate('/')}
            />
            <RunsAndAutomationsRail
              onNavigate={onNavigate}
              confirmAction={confirmStyled}
            />
          </div>
        )}

        {/* Footer 44px: avatar + profile name left, Settings gear right */}
        <div className="sidebar-footer">
          <UserDropdown
            selectedStatus={dropdownUser.status}
            onStatusChange={(status) => {
              if (signedIn) setAccountStatus(status as UserStatus);
            }}
            onAction={handleUserAction}
            signedIn={signedIn}
            accounts={accounts.map((a) => ({
              id: a.id,
              name: a.displayName,
              avatar: a.avatar,
              initials: a.initials,
            }))}
            align="start"
            side="top"
            alignOffset={-8}
            contentWidth={sidebarWidth}
            user={dropdownUser}
            updateAvailable={updateAvailable}
            trigger={
              <Button
                variant="bare"
                type="button"
                className="profile"
                title={dropdownUser.name}
                aria-label={`Account menu — ${dropdownUser.name}`}
                data-testid="user-menu-trigger"
              >
                <span className="avatar">
                  {dropdownUser.avatar ? (
                    <img src={dropdownUser.avatar} alt="" />
                  ) : (
                    dropdownUser.initials || dropdownUser.name.slice(0, 2).toUpperCase()
                  )}
                </span>
                <span className="min-w-0 flex-1 truncate text-xs font-medium text-tier-1">
                  {dropdownUser.name}
                </span>
              </Button>
            }
          />

          <Button
            variant="bare"
            type="button"
            onClick={() => openSettingsSection()}
            className="relative flex size-7 shrink-0 items-center justify-center rounded-md text-tier-3 transition-colors hover:bg-accent/60 hover:text-tier-1"
            title={
              updateAvailable
                ? `Settings · Update v${updateAvailable.version} available`
                : 'Settings'
            }
            aria-label="Settings"
            data-testid="sidebar-settings-button"
          >
            <Settings className="size-3.5" aria-hidden="true" />
            {updateAvailable ? (
              <span
                className="absolute right-1 top-1 size-1.5 rounded-full bg-primary"
                aria-hidden="true"
                data-testid="sidebar-update"
              />
            ) : null}
          </Button>
        </div>
      </div>

      <WhatsNewModal open={whatsNewOpen} onClose={() => setWhatsNewOpen(false)} />
      <NotificationsPanel
        open={notificationsOpen}
        onClose={() => setNotificationsOpen(false)}
      />
      <ConfirmDialog
        open={confirmState.open}
        title={confirmState.title}
        message={confirmState.message}
        confirmLabel={confirmState.confirmLabel}
        cancelLabel={confirmState.cancelLabel}
        variant={confirmState.variant}
        onConfirm={handleConfirm}
        onCancel={handleCancel}
      />
      <SwitchAccountModal
        open={switchAccountOpen}
        onClose={() => setSwitchAccountOpen(false)}
        onCreateNew={() => openSettingsSection("account")}
      />
    </div>
  );
}
