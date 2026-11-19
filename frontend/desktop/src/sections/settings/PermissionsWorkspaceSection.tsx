/* ── Permissions & Workspace ───────────────────────────────────────────── */
/* The Part D diet's fourth tab: one door for what the agent may touch and
 * where it may touch it — the sandbox modes, tool grants, privacy scrubbing
 * and external access, which used to be four separate hidden-tier sections
 * each carrying its own rail row. Hosting them as tabs keeps every control
 * reachable while the rail stays at five rows. */

import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Globe, ShieldCheck, Trash2 } from 'lucide-react';
import { SettingsTabs } from '@/components/settings/SettingsTabs';
import { settingsTabPanelId } from '@/components/settings/settingsTabIds';
import { AgentSandboxSection } from './AgentSandboxSection';
import { PrivacySection } from './PrivacySection';
import { ExternalAccessSection } from './ExternalAccessSection';

const TABS = [
  { key: 'permissions', label: 'Permissions', icon: ShieldCheck, description: 'Tool grants and sandbox modes' },
  { key: 'privacy', label: 'Privacy', icon: Trash2, description: 'What leaves the machine' },
  { key: 'access', label: 'External access', icon: Globe, description: 'API doors and pairing' },
] as const;

type TabKey = (typeof TABS)[number]['key'];
const TAB_KEYS = new Set<string>(TABS.map((t) => t.key));

function asTabKey(value: string): TabKey | null {
  return TAB_KEYS.has(value) ? (value as TabKey) : null;
}

export function PermissionsWorkspaceSection() {
  const [params, setParams] = useSearchParams();
  const sectionParam = params.get('section') || '';
  const initial = asTabKey(sectionParam) ?? 'permissions';
  const [tab, setTab] = useState<TabKey>(initial);

  const activeTab = useMemo(() => asTabKey(sectionParam) ?? tab, [sectionParam, tab]);

  const onChange = (next: string) => {
    const key = asTabKey(next) ?? 'permissions';
    setTab(key);
    const nextParams = new URLSearchParams(params);
    nextParams.set('section', key);
    setParams(nextParams, { replace: true });
  };

  return (
    <div className="px-8 py-6 space-y-4 h-full flex flex-col">
      <header className="shrink-0">
        <h2 className="text-2xl font-semibold tracking-tight text-foreground">Permissions &amp; Workspace</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          What the agent may touch, what leaves the machine, and who else can reach August.
        </p>
      </header>
      <div className="flex-1 min-h-0 overflow-auto">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
          <SettingsTabs
            className="lg:sticky lg:top-2 shrink-0"
            orientation="vertical"
            value={activeTab}
            onChange={onChange}
            items={[...TABS]}
            label="Permissions and workspace views"
            panelIdPrefix="permissions-workspace"
          />
          <div
            className="min-w-0 flex-1"
            role="tabpanel"
            id={settingsTabPanelId('permissions-workspace', activeTab) ?? undefined}
            aria-labelledby={`permissions-workspace-tab-${activeTab}`}
          >
            {activeTab === 'permissions' && <AgentSandboxSection />}
            {activeTab === 'privacy' && <PrivacySection />}
            {activeTab === 'access' && <ExternalAccessSection />}
          </div>
        </div>
      </div>
    </div>
  );
}

export default PermissionsWorkspaceSection;
