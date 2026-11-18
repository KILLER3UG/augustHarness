/* ── ComposerWorkspaceChips — compatibility alias for WorkspacePill ─────── */
/* Kept so any external caller importing ComposerWorkspaceChips continues to
 * work; the primary pill lives in WorkspacePill.tsx. */

import { WorkspacePill, type WorkspacePillProps } from './WorkspacePill';

export type ComposerWorkspaceChipsProps = WorkspacePillProps;

export function ComposerWorkspaceChips(props: ComposerWorkspaceChipsProps) {
  return <WorkspacePill {...props} />;
}
