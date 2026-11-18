import { useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { deleteBot, ensureBotChat, updateBotUiMeta } from '@/api/api-client/bots';
import { Button } from '@/components/ui/button';
import type { ChatMessage } from '@/types/chat';

/**
 * BotReceiptChip — single horizontal receipt row for a bot the orchestrator
 * created or retired on this turn.
 *
 * Edit opens a compact inline rename row (`updateBotUiMeta`) with an Open chat
 * link to the bot's canonical session; Undo retires the bot via `deleteBot`.
 */

type BotReceipt = {
  agentId: string;
  name: string;
  role?: string;
  permissions?: string[];
  droppedPermissions?: string[];
  canonicalChatId?: string;
};

function parseReceipt(summary: string | undefined, kind: string): BotReceipt | null {
  if (!summary) return null;
  try {
    const parsed = JSON.parse(summary) as Record<string, unknown>;
    if (parsed?.kind !== kind) return null;
    const agentId = typeof parsed.agentId === 'string' ? parsed.agentId : '';
    if (!agentId) return null;
    return {
      agentId,
      name: typeof parsed.name === 'string' ? parsed.name : 'bot',
      role: typeof parsed.role === 'string' ? parsed.role : undefined,
      permissions: Array.isArray(parsed.permissions) ? (parsed.permissions as string[]) : undefined,
      droppedPermissions: Array.isArray(parsed.droppedPermissions)
        ? (parsed.droppedPermissions as string[])
        : undefined,
      canonicalChatId:
        typeof parsed.canonicalChatId === 'string' ? parsed.canonicalChatId : undefined,
    };
  } catch {
    /* the receipt is JSON; a non-JSON summary is not a creation */
  }
  return null;
}

export function BotReceiptChip({ tools }: { tools?: ChatMessage['tools'] }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const created = useMemo(() => {
    for (const tool of tools ?? []) {
      const hit = parseReceipt(tool.summary, 'bot_created');
      if (hit) return hit;
    }
    return null;
  }, [tools]);

  const retired = useMemo(() => {
    for (const tool of tools ?? []) {
      const hit = parseReceipt(tool.summary, 'bot_retired');
      if (hit) return hit;
    }
    return null;
  }, [tools]);

  const [editing, setEditing] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');
  const [savedTitle, setSavedTitle] = useState<string | null>(null);

  const retire = useMutation({
    mutationFn: (agentId: string) => deleteBot(agentId),
    onSuccess: () => {
      toast.success('Bot retired');
      void qc.invalidateQueries({ queryKey: ['bots'] });
    },
    onError: (err) =>
      toast.error(`Retire failed: ${err instanceof Error ? err.message : String(err)}`),
  });

  const rename = useMutation({
    mutationFn: ({ agentId, title }: { agentId: string; title: string }) =>
      updateBotUiMeta(agentId, { title }),
    onSuccess: (_bot, vars) => {
      setSavedTitle(vars.title);
      setEditing(false);
      toast.success('Bot updated');
      void qc.invalidateQueries({ queryKey: ['bots'] });
    },
    onError: (err) =>
      toast.error(`Update failed: ${err instanceof Error ? err.message : String(err)}`),
  });

  if (!created && retired) {
    return (
      <div
        className="flex w-full min-w-0 items-center gap-2.5 rounded-[10px] border border-[var(--dt-line)] bg-[var(--dt-receipt-bg)] px-3 py-2 text-xs"
        data-slot="bot-receipt"
        data-testid="bot-retired-chip"
      >
        <span className="size-1.5 shrink-0 rounded-full bg-[var(--dt-fg-3)]" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-[var(--dt-fg-2)]">
          Bot &ldquo;{retired.name}&rdquo; retired
        </span>
      </div>
    );
  }
  if (!created) return null;

  const displayName = savedTitle || created.name;
  const shown = created.permissions ?? [];
  const dropped = created.droppedPermissions ?? [];
  const detailParts: string[] = [];
  if (created.role) detailParts.push(created.role);
  if (shown.length > 0) {
    detailParts.push(
      shown.length === 1 && shown[0] === 'all' ? 'all tools' : shown.join(', '),
    );
  }
  if (dropped.length > 0) detailParts.push(`not granted: ${dropped.join(', ')}`);

  const openBotChat = async () => {
    if (created.canonicalChatId) {
      void navigate(`/c/${created.canonicalChatId}`);
      return;
    }
    try {
      const chat = await ensureBotChat(created.agentId);
      void navigate(`/c/${chat.sessionId}`);
    } catch (err) {
      toast.error(`Could not open chat: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  return (
    <div
      className="flex w-full min-w-0 flex-col gap-1.5"
      data-slot="bot-receipt"
      data-testid="bot-receipt-chip"
    >
      <div className="flex w-full min-w-0 items-center gap-2.5 rounded-[10px] border border-[var(--dt-line)] bg-[var(--dt-receipt-bg)] px-3 py-2 text-xs">
        <span className="size-1.5 shrink-0 rounded-full bg-success" aria-hidden />
        <span
          className="min-w-0 flex-1 truncate text-[var(--dt-fg-1)]"
          title={detailParts.length > 0 ? `${displayName} · ${detailParts.join(' · ')}` : displayName}
        >
          <span>Bot &ldquo;{displayName}&rdquo; ready</span>
          {created.role ? (
            <span className="text-[var(--dt-fg-3)]"> · {created.role}</span>
          ) : null}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              if (!editing) setTitleDraft(displayName);
              setEditing((v) => !v);
            }}
            className="h-6 px-2 text-2xs text-[var(--dt-fg-2)] hover:text-[var(--dt-fg-1)]"
            data-testid="bot-receipt-edit"
          >
            Edit
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => retire.mutate(created.agentId)}
            disabled={retire.isPending}
            className="h-6 px-2 text-2xs text-[var(--dt-fg-2)] hover:text-[var(--dt-fg-1)]"
            title="Retire this bot"
            data-testid="bot-receipt-retire"
          >
            Undo
          </Button>
        </div>
      </div>
      {editing && (
        <div className="flex flex-wrap items-center gap-1.5 rounded-[10px] border border-[var(--dt-line)] bg-[var(--dt-receipt-bg)] px-3 py-1.5 text-xs">
          <input
            type="text"
            value={titleDraft}
            onChange={(e) => setTitleDraft(e.target.value)}
            aria-label="Bot display title"
            className="h-7 min-w-0 flex-1 rounded-md border border-[var(--dt-line)] bg-[var(--dt-card)] px-2 text-xs text-[var(--dt-fg-1)] outline-none focus:border-[var(--dt-line-strong)]"
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={!titleDraft.trim() || rename.isPending}
            onClick={() =>
              rename.mutate({ agentId: created.agentId, title: titleDraft.trim() })
            }
            className="h-7 px-2 text-2xs"
          >
            Save
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => void openBotChat()}
            className="h-7 px-2 text-2xs text-[var(--dt-fg-2)]"
          >
            Open chat
          </Button>
        </div>
      )}
    </div>
  );
}