/* ── WorkingIndicator ─────────────────────────────────────────────────── */
/* Shown above the composer while a turn streams. Two parts:              */
/*   • the AUG wordmark (kept for identity),                              */
/*   • a progressive sentence stack pulled from the session's live        */
/*     activity store — every finished step ("Read x", "Ran tests",       */
/*     "Edited y") renders as its own line, newest at the bottom,         */
/*     older lines dimming away Claude-style. While the model is          */
/*     between steps the last line carries animated ellipsis dots.        */
/*                                                                        */
/* Its Todo card is ALSO the app's only todo surface: the right-drawer    */
/* "Tasks" pane was deleted (two surfaces did this job), so everything    */
/* that pane showed — the A-number per step, the step's commit SHA and    */
/* review state, the status groups, and the manual check-off door — lives */
/* here now. It reads `useSessionTodoRows` (the session snapshot the      */
/* drawer used) because that is the only payload carrying the proof       */
/* fields; see the note in store/liveActivity.ts.                         */

import { phaseLabel } from '@/lib/phase-labels';
import { useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { Check, ChevronDown, Circle, Loader2, Minus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { api } from '@/api/client';
import { useLiveActivityStore, useSessionTodoRows, type SessionTodoRow } from '@/store/liveActivity';
import { resolveUiSessionId } from '@/sections/chat/stream/session-id-map';
import { summarizeThoughtHeader } from '@/lib/process-summary';
import { cn } from '@/lib/utils';
import type { WorkbenchSession, WorkbenchTodo } from '@/types/workbench';

interface WorkingIndicatorProps {
  className?: string;
  /** Live session key — subscribes to that session's activity feed. */
  sessionId?: string | null;
  /** False once the turn settles. The sentence stack and the phase chip are
   *  mid-stream only; the Todo card keeps its rows (they come from the session
   *  snapshot, not the live store), which is what makes the check-off door and
   *  the per-step commit proof reachable after the answer is in. */
  streaming?: boolean;
}

/** Sentences visible at once (oldest fade out above). */
const MAX_LINES = 3;

function trimLine(s: string): string {
  const t = s.replace(/\s+/g, ' ').trim();
  return t.length > 96 ? `${t.slice(0, 94)}…` : t;
}

/* The AUG wordmark used to lead this indicator and is deliberately gone —
 * see the note at its former call site. The component keeps the identity
 * out of the status line and spends the row on the sentence stack. */
function Dots({ tone = 'text-muted-foreground/70' }: { tone?: string }) {
  return (
    <span className={`ml-0.5 inline-flex items-baseline ${tone}`} data-testid="typing-dots">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="text-[0.75rem] leading-none"
          animate={{ opacity: [0.15, 0.9, 0.15] }}
          transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.22, ease: 'easeInOut' }}
        >
          .
        </motion.span>
      ))}
    </span>
  );
}

/** The status bands of the expanded card, in the order the deleted drawer pane
 *  used. `cancelled` is the one group the snapshot cannot carry — it only ever
 *  arrives on a live frame. */
const TODO_GROUPS = [
  { status: 'in_progress', label: 'In progress' },
  { status: 'pending', label: 'Pending' },
  { status: 'completed', label: 'Completed' },
  { status: 'cancelled', label: 'Cancelled' },
] as const;

function todoStatusTone(status: SessionTodoRow['status']): string {
  if (status === 'completed') return 'text-muted-foreground/60 line-through';
  if (status === 'cancelled') return 'text-muted-foreground/50 line-through';
  if (status === 'in_progress') return 'text-foreground/90';
  return 'text-muted-foreground';
}

/** The review word and its colour, exactly as the pane said it. An absent
 *  review is NOT "unreviewed-by-accident" — it is a step nobody has looked at,
 *  which is what the pane also printed. */
function todoReviewBadge(review?: SessionTodoRow['review']): { label: string; tone: string } {
  switch (review?.status) {
    case 'approved':
      return { label: 'reviewed', tone: 'text-success-fg' };
    case 'changes_requested':
      return { label: 'changes requested', tone: 'text-warning-fg' };
    case 'failed':
      return { label: 'review failed', tone: 'text-destructive' };
    default:
      return { label: 'unreviewed', tone: 'text-muted-foreground/60' };
  }
}

/**
 * One row of the todo card. A row control lives here rather than in a second
 * component file because the pane that used to own it (`RightDrawerTasksSection`)
 * is deleted, and a check-off row is not a feature of its own.
 */
function TodoRowControl({
  row,
  stepNumber,
  busy,
  canToggle,
  onToggle,
}: {
  row: SessionTodoRow;
  /** 1-based position in the MODEL's own list order. This is the only
   *  human-readable mapping of the backend's stable `step_<hex>` id: the id is
   *  what `commit_step`/`review_step` take as `step_ref`, and it survives a
   *  wholesale `update_todos` because the host re-matches rows by content
   *  (backend step_commits.py, docs/agent/harness.md §Plan-step commits). */
  stepNumber: number;
  busy: boolean;
  canToggle: boolean;
  onToggle: (id: string) => void;
}) {
  const commits = row.commit ?? [];
  const review = todoReviewBadge(row.review);
  return (
    <Button
      type="button"
      variant="bare"
      role="checkbox"
      aria-checked={row.status === 'completed'}
      disabled={!canToggle}
      data-testid="working-todo-row"
      data-status={row.status}
      title={canToggle ? 'Click to toggle done' : undefined}
      onClick={() => onToggle(row.id)}
      className="flex w-full items-start gap-2 rounded px-1 py-0.5 text-left text-2xs leading-4 transition-colors hover:bg-muted/40 disabled:cursor-default disabled:opacity-100 disabled:hover:bg-transparent"
    >
      <span className="mt-px shrink-0" aria-hidden>
        {busy ? (
          <Loader2 className="size-3 animate-spin text-muted-foreground/70" />
        ) : row.status === 'completed' ? (
          <Check className="size-3 text-success-fg" />
        ) : row.status === 'in_progress' ? (
          <Loader2 className="size-3 animate-spin text-primary" />
        ) : row.status === 'cancelled' ? (
          <Minus className="size-3 text-muted-foreground/50" />
        ) : (
          <Circle className="size-3 text-muted-foreground/50" />
        )}
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="shrink-0 font-mono tabular-nums text-muted-foreground/55">
            A{stepNumber}
          </span>
          <span className={cn('min-w-0 truncate', todoStatusTone(row.status))} title={row.content}>
            {row.content}
          </span>
        </span>
        {commits.length > 0 ? (
          /* The step's proof: its last commit (short SHA, +N for the commits
             adopted inside the same window) and the review that ran against it.
             Nowhere else in the app shows a `commit_step` / `review_step`
             result — the pane that did is gone. */
          <span className="flex min-w-0 flex-wrap items-center gap-2 font-mono text-muted-foreground/60">
            <span className="tabular-nums">
              {commits[commits.length - 1]?.sha?.slice(0, 7)}
              {commits.length > 1 ? ` +${commits.length - 1}` : ''}
            </span>
            <span className={review.tone}>{review.label}</span>
          </span>
        ) : null}
      </span>
    </Button>
  );
}

export function WorkingIndicator({ className, sessionId, streaming = true }: WorkingIndicatorProps) {
  // The activity store is keyed by UI session id; the pane may hand us a
  // wb_* route id, so normalize before subscribing.
  const uiKey = sessionId ? resolveUiSessionId(sessionId) : null;
  // Raw slice subscription (stable reference) — deriving in a zustand
  // selector would allocate a fresh object per tick and loop re-renders.
  const entry = useLiveActivityStore((s) => (uiKey ? s.bySession[uiKey] : undefined));

  // Newest-last sentence list: one line per activity item, consecutive and
  // earlier duplicates collapse (a retried tool replaces its old line).
  const lines = useMemo(() => {
    const items = entry?.items ?? [];
    const out: string[] = [];
    for (const item of items) {
      let line = '';
      if (item.kind === 'thinking') {
        // Claude-style: the stack shows a distilled "what the model is
        // doing" header per thinking step, never a raw chain-of-thought
        // sentence. The in-flight tail distills with the live participle
        // form ("Loading the skill…"); settled steps keep their header.
        const rawDetail = (item.detail || '').split('\n')[0];
        const source = rawDetail || item.label;
        const distilled = summarizeThoughtHeader(
          source,
          item.status === 'running',
        );
        line = trimLine(distilled);
      } else {
        const base = item.status === 'error' ? `${item.label} — failed` : item.label;
        const detail = (item.detail || '').split('\n')[0];
        line = trimLine(detail && detail !== item.label ? `${base} · ${detail}` : base);
      }
      if (!line) continue;
      const dupIdx = out.lastIndexOf(line);
      if (dupIdx >= 0) out.splice(dupIdx, 1);
      out.push(line);
    }
    return out.slice(-MAX_LINES);
  }, [entry]);

  const idle = lines.length === 0;
  const execution = entry?.execution;
  // The checklist the deleted Tasks pane used to render: session snapshot rows
  // (which carry the commit/review proof) with the live SSE statuses overlaid
  // while they are the fresher of the two.
  const { rows: todoList, wbId } = useSessionTodoRows(sessionId);
  const todosDone = todoList.filter((t) => t.status === 'completed').length;
  const todoCurrent =
    todoList.find((t) => t.status === 'in_progress') ??
    todoList.find((t) => t.status === 'pending');
  const [todosOpen, setTodosOpen] = useState(false);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const qc = useQueryClient();

  /**
   * Check a step off by hand — the door only the deleted pane had, so the card
   * has to carry it or the ability is gone. Optimistic flip into the cached
   * session, then the PATCH; a failure rolls the flip back from the server.
   * The body sends id/content/status only: the backend re-attaches each step's
   * host-owned commit/review fields by its stable id, so a check-off cannot
   * erase a step's proof.
   */
  const handleToggle = (id: string) => {
    if (!wbId || togglingId) return;
    setTogglingId(id);
    const flip = (t: WorkbenchTodo): WorkbenchTodo =>
      t.id === id
        ? { ...t, status: t.status === 'completed' ? 'pending' : 'completed' }
        : t;
    qc.setQueryData<WorkbenchSession | undefined>(['workbench-session', wbId], (prev) =>
      prev?.todos ? { ...prev, todos: prev.todos.map(flip) } : prev,
    );
    void api
      .patch<{ todos?: WorkbenchTodo[] }>('/api/workbench/todos', {
        sessionId: wbId,
        todos: todoList.map((t) => ({
          id: t.id,
          content: t.content,
          status:
            t.id === id
              ? t.status === 'completed'
                ? 'pending'
                : 'completed'
              : t.status,
        })),
      })
      .then((saved) => {
        if (!saved.todos) return;
        qc.setQueryData<WorkbenchSession | undefined>(['workbench-session', wbId], (prev) =>
          prev ? { ...prev, todos: saved.todos ?? prev.todos } : prev,
        );
      })
      .catch(() => {
        void qc.invalidateQueries({ queryKey: ['workbench-session', wbId] });
      })
      .finally(() => setTogglingId(null));
  };

  return (
    <div
      className={className}
      role="status"
      aria-live="polite"
      aria-label="Assistant is working"
      data-aug-indicator
    >
      {/* No wordmark here. This line used to open with an `A U G U S T |`
          row — six separately-animated letters plus a caret, 18px tall, to
          spell a name that is already in the sidebar header and again in the
          status bar at the bottom of the window. Seven glyphs of chrome in
          front of the one thing this component exists to say.
          The reference spends this row on status instead: Claude's activity
          line is `···  <title>  <elapsed> ⌄`, and the word "Claude" appears
          nowhere in it. The progressive sentence stack below is that same
          line, and it is the part worth the pixels. */}
      <div className="flex flex-col items-center gap-0.5 py-0.5">
        {/* No "this session has no todos" empty state, deliberately: the pane
            that printed one lived in a panel the user opened on purpose, and a
            card that only ever says "nothing yet" above the composer is noise.
            The absence of the pill IS the empty state. */}
        {todoList.length > 0 ? (
          /* The todo checklist — the header pill answers "how many steps and
             where are we"; expanding it shows every row with its status, its
             step-commit proof and the check-off door. Rows come from the
             session snapshot with the live `submit_todos` / `update_todos`
             statuses on top, so the list survives the end of the turn. Falls
             back to the bare phase pill when the model never submitted todos. */
          <div className="flex flex-col items-center gap-1" data-testid="working-todos">
            <Button
              type="button"
              variant="bare"
              onClick={() => setTodosOpen((v) => !v)}
              aria-expanded={todosOpen}
              className="flex items-center gap-1.5 rounded-full border border-primary/25 bg-primary/10 px-2.5 py-px text-2xs font-medium tracking-wide text-primary transition-colors hover:bg-primary/15"
              data-testid="working-todos-toggle"
              title="Todo list"
            >
              {/* The CURRENT STEP, not the model's name for its own list.
                  The reference's working line is "··· <the work> <elapsed> >"
                  -- "Fathoming  21s" -- and the work there is the TODO item.
                  The pill's label used to be the model's OPTIONAL list title,
                  which is free text the model writes, so the same feature
                  could be called "Plan", "Research" or "Fix the charts"
                  depending on the turn. The feature is a TODO list, it is
                  named "Todo", and the step says what the work IS. */}
              <span className="max-w-[240px] truncate">{todoCurrent?.content || 'Todo'}</span>
              <span className="tabular-nums opacity-80">
                {todosDone}/{todoList.length}
              </span>
              <ChevronDown
                className={cn('size-3 transition-transform', todosOpen && 'rotate-180')}
                aria-hidden
              />
            </Button>
            {todosOpen ? (
              <div className="w-full max-w-md rounded-lg border border-border/50 bg-card/80 px-3 py-1.5 text-left">
                {/* The list's NAME, so the feature is called what it is, plus the
                    scope line the deleted pane carried in its brand strip:
                    "Current step" while one is running, "All steps" when none is. */}
                <div className="mb-1 flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-muted-foreground/70">
                  <span data-testid="working-todo-caption">Todo</span>
                  <span className="ml-auto text-muted-foreground/60">
                    {todoCurrent ? 'Current step' : 'All steps'}
                  </span>
                  <span className="font-mono tabular-nums opacity-70">
                    {todosDone}/{todoList.length}
                  </span>
                </div>
                {TODO_GROUPS.map((group) => {
                  const items = todoList.filter((row) => row.status === group.status);
                  if (items.length === 0) return null;
                  return (
                    <div key={group.status} className="mb-1 last:mb-0">
                      <div className="flex items-center gap-1.5 px-1 text-2xs font-semibold uppercase tracking-wider text-muted-foreground/60">
                        <span className="size-1.5 rounded-full bg-current" aria-hidden />
                        {group.label}
                        <span className="ml-auto font-mono tabular-nums opacity-70">
                          {items.length}
                        </span>
                      </div>
                      {items.map((row) => (
                        <TodoRowControl
                          key={row.id}
                          row={row}
                          stepNumber={todoList.indexOf(row) + 1}
                          busy={togglingId === row.id}
                          canToggle={Boolean(wbId)}
                          onToggle={handleToggle}
                        />
                      ))}
                    </div>
                  );
                })}
              </div>
            ) : todoCurrent ? (
              <div
                className="max-w-md truncate text-center text-2xs text-muted-foreground"
                data-testid="working-todo-current"
                title={todoCurrent.content}
              >
                {todoCurrent.content}
              </div>
            ) : null}
          </div>
        ) : streaming && execution ? (
          /* The PHASE, humanised and QUIET. It rendered the raw enum value
             in caps, so the pill literally read "RESEARCH" and looked like
             the name of a feature rather than a piece of progress
             bookkeeping. The phase is a status, not a title. Mid-stream only:
             it is the live turn's state, and the strip now stays mounted after
             the turn when the session still has todos. */
          <div
            className="rounded-full border border-primary/25 bg-primary/10 px-2 py-px text-2xs font-medium text-primary"
            data-testid="working-phase"
          >
            {phaseLabel(execution.phase)}
            {execution.step > 0 ? ` · step ${execution.step}` : ''}
          </div>
        ) : null}
        {/* Mid-stream only: the sentence stack is the live turn's narration,
            and the strip now stays mounted after the turn when the session
            still has todos. */}
        {streaming ? (
          <div className="w-full max-w-xl" data-testid="working-lines">
            {idle ? (
              <div
                key="idle-line"
                className="flex items-baseline justify-center gap-1 text-[0.71875rem] italic leading-4 text-muted-foreground/70"
              >
                <span>Thinking</span>
                <Dots />
              </div>
            ) : (
              <div className="flex flex-col gap-0.5">
                {lines.map((line, i) => {
                  const isLast = i === lines.length - 1;
                  const opacityClass = isLast
                    ? 'opacity-95 text-foreground/90 font-normal'
                    : i === lines.length - 2
                      ? 'opacity-60 text-muted-foreground'
                      : 'opacity-35 text-muted-foreground';
                  return (
                    <div
                      key={`slot-${i}`}
                      className={`truncate text-center text-[0.71875rem] leading-4 transition-opacity duration-150 ${opacityClass}`}
                    >
                      <span>{line}</span>
                      {isLast && <Dots tone="text-primary/60" />}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}
