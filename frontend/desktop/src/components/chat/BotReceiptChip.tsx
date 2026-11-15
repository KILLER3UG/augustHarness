import { useId, useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Bot, ChevronDown, Undo2 } from 'lucide-react';
import { toast } from 'sonner';
import { deleteBot } from '@/api/api-client/bots';
import { Button } from '@/components/ui/button';
import type { ChatMessage } from '@/types/chat';

/**
 * BotReceiptChip — the receipt for a bot the ORCHESTRATOR created on this turn.
 *
 * The principle this serves: every capability gets a minimal UI surface (view,
 * edit, undo), but creating and operating it is agent-driven by default. The
 * agent creates a bot through `create_bot`; the human does not fill a form. The
 * card is the record of what was made — name, role, permissions — with an edit
 * door and an undo.
 *
 * Shape follows MemorySaveChip exactly: one quiet bordered card, collapsed by
 * default, body carries the detail plus the action. It reads the turn's own
 * tool summaries, so a turn that created no bot renders nothing.
 *
 * Undo is `retire_bot` — the mirror of creation. It removes the record and
 * cascades its private data; the chat history stays in the session list, which
 * is why this says "retire" rather than "delete".
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

  const retire = useMutation({
    mutationFn: (agentId: string) => deleteBot(agentId),
    onSuccess: () => {
      toast.success('Bot retired');
      void qc.invalidateQueries({ queryKey: ['bots'] });
    },
    onError: (err) =>
      toast.error(`Retire failed: ${err instanceof Error ? err.message : String(err)}`),
  });

  const [open, setOpen] = useState(false);
  const uid = useId();
  const headId = `bot-receipt-head-${uid}`;
  const bodyId = `bot-receipt-body-${uid}`;

  // A retire receipt stands on its own: nothing was created this turn, so the
  // card reports what went away rather than offering to undo it again.
  if (!created && retired) {
    return (
      <div
        className="memory-card w-fit min-w-0 max-w-full"
        data-slot="bot-receipt"
        data-testid="bot-retired-chip"
      >
        <div className="memory-card-head">
          <Bot className="size-3 shrink-0" aria-hidden />
          <strong>Retired {retired.name}</strong>
        </div>
      </div>
    );
  }
  if (!created) return null;

  const shown = created.permissions ?? [];
  const dropped = created.droppedPermissions ?? [];

  return (
    <div
      className="memory-card w-fit min-w-0 max-w-full"
      data-open={open}
      data-slot="bot-receipt"
      data-testid="bot-receipt-chip"
    >
      <Button
        variant="bare"
        type="button"
        id={headId}
        className="memory-card-head"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={bodyId}
      >
        <Bot className="memory-card-chevron size-3 shrink-0" aria-hidden />
        <strong>Bot ready</strong>
        <ChevronDown className="memory-card-chevron size-3" aria-hidden />
      </Button>
      <div
        className="memory-card-body"
        id={bodyId}
        role="region"
        aria-labelledby={headId}
        hidden={!open}
      >
        <div className="memory-card-entry">
          <span className="memory-card-key">{created.name}</span>
          {created.role ? <span className="text-muted-foreground"> · {created.role}</span> : null}
        </div>
        {shown.length > 0 && (
          <div className="memory-card-entry text-muted-foreground">
            {shown.length === 1 && shown[0] === 'all' ? 'all tools' : shown.join(', ')}
          </div>
        )}
        {/* Named, not swallowed: a tool the agent asked for but could not have
            is the ceiling doing its job, and hiding it would make the receipt
            a lie about what the bot can do. */}
        {dropped.length > 0 && (
          <div className="memory-card-entry text-muted-foreground">
            not granted: {dropped.join(', ')}
          </div>
        )}
        <div className="mt-1.5">
          <Button
            type="button"
            variant="bare"
            onClick={() => retire.mutate(created.agentId)}
            disabled={retire.isPending}
            className="inline-flex items-center gap-1 text-2xs text-primary hover:underline"
            data-testid="bot-receipt-retire"
          >
            <Undo2 className="size-3" aria-hidden /> Retire this bot
          </Button>
        </div>
      </div>
    </div>
  );
}