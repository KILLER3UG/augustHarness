import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Bot as BotIcon,
  CalendarClock,
  Pause,
  Pencil,
  Play,
  Plus,
  Square,
  Trash2,
  Undo2,
  X,
} from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import {
  deleteBot,
  ensureBotChat,
  listBots,
  updateBotUiMeta,
  type Bot,
} from '@/api/api-client/bots';
import {
  deleteAutomation,
  getAutomations,
  patchAutomation,
  runAutomation,
  type AutomationJob,
} from '@/api/api-client/automations';
import { resolveUiSessionId, stopChatStream } from '@/sections/chat/chat-stream-manager';
import { composerSeed } from '@/store/composer-seed';
import { Button } from '@/components/ui/button';
import type { ConfirmDialogOptions } from '@/hooks/useConfirmDialog';
import { cn } from '@/lib/utils';

/** The composer prompt that replaces the manual creation flow. */
const ASK_AGENT_PROMPT =
  'Create a bot for me. Ask me what you need — name, what it should do, how it should work, ' +
  'and which tools it needs — then create it with create_bot.';

function timeAgo(iso?: string | null): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return '';
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return 'now';
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.round(hrs / 24);
  return `${days}d`;
}

export interface BotsRailProps {
  /** Open a workbench session by id (the canonical Bot Chat). */
  onOpenSession: (sessionId: string) => void;
  activeSessionId?: string;
  /** Navigate to the chat surface so a seeded prompt is visible and sendable. */
  onGoToChat?: () => void;
}

export function BotsRail({ onOpenSession, activeSessionId, onGoToChat }: BotsRailProps) {
  const [detail, setDetail] = useState<Bot | null>(null);

  const botsQuery = useQuery({ queryKey: ['bots'], queryFn: listBots });
  const bots = useMemo(
    () =>
      (botsQuery.data?.bots ?? []).slice().sort((a, b) => {
        const ah = a.uiMeta?.hidden ? 1 : 0;
        const bh = b.uiMeta?.hidden ? 1 : 0;
        return ah - bh;
      }),
    [botsQuery.data],
  );

  const chatsQuery = useQuery({
    queryKey: ['bots', 'chats'],
    queryFn: async () => {
      const out: Record<string, string> = {};
      await Promise.all(
        (botsQuery.data?.bots ?? []).map(async (bot) => {
          try {
            const chat = await ensureBotChat(bot.id);
            out[bot.id] = chat.sessionId;
          } catch {
            /* a bot with no chat yet still renders as a row */
          }
        }),
      );
      return out;
    },
    enabled: !!botsQuery.data?.bots.length,
    staleTime: 30_000,
  });
  const botSessionIds = chatsQuery.data ?? {};

  const summariesQ = useQuery({
    queryKey: ['bots', 'summaries'],
    queryFn: async () => {
      const { getWorkbenchSessions } = await import('@/api/workbench');
      const sessions = await getWorkbenchSessions();
      const out: Record<string, { lastPreview?: string; updatedAt?: string | null }> = {};
      for (const s of sessions) {
        if (s.canonicalBotChat && s.agentId) {
          out[s.agentId] = { lastPreview: s.lastPreview, updatedAt: s.updatedAt };
        }
      }
      return out;
    },
    enabled: (botsQuery.data?.bots?.length ?? 0) > 0,
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
  const summaries = summariesQ.data ?? {};

  const openChat = async (bot: Bot) => {
    try {
      const chat = await ensureBotChat(bot.id);
      onOpenSession(chat.sessionId);
    } catch {
      toast.error('Could not open this bot’s chat');
    }
  };

  /** The replacement for the create modal: seed the composer and go to chat. */
  const askAgent = () => {
    composerSeed.set(ASK_AGENT_PROMPT);
    onGoToChat?.();
  };

  const anyBots = (botsQuery.data?.bots?.length ?? 0) > 0;
  const rosterFailed = botsQuery.isError && !botsQuery.data;

  return (
    <div className="august-bots-rail">
      <div className="flex items-center justify-between px-2 mb-1">
        <div className="flex items-center gap-1">
          <h3 className="text-xs text-tier-2 font-medium">Bots</h3>
          {anyBots && <span className="text-xs text-tier-3 tabular-nums">{bots.length}</span>}
        </div>
        <Button
          variant="bare"
          type="button"
          onClick={askAgent}
          className="rounded p-0.5 text-tier-2 transition-colors hover:bg-white/[0.04] hover:text-tier-1"
          title="Ask the agent to create a bot"
          aria-label="Ask the agent to create a bot"
          data-testid="bots-ask-agent"
        >
          <Plus className="size-3" />
        </Button>
      </div>

      <div className="space-y-0.5">
        {bots.map((bot) => {
          const title = bot.uiMeta?.title || bot.name;
          const sessionId = botSessionIds[bot.id];
          const active = !!sessionId && sessionId === activeSessionId;
          const summary = summaries[bot.id];
          return (
            <div
              key={bot.id}
              className={cn(
                'august-bot-row group relative rounded-md',
                active ? 'bg-white/[0.06]' : 'hover:bg-white/[0.04]',
              )}
            >
              <Button
                variant="bare"
                type="button"
                onClick={() => setDetail(bot)}
                onDoubleClick={() => void openChat(bot)}
                className="flex w-full min-w-0 items-center gap-2 px-2 py-1 text-left"
                title={bot.description || title}
                data-testid={`bot-row-${bot.name}`}
              >
                <BotIcon className="size-3 shrink-0 text-tier-3" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline justify-between gap-1">
                    <span
                      className={cn(
                        'truncate text-xs',
                        bot.uiMeta?.hidden ? 'text-tier-3 italic' : 'text-tier-1',
                      )}
                    >
                      {title}
                    </span>
                    {summary?.updatedAt && (
                      <span className="shrink-0 text-2xs text-tier-3 tabular-nums">
                        {timeAgo(summary.updatedAt)}
                      </span>
                    )}
                  </span>
                  {summary?.lastPreview && (
                    <span className="block truncate text-2xs text-tier-3">
                      {summary.lastPreview}
                    </span>
                  )}
                </span>
              </Button>
            </div>
          );
        })}

        {rosterFailed ? (
          <p className="px-2 py-1 text-xs text-tier-3 italic">
            Couldn’t load the roster — your bots may still exist.
          </p>
        ) : !anyBots ? (
          <Button
            variant="bare"
            type="button"
            onClick={askAgent}
            className="w-full px-2 py-1 text-left text-xs text-tier-3 italic hover:text-tier-2"
            data-testid="bots-empty-ask"
          >
            No bots yet — ask the agent to make one.
          </Button>
        ) : null}
      </div>

      {detail && (
        <BotDetail
          bot={detail}
          sessionId={botSessionIds[detail.id]}
          onClose={() => setDetail(null)}
          onOpenChat={openChat}
        />
      )}
    </div>
  );
}

interface SidebarWorkbenchRun {
  id: string;
  title: string;
  status: string;
  updatedAt: string;
  createdAt: string;
  messageCount: number;
}

/** Recent/active workbench runs and scheduled automations with inline undo/actions. */
export function RunsAndAutomationsRail({
  onNavigate,
  confirmAction,
}: {
  onNavigate: (path: string) => void;
  confirmAction: (opts: ConfirmDialogOptions) => Promise<boolean>;
}) {
  const qc = useQueryClient();

  const runsQ = useQuery<SidebarWorkbenchRun[]>({
    queryKey: ['workbench-runs'],
    queryFn: () => api.get<SidebarWorkbenchRun[]>('/api/workbench/sessions'),
    refetchInterval: (q) =>
      (q.state.data ?? []).some((r) => r.status === 'streaming' || r.status === 'awaiting_approval')
        ? 4_000
        : 20_000,
    staleTime: 2_000,
  });

  const automationsQ = useQuery({
    queryKey: ['automations'],
    queryFn: getAutomations,
    refetchInterval: (q) =>
      (q.state.data?.jobs ?? []).some((j) => j.status === 'running') ? 3_000 : 30_000,
  });

  const patchAuto = useMutation({
    mutationFn: ({ id, paused }: { id: string; paused: boolean }) => patchAutomation(id, { paused }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['automations'] }),
    onError: (err) => toast.error(`Could not update: ${err instanceof Error ? err.message : String(err)}`),
  });

  const runAuto = useMutation({
    mutationFn: (id: string) => runAutomation(id, true),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['automations'] }),
    onError: (err) => toast.error(`Run failed: ${err instanceof Error ? err.message : String(err)}`),
  });

  const removeAuto = useMutation({
    mutationFn: (id: string) => deleteAutomation(id),
    onSuccess: () => {
      toast.success('Automation deleted');
      void qc.invalidateQueries({ queryKey: ['automations'] });
    },
    onError: (err) => toast.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`),
  });

  const recentRuns = useMemo(() => {
    const raw = (runsQ.data ?? []).filter(
      (r) => r.status === 'streaming' || r.status === 'awaiting_approval' || r.messageCount > 0,
    );
    return raw
      .slice()
      .sort((a, b) => {
        const aLive = a.status === 'streaming' || a.status === 'awaiting_approval' ? 1 : 0;
        const bLive = b.status === 'streaming' || b.status === 'awaiting_approval' ? 1 : 0;
        if (aLive !== bLive) return bLive - aLive;
        return Date.parse(b.updatedAt || b.createdAt || '') - Date.parse(a.updatedAt || a.createdAt || '');
      })
      .slice(0, 8);
  }, [runsQ.data]);

  const jobs = automationsQ.data?.jobs ?? [];

  async function requestUndoAutomation(job: AutomationJob) {
    const ok = await confirmAction({
      title: 'Delete this automation?',
      message: `"${job.name || 'Untitled'}" and its run history will be removed. This cannot be undone.`,
      confirmLabel: 'Delete',
      variant: 'destructive',
    });
    if (ok) removeAuto.mutate(job.id);
  }

  return (
    <div className="mt-4 space-y-4">
      {/* Recent & active runs */}
      <section data-testid="sidebar-runs-section">
        <div className="mb-1 flex items-center justify-between px-2">
          <div className="flex items-center gap-1">
            <h3 className="text-xs font-medium text-tier-2">Runs</h3>
            {recentRuns.length > 0 && (
              <span className="text-xs text-tier-3 tabular-nums">{recentRuns.length}</span>
            )}
          </div>
          <Button
            variant="bare"
            type="button"
            onClick={() => onNavigate('/runs')}
            className="rounded px-1.5 py-0.5 text-2xs text-tier-3 transition-colors hover:bg-white/[0.04] hover:text-tier-1"
            data-testid="sidebar-runs-open"
          >
            All
          </Button>
        </div>

        {runsQ.isError && !runsQ.data ? (
          <p className="px-2 py-1 text-xs text-tier-3 italic">Couldn’t load runs.</p>
        ) : recentRuns.length === 0 ? (
          <p className="px-2 py-1 text-xs text-tier-3 italic">No recent runs.</p>
        ) : (
          <ul className="space-y-0.5">
            {recentRuns.map((run) => {
              const isStreaming = run.status === 'streaming';
              const isAwaiting = run.status === 'awaiting_approval';
              return (
                <li
                  key={run.id}
                  className="group flex items-center gap-1.5 rounded-md px-2 py-1 transition-colors hover:bg-white/[0.04]"
                  data-testid={`sidebar-run-row-${run.id}`}
                >
                  <Button
                    variant="bare"
                    type="button"
                    onClick={() => onNavigate(`/c/${resolveUiSessionId(run.id)}`)}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                  >
                    <span
                      className={cn(
                        'size-1.5 shrink-0 rounded-full',
                        isStreaming
                          ? 'bg-primary animate-pulse'
                          : isAwaiting
                            ? 'bg-warning'
                            : 'bg-tier-3/50',
                      )}
                      aria-hidden="true"
                    />
                    <span className="min-w-0 flex-1 truncate text-xs text-tier-1">
                      {run.title || 'Untitled run'}
                    </span>
                  </Button>
                  {isStreaming ? (
                    <Button
                      variant="bare"
                      type="button"
                      onClick={() => {
                        void stopChatStream(resolveUiSessionId(run.id));
                        toast.success('Run stopped');
                      }}
                      className="rounded p-0.5 text-tier-3 hover:bg-white/[0.06] hover:text-tier-1"
                      title="Stop run"
                      aria-label="Stop run"
                    >
                      <Square className="size-2.5" />
                    </Button>
                  ) : (
                    <span className="shrink-0 text-2xs text-tier-3 tabular-nums">
                      {timeAgo(run.updatedAt || run.createdAt)}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* Scheduled automations with inline pause / run / undo */}
      <section data-testid="sidebar-automations-section">
        <div className="mb-1 flex items-center justify-between px-2">
          <div className="flex items-center gap-1">
            <CalendarClock className="size-3 text-tier-3" aria-hidden="true" />
            <h3 className="text-xs font-medium text-tier-2">Automations</h3>
            {jobs.length > 0 && (
              <span className="text-xs text-tier-3 tabular-nums">{jobs.length}</span>
            )}
          </div>
        </div>

        {jobs.length === 0 ? (
          <p className="px-2 py-1 text-xs text-tier-3 italic">
            No scheduled automations.
          </p>
        ) : (
          <ul className="space-y-0.5">
            {jobs.map((job) => (
              <li
                key={job.id}
                className="group flex items-center gap-1.5 rounded-md px-2 py-1 transition-colors hover:bg-white/[0.04]"
                data-testid={`sidebar-automation-row-${job.id}`}
              >
                <span
                  className={cn(
                    'size-1.5 shrink-0 rounded-full',
                    job.status === 'running'
                      ? 'bg-primary animate-pulse'
                      : job.status === 'error'
                        ? 'bg-danger'
                        : job.paused
                          ? 'bg-tier-3/40'
                          : 'bg-success',
                  )}
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs text-tier-1">{job.name || 'Automation'}</div>
                  <div className="truncate font-mono text-2xs text-tier-3">
                    {job.schedule || 'manual'}
                    {job.paused ? ' · paused' : ''}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                  <Button
                    variant="bare"
                    type="button"
                    onClick={() =>
                      job.paused
                        ? patchAuto.mutate({ id: job.id, paused: false })
                        : runAuto.mutate(job.id)
                    }
                    disabled={runAuto.isPending || patchAuto.isPending}
                    className="rounded p-0.5 text-tier-3 hover:bg-white/[0.06] hover:text-tier-1"
                    title={job.paused ? 'Resume' : 'Run now'}
                    aria-label={job.paused ? 'Resume automation' : 'Run automation now'}
                  >
                    {job.paused ? <Play className="size-2.5" /> : <Square className="size-2.5" />}
                  </Button>
                  {!job.paused && (
                    <Button
                      variant="bare"
                      type="button"
                      onClick={() => patchAuto.mutate({ id: job.id, paused: true })}
                      disabled={patchAuto.isPending}
                      className="rounded p-0.5 text-tier-3 hover:bg-white/[0.06] hover:text-tier-1"
                      title="Pause schedule"
                      aria-label="Pause automation"
                    >
                      <Pause className="size-2.5" />
                    </Button>
                  )}
                  <Button
                    variant="bare"
                    type="button"
                    onClick={() => void requestUndoAutomation(job)}
                    disabled={removeAuto.isPending}
                    className="rounded p-0.5 text-tier-3 hover:bg-white/[0.06] hover:text-danger-fg"
                    title="Undo / delete automation"
                    aria-label="Undo / delete automation"
                    data-testid={`sidebar-automation-undo-${job.id}`}
                  >
                    <Undo2 className="size-2.5" />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/**
 * BotDetail — the minimal edit surface: name, role, tools, retire.
 */
function BotDetail({
  bot,
  sessionId,
  onClose,
  onOpenChat,
}: {
  bot: Bot;
  sessionId?: string;
  onClose: () => void;
  onOpenChat: (bot: Bot) => void | Promise<void>;
}) {
  const qc = useQueryClient();
  const [title, setTitle] = useState(bot.uiMeta?.title || bot.name);

  const save = useMutation({
    mutationFn: () => updateBotUiMeta(bot.id, { title: title.trim() || bot.name }),
    onSuccess: () => {
      toast.success('Bot updated');
      void qc.invalidateQueries({ queryKey: ['bots'] });
      onClose();
    },
    onError: () => toast.error('Could not update the bot'),
  });

  const retire = useMutation({
    mutationFn: () => deleteBot(bot.id),
    onSuccess: () => {
      toast.success('Bot retired');
      void qc.invalidateQueries({ queryKey: ['bots'] });
      onClose();
    },
    onError: () => toast.error('Could not retire the bot'),
  });

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={bot.uiMeta?.title || bot.name}
      data-testid="bot-detail"
      onClick={onClose}
    >
      <div
        className="w-[min(94vw,420px)] rounded-2xl border border-border/70 bg-card p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold text-foreground">
              {bot.uiMeta?.title || bot.name}
            </h2>
            <p className="mt-0.5 truncate text-xs text-muted-foreground">@{bot.name}</p>
          </div>
          <Button
            variant="bare"
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-white/5 hover:text-foreground"
            aria-label="Close"
          >
            <X className="size-4" />
          </Button>
        </div>

        {bot.description && (
          <p className="mt-3 text-xs leading-relaxed text-muted-foreground/90">
            {bot.description}
          </p>
        )}

        <label className="mt-4 block text-xs text-muted-foreground" htmlFor="bot-detail-title">
          Title
        </label>
        <input
          id="bot-detail-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="mt-1 w-full rounded-md border border-border/60 bg-transparent px-2 py-1 text-xs text-foreground outline-none focus:border-primary/60"
          placeholder="What this bot is called"
          data-testid="bot-detail-title"
        />

        {bot.role && (
          <div className="mt-4">
            <p className="text-xs text-muted-foreground">Role</p>
            <p className="mt-1 text-xs text-foreground/80" data-testid="bot-detail-role">
              {bot.role}
            </p>
          </div>
        )}

        <div className="mt-5 flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            onClick={() => void onOpenChat(bot)}
            disabled={!sessionId}
            data-testid="bot-detail-open"
          >
            Open chat
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => save.mutate()}
            disabled={save.isPending}
            data-testid="bot-detail-save"
          >
            <Pencil className="size-3" aria-hidden /> Save
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="ml-auto text-destructive hover:text-destructive"
            onClick={() => retire.mutate()}
            disabled={retire.isPending}
            data-testid="bot-detail-retire"
          >
            <Trash2 className="size-3" aria-hidden /> Retire
          </Button>
        </div>
      </div>
    </div>
  );
}
