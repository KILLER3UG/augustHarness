/**
 * Live turn activity for the inline working strip above the composer.
 * Keeps users informed while chat thoughts are collapsed mid-stream.
 * State is keyed per session so concurrent chats do not overwrite each other.
 *
 * The todo checklist is only half here: `todosUpdated` publishes the live
 * statuses, and the session snapshot (`useSessionTodoRows` at the bottom of
 * this file) carries the host-owned step-commit/review proof. Both surfaces —
 * the card and the thread strip's visibility gate — read that one hook.
 */

import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { create } from 'zustand';
import { getWorkbenchSession } from '@/api/workbench';
import { normalizeWorkbenchSession } from '@/lib/workbench-plan';
import { useSessionsStore } from '@/store/sessions';
import type { WorkbenchTodo } from '@/types/workbench';

export type LiveActivityKind = 'thinking' | 'view' | 'edit' | 'run' | 'tool';

export interface LiveActivityItem {
  id: string;
  kind: LiveActivityKind;
  label: string;
  detail?: string;
  status: 'running' | 'done' | 'error';
  at: number;
}

/** Latest update_state phase/step (executionState SSE event). */
export interface ExecutionStateLive {
  phase: string;
  step: number;
  at: number;
}

/** Live todo checklist from `submit_todos` / `update_todos` (todosUpdated). */
export interface TodosLive {
  items: Array<{
    id: string;
    content: string;
    status: 'pending' | 'in_progress' | 'completed' | 'cancelled';
  }>;
  title: string;
  at: number;
}

export interface SessionLiveActivity {
  headline: string;
  items: LiveActivityItem[];
  execution?: ExecutionStateLive;
  todos?: TodosLive;
}

interface LiveActivityState {
  bySession: Record<string, SessionLiveActivity>;
}

export const useLiveActivityStore = create<LiveActivityState>(() => ({
  bySession: {},
}));

const MAX_ITEMS = 40;

export function publishLiveActivity(input: {
  sessionId: string;
  headline: string;
  items: LiveActivityItem[];
}): void {
  if (!input.sessionId) return;
  useLiveActivityStore.setState((prev) => ({
    bySession: {
      ...prev.bySession,
      [input.sessionId]: {
        headline: input.headline,
        items: input.items.slice(-MAX_ITEMS),
        // The timeline rebuilds items every rAF — preserve the phase/step
        // chip and todo list published by their own SSE handlers.
        execution: prev.bySession[input.sessionId]?.execution,
        todos: prev.bySession[input.sessionId]?.todos,
      },
    },
  }));
}

export function publishExecutionState(sessionId: string, phase: string, step: number): void {
  if (!sessionId || !phase) return;
  useLiveActivityStore.setState((prev) => {
    const entry = prev.bySession[sessionId];
    return {
      bySession: {
        ...prev.bySession,
        [sessionId]: {
          headline: entry?.headline ?? '',
          items: entry?.items ?? [],
          execution: { phase, step, at: Date.now() },
          todos: entry?.todos,
        },
      },
    };
  });
}

/** `todosUpdated` SSE handler — replaces the live checklist wholesale. */
export function publishTodos(
  sessionId: string,
  items: TodosLive['items'],
  title: string,
): void {
  if (!sessionId) return;
  useLiveActivityStore.setState((prev) => {
    const entry = prev.bySession[sessionId];
    return {
      bySession: {
        ...prev.bySession,
        [sessionId]: {
          headline: entry?.headline ?? '',
          items: entry?.items ?? [],
          execution: entry?.execution,
          todos: { items, title, at: Date.now() },
        },
      },
    };
  });
}

export function clearLiveActivity(sessionId?: string | null): void {
  if (!sessionId) {
    useLiveActivityStore.setState({ bySession: {} });
    return;
  }
  useLiveActivityStore.setState((prev) => {
    if (!(sessionId in prev.bySession)) return prev;
    const next = { ...prev.bySession };
    delete next[sessionId];
    return { bySession: next };
  });
}

/** Selector helper for a single session's activity. */
export function selectSessionLiveActivity(
  state: LiveActivityState,
  sessionId: string | null | undefined,
): SessionLiveActivity {
  if (!sessionId) return { headline: '', items: [] };
  return state.bySession[sessionId] ?? { headline: '', items: [] };
}

/* ── The todo checklist the inline card renders ─────────────────────────── */

/**
 * One row of the kept todo card: the live checklist fields, plus the
 * host-owned proof fields (`commit` / `review`) the session snapshot carries.
 * Those two fields are the reason this hook reads REST at all — see below.
 */
export interface SessionTodoRow {
  id: string;
  content: string;
  status: 'pending' | 'in_progress' | 'completed' | 'cancelled';
  commit?: WorkbenchTodo['commit'];
  review?: WorkbenchTodo['review'];
}

/**
 * The session's todo rows, read by BOTH mount points of the one remaining todo
 * surface: the card itself and the thread strip's visibility gate (a gate the
 * card cannot own, because its parent collapses the subtree when no turn is
 * streaming).
 *
 * Why the snapshot and not a widened SSE payload: `commit_step` / `review_step`
 * write the host fields onto the session's todos and only invalidate
 * `workbench-session` — they never emit a `todosUpdated` frame, so no live
 * payload can carry a SHA or a review state. The live entry is also cleared the
 * moment the stream settles, so a store-only card would take the checklist —
 * and the manual check-off door — down with it. The frame stays the FAST path:
 * when it is newer than the last snapshot fetch its statuses overlay the
 * server's, so a model tick lands on screen before the refetch does.
 */
export function useSessionTodoRows(
  sessionId?: string | null,
): { rows: SessionTodoRow[]; wbId: string | null } {
  // Primitive selectors only — an object-returning selector re-renders every
  // consumer on any session-list change (the same trap noted above).
  const uiKey = useSessionsStore((s) => {
    if (!sessionId) return null;
    const hit = s.sessions.find(
      (m) => m.id === sessionId || m.workbenchSessionId === sessionId,
    );
    return hit?.id ?? sessionId;
  });
  // Strictly the backend id: a chat that has never opened a Workbench session
  // has none, and must not fire a request for its UI id (that is a 404 on every
  // thread mount). `resolveWorkbenchSessionId` falls back to the UI id.
  const wbId = useSessionsStore((s) => {
    if (!sessionId) return null;
    const hit = s.sessions.find(
      (m) => m.id === sessionId || m.workbenchSessionId === sessionId,
    );
    return hit?.workbenchSessionId ?? null;
  });
  const live = useLiveActivityStore((s) => (uiKey ? s.bySession[uiKey]?.todos : undefined));
  const snapshot = useQuery({
    queryKey: ['workbench-session', wbId],
    queryFn: () => getWorkbenchSession(wbId as string),
    enabled: Boolean(wbId),
    retry: false,
  });
  // Depend on the raw query result, not a derived array: `normalizeWorkbenchSession`
  // builds a fresh object per render, which would re-run the merge every tick.
  const data = snapshot.data;
  const fetchedAt = snapshot.dataUpdatedAt;

  const rows = useMemo<SessionTodoRow[]>(() => {
    const server = normalizeWorkbenchSession(data)?.todos ?? [];
    const liveItems = live?.items ?? [];
    if (server.length === 0) return liveItems;
    const overlayLive = !!live && live.at > fetchedAt;
    const byId = new Map(liveItems.map((item) => [item.id, item]));
    const merged: SessionTodoRow[] = server.map((todo) => ({
      ...todo,
      status: overlayLive ? byId.get(todo.id)?.status ?? todo.status : todo.status,
    }));
    if (overlayLive) {
      // A fresh `submit_todos` list can hold rows the last fetch never saw.
      for (const item of liveItems) {
        if (!server.some((todo) => todo.id === item.id)) merged.push({ ...item });
      }
    }
    return merged;
  }, [data, live, fetchedAt]);

  return { rows, wbId };
}
