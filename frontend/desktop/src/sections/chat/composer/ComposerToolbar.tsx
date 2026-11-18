/* ── Composer toolbar ──────────────────────────────────────────────────── */
/* Two-row composer interior:
 *   Top row:    {children} (auto-growing textarea, attachments, preview)
 *   Bottom bar: LEFT  [+] · [Workspace ▾] · [Access: X ▾]
 *               RIGHT [Model ▾] · [16k ▾] (local only) · [↑ Send / ■ Stop]
 *
 * Wraps both rows so the Ctrl/Cmd-hold chord preview still captures key
 * events bubbling out of the textarea. */

import { useState, useEffect, useRef, type ReactNode } from 'react';
import { ArrowUp, ListEnd, Square } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { WorkbenchSession } from '@/types/workbench';
import type { ModelItem } from '../model-display';
import type { SessionUsageState } from '../hooks/useChatUsage';
import { ComposerActionsMenu } from './ComposerActionsMenu';
import { CameraPopover } from './CameraPopover';
import { SubagentSpawnModal } from './SubagentSpawnModal';
import { CostCeilingChip } from './CostCeilingChip';
import type { AnchorPos } from './useComposerPopovers';
import type { ChatSendFn } from '../hooks/useChatSend';

/** How long the modifier must be held ALONE before the primary control
 *  previews the chord action. */
const CHORD_PREVIEW_HOLD_MS = 150;

export function ComposerToolbar({
  sessionId,
  loadedSessionId,
  input,
  attachmentsCount,
  attachmentsReading = false,
  streaming,
  send,
  stop,
  workbenchSession,
  sessionUsage,
  visibleModels,
  selectedModel,
  actionsOpen,
  actionsPos,
  actionsTriggerRef,
  onToggleActions,
  onAttach,
  onMention,
  onCamera,
  cameraOpen,
  cameraPos,
  onCameraClose,
  onCameraCapture,
  sendKind = 'send',
  onAskParallel,
  onStartDebate,
  leftExtras,
  rightExtras,
  children,
}: {
  sessionId: string | null;
  loadedSessionId: string | null;
  input: string;
  attachmentsCount: number;
  attachmentsReading?: boolean;
  streaming: boolean;
  send: ChatSendFn;
  stop: () => void;
  workbenchSession: WorkbenchSession | null;
  sessionUsage: SessionUsageState;
  visibleModels: ModelItem[];
  selectedModel: ModelItem | null;
  actionsOpen: boolean;
  actionsPos: AnchorPos | null;
  actionsTriggerRef: React.RefObject<HTMLButtonElement | null>;
  onToggleActions: () => void;
  onAttach: () => void;
  onMention: () => void;
  onCamera?: () => void;
  cameraOpen?: boolean;
  cameraPos?: AnchorPos | null;
  onCameraClose?: () => void;
  onCameraCapture?: (file: File) => void;
  sendKind?: 'steer' | 'continue' | 'dispatch' | 'send';
  onAskParallel?: () => void;
  onStartDebate?: () => void;
  /** Left-group pills rendered beside `+` (Workspace pill, Access pill). */
  leftExtras?: ReactNode;
  /** Right-group pills rendered before Send/Stop (Model pill, Context pill). */
  rightExtras?: ReactNode;
  /** Top row content (attachments, steer notice, textarea, preview). */
  children?: ReactNode;
}) {
  const [spawnOpen, setSpawnOpen] = useState(false);
  useEffect(() => {
    const onOpen = () => setSpawnOpen(true);
    window.addEventListener('august:open-spawn', onOpen);
    return () => window.removeEventListener('august:open-spawn', onOpen);
  }, []);

  const [chordPreview, setChordPreview] = useState(false);
  const chordTimerRef = useRef<number | null>(null);
  const clearChordPreview = () => {
    if (chordTimerRef.current != null) {
      window.clearTimeout(chordTimerRef.current);
      chordTimerRef.current = null;
    }
    setChordPreview(false);
  };
  const onComposerKeyDown = (e: React.KeyboardEvent) => {
    if (!streaming) return;
    if (e.key === 'Meta' || e.key === 'Control') {
      if (chordTimerRef.current == null) {
        chordTimerRef.current = window.setTimeout(
          () => setChordPreview(true),
          CHORD_PREVIEW_HOLD_MS,
        );
      }
      return;
    }
    clearChordPreview();
  };

  const canSend =
    !!sessionId &&
    loadedSessionId === sessionId &&
    !!selectedModel &&
    !attachmentsReading &&
    (input.trim().length > 0 || attachmentsCount > 0);

  return (
    <>
      <div
        onKeyDown={onComposerKeyDown}
        onKeyUp={clearChordPreview}
        onBlur={clearChordPreview}
      >
        <div className="min-w-0">{children}</div>

        <div
          className="flex items-center justify-between gap-2 px-3 pb-2.5 pt-1"
          data-testid="composer-bottom-bar"
        >
          <div className="flex min-w-0 items-center gap-1.5">
            <ComposerActionsMenu
              open={actionsOpen}
              pos={actionsPos}
              triggerRef={actionsTriggerRef}
              onToggle={onToggleActions}
              onAttach={onAttach}
              onMention={onMention}
              onCamera={onCamera}
              onSpawn={() => {
                onToggleActions();
                setSpawnOpen(true);
              }}
              onAskParallel={
                onAskParallel
                  ? () => {
                      onToggleActions();
                      onAskParallel();
                    }
                  : undefined
              }
              onStartDebate={
                onStartDebate
                  ? () => {
                      onToggleActions();
                      onStartDebate();
                    }
                  : undefined
              }
              extras={
                <div className="flex flex-wrap items-center gap-2">
                  {sessionUsage && workbenchSession?.id && (
                    <CostCeilingChip
                      sessionId={workbenchSession.id}
                      cost={sessionUsage.totalCost ?? 0}
                      estimated={sessionUsage.costEstimated}
                      initialCeiling={workbenchSession.costCeiling ?? 0}
                    />
                  )}
                </div>
              }
            />
            {onCamera && onCameraCapture && (
              <CameraPopover
                open={!!cameraOpen}
                pos={cameraPos ?? null}
                onClose={() => onCameraClose?.()}
                onCapture={(file) => onCameraCapture(file)}
              />
            )}
            {leftExtras}
          </div>

          <div className="flex min-w-0 shrink-0 items-center gap-1.5">
            {rightExtras}

            {streaming ? (
              chordPreview && canSend ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setChordPreview(false);
                    void send(undefined, { queueAfterStop: true });
                  }}
                  title="Queue the draft — runs after the current response finishes"
                  aria-label="Queue draft after stop"
                  data-testid="composer-chord-queue"
                  className="h-7 shrink-0 rounded-full px-2.5"
                >
                  <ListEnd className="size-3" />
                  <span className="text-2xs font-medium">Queue</span>
                </Button>
              ) : (
                <Button
                  type="button"
                  variant="bare"
                  onClick={stop}
                  title="Stop"
                  aria-label="Stop"
                  data-testid="composer-stop-button"
                  className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--dt-stop-bg,var(--dt-elevated))] text-[var(--dt-primary)] transition-opacity hover:opacity-90"
                >
                  <Square className="size-3 fill-current" />
                </Button>
              )
            ) : (
              <Button
                type="button"
                variant="bare"
                onClick={() => {
                  void send();
                }}
                disabled={!canSend}
                title={
                  sendKind === 'steer'
                    ? 'Steer this worker on its next round'
                    : sendKind === 'continue'
                      ? 'Continue the named workstream'
                      : sendKind === 'dispatch'
                        ? 'Send to the orchestrator (it will spawn workers)'
                        : canSend
                          ? 'Send (Enter)'
                          : !selectedModel
                            ? 'No model available — add a provider in Settings → Model settings'
                            : !sessionId
                              ? 'Open a chat session first'
                              : 'Type a message to send'
                }
                aria-label={
                  sendKind === 'steer'
                    ? 'Steer'
                    : sendKind === 'continue'
                      ? 'Continue'
                      : sendKind === 'dispatch'
                        ? 'Dispatch'
                        : 'Send'
                }
                data-testid="composer-send-button"
                className={cn(
                  'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--dt-primary)] text-[var(--dt-primary-foreground)] transition-opacity hover:opacity-90 disabled:pointer-events-none disabled:opacity-30',
                )}
              >
                <ArrowUp className="size-3.5" />
              </Button>
            )}
          </div>
        </div>
      </div>

      <SubagentSpawnModal
        sessionId={workbenchSession?.id}
        open={spawnOpen}
        onClose={() => setSpawnOpen(false)}
        models={visibleModels}
      />
    </>
  );
}
