import { create } from 'zustand';

/**
 * composer-seed — a prompt handed to the composer by something OUTSIDE the chat.
 *
 * WHY A STORE, NOT A DRAFT
 * -----------------------
 * Composer drafts are keyed by session id (`composerDraftStorageKey`), and the
 * "new chat" session id does not exist until the chat mounts. So a control in the
 * sidebar cannot write a draft the composer will ever read: the key it would need
 * is null at that moment. That is why this exists instead.
 *
 * The rail's "Ask the agent to create a bot" action sets a seed here and asks the
 * chat surface to open. ChatThread consumes it once on mount — into the composer,
 * and only into the composer — then clears it, so the same prompt cannot reappear
 * after a later navigation.
 *
 * One consumer, one producer, and the value is never persisted: a seed is an
 * instruction for the next composer mount, not a draft the user is keeping.
 */

interface ComposerSeed {
  /** The text to place in the composer. */
  text: string;
  /** Bumped on every set so a repeat of the same text still lands. */
  nonce: number;
}

interface ComposerSeedState {
  seed: ComposerSeed | null;
  setSeed: (text: string) => void;
  clearSeed: () => void;
}

export const useComposerSeedStore = create<ComposerSeedState>((set) => ({
  seed: null,
  setSeed: (text: string) => set((s) => ({ seed: { text, nonce: (s.seed?.nonce ?? 0) + 1 } })),
  clearSeed: () => set({ seed: null }),
}));

/** Imperative access for a non-React caller (kept minimal, matching $sessions). */
export const composerSeed = {
  set: (text: string): void => useComposerSeedStore.getState().setSeed(text),
  take: (): string | null => {
    const seed = useComposerSeedStore.getState().seed;
    if (seed) useComposerSeedStore.getState().clearSeed();
    return seed?.text ?? null;
  },
};