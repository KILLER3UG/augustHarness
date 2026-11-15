import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot as BotIcon, Pencil, Plus, Trash2, X } from 'lucide-react';
import { toast } from 'sonner';
import {
  deleteBot,
  ensureBotChat,
  listBots,
  updateBotUiMeta,
  type Bot,
} from '@/api/api-client/bots';
import { composerSeed } from '@/store/composer-seed';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

/**
 * BotsRail — the whole bot surface: a compact list, and a detail view.
 *
 * DESIGN (the "agent acts, user supervises" principle)
 * ----------------------------------------------------
 * Bots are a TOOLABLE concept. The orchestrator creates, configures and retires
 * them through `create_bot` / `update_bot` / `retire_bot`, and the turn shows a
 * receipt card (BotReceiptChip). So this surface is no longer where a user BUILDS
 * a bot — it is where they SEE what exists, OPEN one, and make a small correction
 * when the agent got a detail wrong.
 *
 * What was removed, and why:
 *   - the create modal (BotCreateModal, 536 lines, with its avatar picker,
 *     skills picker, memory-scope and workspace fields) — replaced by one
 *     "Ask the agent to create a bot" action that seeds the composer. Every field
 *     it collected is now something the agent asks about in conversation, which
 *     is the point of the redesign.
 *   - group rooms (the Rooms section, the rooms modal, the New Group Chat menu
 *     item) — multi-bot rooms were rejected by the owner; they are deleted, not
 *     hidden.
 *   - the activity mute toggle and the "Active now" strip — notification chrome
 *     the chat itself already covers.
 *   - the search box — a roster of a handful of bots does not need search; the
 *     rail is a list you can read.
 *   - the row menu (hide / copy handle / randomise avatar / duplicate / delete)
 *     and the profile card that hosted it — the detail view carries the actions
 *     that matter, and the rest was chrome for a generated face.
 *
 * What stayed:
 *   - the list, one row per bot, with the canonical chat's last-message preview
 *     and relative time (the two facts that help you choose a row).
 *   - a detail view: name, role, tools, and the actions.
 */

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
  const qc = useQueryClient();
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
          {anyBots && <span className="text-xs text-tier-2 tabular-nums">{bots.length}</span>}
        </div>
        {/* One action, and it is the agent-driven one. */}
        <button
          type="button"
          onClick={askAgent}
          className="rounded p-0.5 text-tier-2 transition-colors hover:bg-white/[0.03] hover:text-tier-1"
          title="Ask the agent to create a bot"
          aria-label="Ask the agent to create a bot"
          data-testid="bots-ask-agent"
        >
          <Plus className="size-3" />
        </button>
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
                active ? 'bg-white/[0.05]' : 'hover:bg-white/[0.03]',
              )}
            >
              <button
                type="button"
                onClick={() => setDetail(bot)}
                onDoubleClick={() => void openChat(bot)}
                className="flex w-full min-w-0 items-center gap-2 px-2 py-1 text-left"
                title={bot.description || title}
                data-testid={`bot-row-${bot.name}`}
              >
                <BotIcon className="size-3 shrink-0 text-muted-foreground/70" aria-hidden />
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
                      <span className="shrink-0 text-xs text-tier-2 tabular-nums">
                        {timeAgo(summary.updatedAt)}
                      </span>
                    )}
                  </span>
                  {summary?.lastPreview && (
                    <span className="block truncate text-xs text-tier-3">
                      {summary.lastPreview}
                    </span>
                  )}
                </span>
              </button>
            </div>
          );
        })}

        {rosterFailed ? (
          <p className="px-2 py-1 text-xs text-tier-3 italic">
            Couldn’t load the roster — your bots may still exist.
          </p>
        ) : !anyBots ? (
          <button
            type="button"
            onClick={askAgent}
            className="w-full px-2 py-1 text-left text-xs text-tier-3 italic hover:text-tier-2"
            data-testid="bots-empty-ask"
          >
            No bots yet — ask the agent to make one.
          </button>
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

/**
 * BotDetail — the minimal edit surface: name, role, tools, retire.
 *
 * Deliberately not a form for every registry field. The two things a user
 * actually corrects are the title (what it is called) and whether it should exist
 * at all. Tools are read-only here: a bot can never be granted more than the
 * agent that made it (the ceiling lives in bot_tools.py), so an editable list
 * would imply a freedom that does not exist.
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
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-white/5 hover:text-foreground"
            aria-label="Close"
          >
            <X className="size-4" />
          </button>
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