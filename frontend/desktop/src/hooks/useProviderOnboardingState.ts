/* ── Setup checklist / first-run onboarding state ───────────────────── */
/* Shows when the user has not completed or skipped the checklist.        */
/* Tracks provider, workspace, Google, plus doctor (backend/MCP/disk).    */

import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import { providersApi, type Provider } from '@/api/providers';
import { getWorkbenchDoctor, type DoctorReport } from '@/api/workbench';
import { useSessionsStore } from '@/store/sessions';

const SKIP_KEY = 'august-onboarding-skipped';
const DONE_KEY = 'august-setup-checklist-done';

export type SetupCheckItem = {
  id: 'provider' | 'model' | 'workspace' | 'google' | 'doctor' | 'local';
  label: string;
  description: string;
  done: boolean;
  optional?: boolean;
  href?: string;
};

/** Loopback base URL — the same rule the backend uses to admit a keyless
 * provider, so a local server counts as a connected provider. */
function isLoopbackUrl(url: string): boolean {
  try {
    const host = new URL(url).hostname.toLowerCase();
    return host === 'localhost' || host === '::1' || host.startsWith('127.');
  } catch {
    return false;
  }
}

export function useProviderOnboardingState() {
  const providersQ = useQuery<Provider[]>({
    queryKey: ['providers'],
    queryFn: () => providersApi.list(),
    staleTime: 30_000,
  });

  const googleQ = useQuery<{
    connections?: Record<string, { connected?: boolean; hasClientId?: boolean }>;
  }>({
    queryKey: ['integrations-connections'],
    queryFn: () =>
      api.get<{
        connections?: Record<string, { connected?: boolean; hasClientId?: boolean }>;
      }>('/api/service-connections'),
    staleTime: 30_000,
    retry: false,
  });

  const doctorQ = useQuery<DoctorReport>({
    queryKey: ['workbench-doctor'],
    queryFn: () => getWorkbenchDoctor(),
    staleTime: 15_000,
    retry: false,
  });

  const sessions = useSessionsStore((s) => s.sessions);
  const hasWorkspace = sessions.some((s) => Boolean(s.workspacePath));

  const providers = useMemo(() => providersQ.data ?? [], [providersQ.data]);
  const hasProvider = providers.length > 0;
  const hasLocalProvider = providers.some((p) => isLoopbackUrl(p.baseUrl ?? ''));

  /* The default-model step: `chat_default` in the cognitive fleet is what
   * gives every new chat a working model without a per-session pick, so
   * first launch is "pick a model (local or API), done". */
  const qc = useQueryClient();
  const cognitiveQ = useQuery<{
    fleet?: Record<string, string>;
    fleetProviders?: Record<string, string>;
  }>({
    queryKey: ['config-cognitive'],
    queryFn: () => api.get('/api/config/cognitive'),
    staleTime: 30_000,
  });
  const chatDefault = (cognitiveQ.data?.fleet?.chat_default ?? '').trim();
  const chatDefaultProvider = (cognitiveQ.data?.fleetProviders?.chat_default ?? '').trim();
  const chatDefaultSet = chatDefault.length > 0;

  /* Reachability gate for the sensible default: a provider the health monitor
   * has marked offline is not "downloaded or reachable", so "Done without
   * picking" must never fall back to it. Unknown (not yet probed) stays
   * eligible — a fresh install must not be locked out before its first probe. */
  const healthQ = useQuery<{ results?: { provider?: string; online?: boolean }[] }>({
    queryKey: ['providers-health'],
    queryFn: () => api.get('/api/providers/health'),
    staleTime: 30_000,
    retry: false,
  });
  const offlineProviders = useMemo(() => {
    const offline = new Set<string>();
    for (const row of healthQ.data?.results ?? []) {
      if (row.provider && row.online === false) offline.add(row.provider);
    }
    return offline;
  }, [healthQ.data]);

  const setDefaultModel = async (modelId: string, providerName: string) => {
    await api.put('/api/config/cognitive', {
      fleet: { chat_default: modelId },
      fleetProviders: { chat_default: providerName },
    });
    await qc.invalidateQueries({ queryKey: ['config-cognitive'] });
  };

  /** Sensible default: local-first (the spec's local-first principle — the
   * default model keeps data on the machine when one is connected), then the
   * first enabled provider with a catalog; within a provider prefer a pinned
   * model, then a free one, then the first listed. Providers the health
   * monitor marked offline are never chosen. */
  const defaultModelChoice = useMemo<{ modelId: string; providerName: string } | null>(() => {
    const eligible = providers.filter(
      (p) => p.enabled && p.models.length > 0 && !offlineProviders.has(p.id),
    );
    const localFirst = [...eligible].sort(
      (a, b) =>
        Number(isLoopbackUrl(b.baseUrl ?? '')) - Number(isLoopbackUrl(a.baseUrl ?? '')),
    );
    for (const p of localFirst) {
      const preferred =
        p.models.find((m) => m.pinned) ?? p.models.find((m) => m.free) ?? p.models[0];
      if (preferred?.id) return { modelId: preferred.id, providerName: p.name };
    }
    return null;
  }, [providers, offlineProviders]);

  /** The one-click choices shown inline in the checklist: every enabled,
   * reachable provider's catalog, recommended choice first, capped to keep
   * the checklist a checklist. Each choice labels where the data goes. */
  const modelChoices = useMemo<
    { modelId: string; providerName: string; badge?: string; local: boolean }[]
  >(() => {
    const out: { modelId: string; providerName: string; badge?: string; local: boolean }[] = [];
    for (const p of providers) {
      if (!p.enabled || offlineProviders.has(p.id)) continue;
      const local = isLoopbackUrl(p.baseUrl ?? '');
      for (const m of p.models) {
        if (out.length >= 8) break;
        const badge = m.pinned ? 'pinned' : m.free ? 'free' : undefined;
        out.push({ modelId: m.id, providerName: p.name, badge, local });
      }
    }
    const rec = defaultModelChoice;
    if (!rec) return out;
    const idx = out.findIndex(
      (c) => c.modelId === rec.modelId && c.providerName === rec.providerName,
    );
    if (idx <= 0) return out;
    const [hit] = out.splice(idx, 1);
    return [hit, ...out];
  }, [providers, defaultModelChoice, offlineProviders]);

  const googleConnected = Boolean(googleQ.data?.connections?.google?.connected);
  const googleReady = Boolean(
    googleQ.data?.connections?.google?.connected ||
      googleQ.data?.connections?.google?.hasClientId,
  );

  const doctor = doctorQ.data;
  const doctorOk = Boolean(doctor?.ok);
  const doctorDetail =
    doctor?.summary ||
    (doctorQ.isError ? 'Could not reach doctor endpoint' : 'Checking health…');

  // Reactive dismissal flag — localStorage alone does not trigger a
  // re-render, so skip()/markDone() must flip this state to close the
  // modal immediately (seeded from storage on first mount).
  const [dismissed, setDismissed] = useState(
    () =>
      typeof localStorage !== 'undefined' &&
      (localStorage.getItem(SKIP_KEY) === 'true' || localStorage.getItem(DONE_KEY) === 'true'),
  );

  const chatDefaultLocal = useMemo(() => {
    if (!chatDefaultSet) return false;
    const row = providers.find(
      (p) => p.name === chatDefaultProvider || p.id === chatDefaultProvider,
    );
    return row ? isLoopbackUrl(row.baseUrl ?? '') : false;
  }, [providers, chatDefaultProvider, chatDefaultSet]);

  const checks: SetupCheckItem[] = useMemo(
    () => [
      {
        id: 'provider',
        label: 'Connect an AI provider',
        description: 'Anthropic, OpenAI, or any OpenAI-compatible endpoint',
        done: hasProvider,
        href: '/settings/providers',
      },
      {
        id: 'model',
        label: 'Pick your default model',
        description: chatDefaultSet
          ? chatDefaultLocal
            ? `Default model: ${chatDefault} — runs locally, data stays on this device`
            : `Default model: ${chatDefault} — cloud via ${chatDefaultProvider || 'the provider'}`
          : defaultModelChoice
            ? `One click — ${defaultModelChoice.modelId} becomes the default`
            : 'Choose the model your chats use by default',
        done: chatDefaultSet,
      },
      {
        id: 'workspace',
        label: 'Open a project folder',
        description: 'Pick a workspace so August can read and edit your files',
        done: hasWorkspace,
        href: '/',
      },
      {
        id: 'doctor',
        label: 'System health',
        description: doctorDetail,
        done: doctorOk,
        optional: true,
        href: '/settings/system-health',
      },
      {
        id: 'local',
        label: 'Use local models (optional)',
        description: hasLocalProvider
          ? 'A local server is connected'
          : 'Detect LM Studio, Ollama, or llama.cpp running on this machine',
        done: hasLocalProvider,
        optional: true,
        href: '/settings/model-local',
      },
      {
        id: 'google',
        label: 'Sign in with Google (optional)',
        description: 'Gmail, Calendar, and Drive in one click when ready',
        done: googleConnected,
        optional: true,
        href: '/settings/plugins',
      },
    ],
    [hasProvider, hasWorkspace, googleConnected, doctorOk, doctorDetail, hasLocalProvider, chatDefaultSet, chatDefault, chatDefaultProvider, chatDefaultLocal, defaultModelChoice],
  );
  const requiredDone = hasProvider; // workspace is strongly recommended but provider is the hard gate
  const allCoreDone = hasProvider && hasWorkspace;

  // Show the setup checklist while core setup is incomplete AND the user has
  // not skipped/marked it done. Fully-configured users never see it again —
  // previously the tautology `shouldShowChecklist || shouldShow` re-showed
  // the modal on every launch until an explicit Done/Skip.
  const shouldShow = !dismissed && !providersQ.isLoading && !allCoreDone;

  const skip = () => {
    localStorage.setItem(SKIP_KEY, 'true');
    setDismissed(true);
  };

  const markDone = () => {
    localStorage.setItem(DONE_KEY, 'true');
    localStorage.setItem(SKIP_KEY, 'true');
    setDismissed(true);
  };

  const resetDismissed = () => {
    localStorage.removeItem(SKIP_KEY);
    localStorage.removeItem(DONE_KEY);
    setDismissed(false);
  };

  return {
    shouldShow,
    providers,
    checks,
    hasProvider,
    hasWorkspace,
    googleConnected,
    googleReady,
    doctor,
    doctorOk,
    allCoreDone,
    requiredDone,
    dismissed,
    isLoading: providersQ.isLoading,
    skip,
    markDone,
    resetDismissed,
    chatDefaultSet,
    setDefaultModel,
    defaultModelChoice,
    modelChoices,
  };
}
