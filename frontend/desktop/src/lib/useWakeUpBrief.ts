/* Wake-up brief transport — Phase 4 "always-on assistant".
 *
 * The shell asks the backend for the brief on app start and on every
 * return-to-focus (sleep resume, unlock, tab visible again all surface as
 * focus/visibility events). The BACKEND owns the decision — the on/off
 * switch, the one cooldown, and at most one brief per start — so this hook
 * is deliberately thin: fetch, hold, dismiss. A failed fetch is swallowed:
 * the brief is a nicety, never an error surface.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/api/client';

export interface WakeUpBrief {
  shouldBrief: boolean;
  reason?: string;
  quiet?: boolean;
  text?: string;
  recentSessions?: Array<{ title: string; updated: string; messages?: number }>;
  automations?: Array<{ name: string; status: string }>;
}

export function useWakeUpBrief() {
  const [brief, setBrief] = useState<WakeUpBrief | null>(null);
  const inflight = useRef(false);

  const check = useCallback(async (reason: 'startup' | 'focus') => {
    if (inflight.current) return;
    inflight.current = true;
    try {
      const r = await api.get<WakeUpBrief>(
        `/api/wake-up/brief?reason=${encodeURIComponent(reason)}`,
      );
      if (r?.shouldBrief) setBrief(r);
    } catch {
      /* best-effort — the brief never becomes an error surface */
    } finally {
      inflight.current = false;
    }
  }, []);

  useEffect(() => {
    void check('startup');
    const onReturn = () => {
      if (document.visibilityState === 'visible') void check('focus');
    };
    window.addEventListener('focus', onReturn);
    document.addEventListener('visibilitychange', onReturn);
    return () => {
      window.removeEventListener('focus', onReturn);
      document.removeEventListener('visibilitychange', onReturn);
    };
  }, [check]);

  const dismiss = useCallback(async () => {
    setBrief(null);
    try {
      await api.post('/api/wake-up/dismiss');
    } catch {
      /* best-effort */
    }
  }, []);

  return { brief, dismiss };
}
