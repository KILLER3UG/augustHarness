/**
 * Derive the files a turn "touched" from its tool-call blocks — the
 * row source for the unified ChangesCard. Pure so it is
 * unit-testable.
 *
 * A file counts when an edit-classified tool call carries a file path in
 * its args. Internal `.aug/` bookkeeping paths are excluded.
 */

import { isAugInternalPath, producedFilePath } from '@/lib/produced-artifacts';
import { isFigurePath } from '@/lib/file-kind';
import type { GitStatus } from '@/api/git';
import type { MessageBlock } from '@/types/chat';

/** Unique, de-duplicated, display-ready paths in first-seen order.
 *
 *  The rule for "a file a tool wrote" is ONE implementation, in
 *  lib/produced-artifacts.ts (`producedFilePath`): an EDIT-classified
 *  call, an office deliverable (`pptx_*`) or `create_html_artifact`,
 *  carrying a path. This file used to hold its own copy of that rule and
 *  the artifact collector held a third; they agreed by luck, and a
 *  disagreement would have shown the same file in two places at once. */
export function collectProducedFiles(blocks?: MessageBlock[] | null): string[] {
  if (!blocks) return [];
  const seen = new Set<string>();
  const files: string[] = [];
  for (const block of blocks) {
    if (block.type !== 'toolCall' || !block.tool) continue;
    const path = producedFilePath(block.tool.name || '', block.tool.context);
    if (!path) continue;
    const key = path.replace(/\\/g, '/').replace(/\/+$/, '');
    if (seen.has(key)) continue;
    seen.add(key);
    files.push(path);
  }
  return files;
}

/** Short display label for a path (basename; falls back to the path). */
export function producedFileLabel(path: string): string {
  const cleaned = path.replace(/\\/g, '/').split('/').filter(Boolean).pop();
  return cleaned || path;
}

/**
 * The FIGURE files a workspace's git status says it is dirty with, absolute.
 *
 * WHY GIT: a figure the model made by RUNNING something — the anatomy
 * workflow's `node references/<name>/build.mjs`, which writes
 * `dial-indicator.html` with `writeFileSync` — leaves the two artifact
 * collectors nothing to read. The argument side refuses it because
 * `run_command` is bucket `run` (tool-classify), and the result side refuses
 * it because a command receipt carries its output (`CONTENT_KEYS`). The
 * harness does not track shell writes either: `_recordWorldDelta`
 * (backend `loop/guards.py`) collects paths from a call's ARGUMENTS, and
 * `node build.mjs` names no output path there; the shadow-git step snapshot
 * only runs when `mutationCount` moved, which only edit-class tools bump.
 * The record that does exist is the workspace's own `git status`, which
 * reports `??` untracked and `M` modified paths and returns the workspace
 * root, so the paths can be made absolute for the drawer's read door.
 *
 * THE LIMITS, STATED: it is git's view, so (a) a workspace that is not a git
 * work tree reports nothing — `app/routers/git.py` `_ensure_repo` refuses
 * rather than initializing one; (b) a path the repo IGNORES is invisible to
 * `status --porcelain`; (c) git collapses a wholly-new directory into one
 * entry (`figs/`), so a figure written into a folder the turn created is
 * named by the folder and skipped here, not opened; (d) porcelain v1 prints
 * paths relative to the REPOSITORY root, so the join to `status.workspace`
 * is only right when the session's workspace IS that root — a session bound
 * to a subdirectory of a larger repo gets a doubled path, and the drawer's
 * open door reports the failure rather than showing the wrong file. (The
 * same assumption is already baked into every consumer of `changedFiles`,
 * which passes git's paths to `openFileInDrawer` unchanged.)
 *
 * Only figure kinds come back (see `isFigurePath`): an `.html`/`.svg`/`.png`
 * is a thing to LOOK at, and this feeds the drawer, so a `.md` or a `.csv` in
 * the same status listing must not steal the panel.
 */
export function figurePathsFromWorkspaceStatus(status?: GitStatus | null): string[] {
  const root = (status?.workspace || '').replace(/[\\/]+$/, '');
  const files = status?.files ?? [];
  if (!root || files.length === 0) return [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const file of files) {
    const rel = String(file.path || '').replace(/\\/g, '/').replace(/^\/+/, '');
    if (!rel || rel.endsWith('/')) continue;
    const abs = `${root}/${rel}`;
    if (isAugInternalPath(abs) || !isFigurePath(abs)) continue;
    const key = abs.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(abs);
  }
  return out;
}

/** A page outranks the captures of it. */
function figureRank(path: string): number {
  const ext = path.split('.').pop()?.toLowerCase() ?? '';
  if (ext === 'html' || ext === 'htm') return 2;
  if (ext === 'svg') return 1;
  return 0;
}

/**
 * The ONE figure to show when a command wrote several.
 *
 * The anatomy workflow's build emits the deliverable AND its verification
 * captures — "Deliver the page, the build script, and the verification
 * captures beside them". Flipping the panel through four files as fast as
 * they read is not showing the work; the page is. A command that writes only
 * a PNG still gets its PNG.
 */
export function figureToShowFromPaths(paths: readonly string[]): string {
  const ranked = [...paths].sort((a, b) => figureRank(b) - figureRank(a));
  return ranked[0] ?? '';
}
