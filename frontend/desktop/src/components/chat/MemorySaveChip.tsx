import { useId, useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, Undo2 } from 'lucide-react';
import { toast } from 'sonner';
import { undoAugustRollback } from '@/api/api-client/manage';
import { Button } from '@/components/ui/button';
import type { ChatMessage } from '@/types/chat';

/**
 * MemorySaveChip — at most ONE receipt for a memory save on this turn,
 * with the rollback entry the write door recorded as its Undo.
 *
 * Reference shape (the Claude pass): a quiet bordered card — "Added to
 * memory ▾" — collapsed by default; the body names the fact under its own
 * `<key>.md` filename and carries the Undo. Silence when the turn saved
 * nothing, or when the build predates `rollbackId` (the receipt then has
 * nothing to undo by).
 */
export function MemorySaveChip({ tools }: { tools?: ChatMessage['tools'] }) {
  const qc = useQueryClient();
  const saved = useMemo(() => {
    let hit: { key: string; rollbackId: string } | null = null;
    for (const tool of tools ?? []) {
      if (tool.name !== 'remember' || !tool.summary) continue;
      try {
        const parsed = JSON.parse(tool.summary) as Record<string, unknown>;
        if (parsed?.ok === true && typeof parsed.rollbackId === 'string' && parsed.rollbackId) {
          hit = {
            key: typeof parsed.key === 'string' ? parsed.key : 'memory',
            rollbackId: parsed.rollbackId,
          };
        }
      } catch {
        /* the receipt is JSON; a non-JSON summary is not a successful save */
      }
    }
    return hit;
  }, [tools]);

  const undo = useMutation({
    mutationFn: (id: string) => undoAugustRollback(id),
    onSuccess: () => {
      toast.success('Memory save undone');
      void qc.invalidateQueries({ queryKey: ['brain-stores'] });
    },
    onError: (err) =>
      toast.error(`Undo failed: ${err instanceof Error ? err.message : String(err)}`),
  });

  const [open, setOpen] = useState(false);
  const uid = useId();
  const headId = `memory-save-head-${uid}`;
  const bodyId = `memory-save-body-${uid}`;

  if (!saved) return null;
  return (
    <div
      className="memory-card w-fit min-w-0 max-w-full"
      data-open={open}
      data-slot="memory-card"
      data-testid="memory-save-chip"
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
        <strong>Added to memory</strong>
        <ChevronDown className="memory-card-chevron size-3" aria-hidden />
      </Button>
      {/* The region stays mounted and `hidden` in the closed state — the
          same pattern ThinkingDisclosure uses, so the trigger advertises a
          real region in BOTH states rather than a body that only exists
          while open. */}
      <div
        className="memory-card-body"
        id={bodyId}
        role="region"
        aria-labelledby={headId}
        hidden={!open}
      >
        <div className="memory-card-entry">
          <span className="memory-card-key">{saved.key}.md</span>
        </div>
        <div className="mt-1.5">
          <Button
            type="button"
            variant="bare"
            onClick={() => undo.mutate(saved.rollbackId)}
            disabled={undo.isPending}
            className="inline-flex items-center gap-1 text-2xs text-primary hover:underline"
            data-testid="memory-save-undo"
          >
            <Undo2 className="size-3" aria-hidden /> Undo this write
          </Button>
        </div>
      </div>
    </div>
  );
}
