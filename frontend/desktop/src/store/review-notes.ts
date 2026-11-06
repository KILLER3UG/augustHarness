/* ── Review notes ────────────────────────────────────────────────────── */
/* Line-anchored notes the user writes on a diff and sends back to the
 * agent as one queued message. User-authored, device-local state —
 * localStorage keyed by session (the store pattern the kanban board used
 * before it moved server-side); notes are NOT agent memory and never
 * enter the facts store.
 *
 * Lifecycle is one field: `sentAt` is set once the backend accepts the
 * queue write (the transcript bubble the drain renders is the delivery
 * receipt), and editing the note clears it so it can be sent again.
 */

import { create } from 'zustand';

export interface ReviewNote {
  id: string;
  filePath: string;
  /** The row the note anchors to — oldLine for removed rows, newLine otherwise. */
  lineNumber: number;
  /** Inclusive range start; must be <= lineNumber when present. */
  startLine?: number;
  body: string;
  createdAt: number;
  /** Set once the note has been handed to the queue. Edits clear it. */
  sentAt?: number;
}

interface ReviewNotesState {
  bySession: Record<string, ReviewNote[]>;
  addNote: (
    sessionId: string,
    note: Pick<ReviewNote, 'filePath' | 'lineNumber' | 'body'> & { startLine?: number },
  ) => ReviewNote;
  /** Body/line edits clear `sentAt` — a changed note must be sent again. */
  updateNote: (
    sessionId: string,
    id: string,
    patch: Partial<Pick<ReviewNote, 'body' | 'lineNumber' | 'startLine'>>,
  ) => void;
  removeNote: (sessionId: string, id: string) => void;
  /** Mark notes queued after the backend accepted the queue write. */
  markQueued: (sessionId: string, ids: readonly string[]) => void;
  clearSession: (sessionId: string) => void;
}

const KEY_PREFIX = 'august.review-notes.v1.';
const MAX_NOTES_PER_SESSION = 200;

function loadSession(sessionId: string): ReviewNote[] {
  try {
    const raw = localStorage.getItem(KEY_PREFIX + sessionId);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (n): n is ReviewNote =>
        !!n && typeof n === 'object' &&
        typeof (n as ReviewNote).id === 'string' &&
        typeof (n as ReviewNote).filePath === 'string' &&
        typeof (n as ReviewNote).body === 'string',
    );
  } catch {
    return [];
  }
}

function persistSession(sessionId: string, notes: ReviewNote[]): void {
  try {
    localStorage.setItem(KEY_PREFIX + sessionId, JSON.stringify(notes));
  } catch {
    // Storage full or unavailable: the in-memory state stays authoritative
    // for this session; nothing user-facing lies about what was saved.
  }
}

/** Hydrate every stored session once at creation — the key set is small
 * (one entry per session that ever had notes) and zustand needs the map
 * populated for selectors to see anything. */
function hydrateAll(): Record<string, ReviewNote[]> {
  const out: Record<string, ReviewNote[]> = {};
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key || !key.startsWith(KEY_PREFIX)) continue;
      const sessionId = key.slice(KEY_PREFIX.length);
      const notes = loadSession(sessionId);
      if (notes.length > 0) out[sessionId] = notes;
    }
  } catch {
    // Private mode / storage disabled: notes work in-memory for this visit.
  }
  return out;
}

let nextId = 0;
function newId(): string {
  nextId += 1;
  return `note_${Date.now().toString(36)}_${nextId.toString(36)}`;
}

export const useReviewNotes = create<ReviewNotesState>((set, get) => ({
  bySession: hydrateAll(),

  addNote: (sessionId, note) => {
    const full: ReviewNote = { ...note, id: newId(), createdAt: Date.now() };
    set((s) => {
      const notes = [...(s.bySession[sessionId] ?? []), full].slice(-MAX_NOTES_PER_SESSION);
      persistSession(sessionId, notes);
      return { bySession: { ...s.bySession, [sessionId]: notes } };
    });
    return full;
  },

  updateNote: (sessionId, id, patch) =>
    set((s) => {
      const notes = (s.bySession[sessionId] ?? []).map((n) =>
        n.id === id ? { ...n, ...patch, sentAt: undefined } : n,
      );
      persistSession(sessionId, notes);
      return { bySession: { ...s.bySession, [sessionId]: notes } };
    }),

  removeNote: (sessionId, id) =>
    set((s) => {
      const notes = (s.bySession[sessionId] ?? []).filter((n) => n.id !== id);
      persistSession(sessionId, notes);
      return { bySession: { ...s.bySession, [sessionId]: notes } };
    }),

  markQueued: (sessionId, ids) =>
    set((s) => {
      const idSet = new Set(ids);
      const notes = (s.bySession[sessionId] ?? []).map((n) =>
        idSet.has(n.id) ? { ...n, sentAt: Date.now() } : n,
      );
      persistSession(sessionId, notes);
      return { bySession: { ...s.bySession, [sessionId]: notes } };
    }),

  clearSession: (sessionId) =>
    set((s) => {
      try {
        localStorage.removeItem(KEY_PREFIX + sessionId);
      } catch {
        // Nothing to do; the in-memory entry is dropped below regardless.
      }
      const { [sessionId]: _dropped, ...rest } = s.bySession;
      return { bySession: rest };
    }),
}));

/** Unsent (not yet queued) notes for a session, oldest first — the send
 * order and the unsent-count badge both read this. */
export function unsentNotes(notes: ReviewNote[] | undefined): ReviewNote[] {
  return (notes ?? []).filter((n) => !n.sentAt);
}
