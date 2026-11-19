/* ── Session list nav — sidebar header ("August" + collapse) & New thread ─── */

import { Plus } from 'lucide-react';
import { SignalIcon } from '@/components/ui/SignalIcon';
import { Button } from '@/components/ui/button';

export interface SessionListNavProps {
  onNew: () => void;
  onToggleCollapsed: () => void;
}

export function SessionListNav({
  onNew,
  onToggleCollapsed,
}: SessionListNavProps) {
  return (
    <div className="august-sidebar-nav shrink-0">
      <div className="sidebar-head" data-tauri-drag-region>
        <div className="brand-copy">
          <span className="brand-name text-sm font-semibold tracking-tight text-tier-1">
            August
          </span>
        </div>
        <Button
          variant="bare"
          type="button"
          className="icon-button"
          onClick={onToggleCollapsed}
          title="Collapse sidebar"
          aria-label="Collapse sidebar"
          data-testid="sidebar-collapse-button"
        >
          <SignalIcon name="panel" />
        </Button>
      </div>

      <div className="sd-sidebar-top">
        <Button
          variant="bare"
          type="button"
          className="new-thread"
          onClick={onNew}
          data-testid="sidebar-new-thread"
        >
          <Plus className="size-3.5 text-tier-2" aria-hidden="true" />
          <span>New thread</span>
        </Button>
      </div>
    </div>
  );
}
