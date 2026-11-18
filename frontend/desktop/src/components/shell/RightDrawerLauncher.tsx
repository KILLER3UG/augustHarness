/* ── RightDrawerLauncher ─ Workbench panel trigger ────────────────── */
/* Clicking the panel icon opens the drawer straight into the ZCode-style */
/* "Open tab" card-grid chooser (no dropdown list).                        */

import { PanelRight, PanelRightClose } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import {
  closeRightDrawer,
  openRightDrawerChooser,
  useRightDrawer,
} from './RightDrawerState';

export function RightDrawerDropdown({
  drawerOpen,
  workersBadge = 0,
  artifactsCount = 0,
}: {
  drawerOpen: boolean;
  workersBadge?: number;
  artifactsCount?: number;
}) {
  const state = useRightDrawer();

  return (
    <Button
      variant="bare"
      type="button"
      size="icon-sm"
      onClick={() => {
        if (state.open && state.chooserActive) {
          closeRightDrawer();
          return;
        }
        openRightDrawerChooser();
      }}
      className={cn(
        'relative size-7 shrink-0 rounded-md transition-colors',
        drawerOpen
          ? 'bg-elevated text-tier-1'
          : 'text-tier-3 hover:bg-accent/60 hover:text-tier-1',
      )}
      title={
        artifactsCount > 0
          ? `${artifactsCount} artifacts available · ${drawerOpen ? 'Workbench sections' : 'Open Workbench'}`
          : drawerOpen
            ? 'Workbench sections'
            : 'Open Workbench'
      }
      aria-label={
        artifactsCount > 0
          ? `Open Workbench, ${artifactsCount} artifacts available`
          : drawerOpen
            ? 'Workbench sections'
            : 'Open Workbench'
      }
      data-testid="workbench-launcher"
    >
      {drawerOpen ? (
        <PanelRightClose className="size-3.5" />
      ) : (
        <PanelRight className="size-3.5" />
      )}
      {workersBadge > 0 ? (
        <span className="absolute -right-0.5 -top-0.5 min-w-3.5 rounded-full bg-warning px-1 text-2xs font-semibold leading-4 text-warning-foreground">
          {workersBadge > 9 ? '9+' : workersBadge}
        </span>
      ) : null}
      {artifactsCount > 0 ? (
        <span
          className="absolute bottom-1 right-1 size-1.5 rounded-full bg-primary"
          aria-hidden="true"
          data-testid="workbench-artifacts-indicator"
        />
      ) : null}
    </Button>
  );
}
