/* ── ReviewNotesPanel ─ line notes that go back to the agent ─────────── */
/* The write side of the review loop: annotate a changed line, edit or
 * delete the note, then send every unsent note to the agent as ONE queued
 * message (deterministic format from lib/diff-notes-format). Queued notes
 * dim — they are on their way, not being written — and editing a queued
 * note clears its sent flag so it can be sent again. */

import { useEffect, useRef, useState } from 'react';
import { Loader2, Pencil, Send, Trash2, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { queueWorkbenchMessage } from '@/api/workbench';
import { formatReviewNotes, sortNotesForSend } from '@/lib/diff-notes-format';
import { useReviewNotes, unsentNotes, type ReviewNote } from '@/store/review-notes';

export interface NoteDraft {
  filePath: string;
  lineNumber: number;
  startLine?: number;
  /** Set when the composer edits an existing note instead of adding one. */
  editId?: string;
}

export function ReviewNotesPanel({
  sessionId,
  notes,
  draft,
  onDraftConsumed,
  onDismissDraft,
  onEditNote,
  onJumpToNote,
}: {
  sessionId: string | null;
  notes: ReviewNote[];
  draft: NoteDraft | null;
  onDraftConsumed: () => void;
  onDismissDraft: () => void;
  /** Opens the composer against an existing note (same file/line). */
  onEditNote: (note: ReviewNote) => void;
  /** Scrolls the diff to the note's line; only wired for files in the diff. */
  onJumpToNote: (note: ReviewNote) => void;
}) {
  const { addNote, updateNote, removeNote, markQueued } = useReviewNotes();
  const [body, setBody] = useState('');
  const [sending, setSending] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const unsent = unsentNotes(notes);

  // A "+" click on a diff line opens the composer for that line; an edit
  // click loads the note. Focus the textarea either way so typing starts.
  // Deliberately not keyed on the `notes` prop: a parent re-render would
  // change its identity and wipe the body mid-typing. The edit target is
  // read from the store by id instead.
  useEffect(() => {
    if (!draft) return;
    if (draft.editId) {
      const note = useReviewNotes
        .getState()
        .bySession[sessionId ?? '']
        ?.find((n) => n.id === draft.editId);
      setBody(note?.body ?? '');
    } else {
      setBody('');
    }
    textareaRef.current?.focus();
  }, [draft, sessionId]);

  const saveDraft = () => {
    const trimmed = body.trim();
    // A note belongs to a session; without one there is no bucket to write
    // into (the old `?? ''` filed notes under a phantom key).
    if (!draft || !trimmed || !sessionId) return;
    if (draft.editId) {
      // Line edits apply; the composer never moves a note between files.
      updateNote(sessionId, draft.editId, {
        body: trimmed,
        lineNumber: draft.lineNumber,
        startLine: draft.startLine,
      });
      toast.success('Note updated — send it again to hand the new text to the agent');
    } else {
      addNote(sessionId, {
        filePath: draft.filePath,
        lineNumber: draft.lineNumber,
        startLine: draft.startLine,
        body: trimmed,
      });
    }
    setBody('');
    onDraftConsumed();
  };

  const sendUnsent = async () => {
    if (sending || !sessionId || unsent.length === 0) return;
    setSending(true);
    try {
      const message = formatReviewNotes(unsent);
      await queueWorkbenchMessage(sessionId, message);
      markQueued(sessionId, unsent.map((n) => n.id));
      toast.success(`${unsent.length} note${unsent.length === 1 ? '' : 's'} queued for the agent`);
      onDismissDraft();
    } catch (err) {
      toast.error(`Could not queue notes: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSending(false);
    }
  };

  const grouped = sortNotesForSend(notes);
  const draftTarget = draft
    ? `${draft.filePath}${draft.startLine !== undefined ? `:${draft.startLine}-${draft.lineNumber}` : `:${draft.lineNumber}`}`
    : null;

  return (
    <div
      className="rounded-lg border border-border/60 bg-card/60 p-2.5 space-y-2"
      data-testid="review-notes-panel"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-medium text-foreground/85">
          Review notes
          <span className="ml-1.5 text-muted-foreground">
            {unsent.length > 0 ? `${unsent.length} unsent` : grouped.length > 0 ? 'all sent' : ''}
          </span>
        </div>
        <Button
          type="button"
          size="sm"
          disabled={!sessionId || unsent.length === 0 || sending}
          onClick={() => void sendUnsent()}
          data-testid="review-notes-send"
          title={sessionId ? 'Queue every unsent note for the agent' : 'No session — notes cannot be sent'}
        >
          {sending ? <Loader2 className="size-3 animate-spin" /> : <Send className="size-3" />}
          Send{unsent.length > 0 ? ` ${unsent.length}` : ''}
        </Button>
      </div>

      {draft && (
        <div className="rounded-md border border-primary/30 bg-primary/5 p-2 space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate font-mono text-2xs text-muted-foreground" title={draftTarget ?? ''}>
              {draft.editId ? 'Editing note' : 'New note'} · {draftTarget}
            </span>
            <Button
              variant="bare"
              type="button"
              onClick={() => {
                setBody('');
                onDismissDraft();
              }}
              aria-label="Discard note draft"
              className="rounded p-0.5 text-muted-foreground/60 hover:bg-accent hover:text-foreground"
            >
              <X className="size-3" />
            </Button>
          </div>
          <textarea
            ref={textareaRef}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                saveDraft();
              }
            }}
            placeholder="Add note for the AI"
            rows={2}
            data-testid="review-note-composer"
            className="w-full resize-none rounded-md border border-border/60 bg-transparent px-2 py-1.5 text-xs outline-none placeholder:text-muted-foreground/50 focus:border-primary/40"
          />
          <div className="flex items-center gap-1.5">
            <Button type="button" size="sm" disabled={!body.trim()} onClick={saveDraft} data-testid="review-note-save">
              {draft.editId ? 'Update note' : 'Add note'}
            </Button>
          </div>
        </div>
      )}

      {grouped.length === 0 && !draft && (
        <div className="text-2xs text-muted-foreground/70">
          Hover a changed line and press + to leave a note for the agent.
        </div>
      )}

      {grouped.length > 0 && (
        <ul className="space-y-1">
          {grouped.map((n) => (
            <li
              key={n.id}
              className={cn(
                'flex items-start gap-2 rounded-md border border-border/50 bg-background/40 px-2 py-1.5',
                n.sentAt && 'opacity-70',
              )}
              data-testid="review-note-row"
            >
              <Button
                variant="bare"
                type="button"
                onClick={() => onJumpToNote(n)}
                className="shrink-0 rounded border border-border/60 px-1 font-mono text-2xs text-tier-3 hover:text-primary"
                title={`Jump to line ${n.lineNumber} of ${n.filePath}`}
              >
                {n.startLine !== undefined && n.startLine !== n.lineNumber
                  ? `${n.startLine}-${n.lineNumber}`
                  : n.lineNumber}
              </Button>
              <span className="min-w-0 flex-1 truncate text-xs text-foreground/85" title={n.body}>
                {n.body}
              </span>
              {n.sentAt && <span className="shrink-0 text-2xs text-muted-foreground">queued</span>}
              <Button
                variant="bare"
                type="button"
                onClick={() => onEditNote(n)}
                aria-label={`Edit note on line ${n.lineNumber}`}
                className="shrink-0 rounded p-0.5 text-muted-foreground/60 hover:bg-accent hover:text-foreground"
                data-testid="review-note-edit"
              >
                <Pencil className="size-3" />
              </Button>
              <Button
                variant="bare"
                type="button"
                onClick={() => {
                  if (sessionId) removeNote(sessionId, n.id);
                }}
                aria-label={`Delete note on line ${n.lineNumber}`}
                className="shrink-0 rounded p-0.5 text-muted-foreground/60 hover:bg-accent hover:text-danger"
                data-testid="review-note-delete"
              >
                <Trash2 className="size-3" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
