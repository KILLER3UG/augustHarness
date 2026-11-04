/* ── Composer toolbar ──────────────────────────────────────────────────── */
/* The composer's ONE control row: the `+` at the left, the message input in
 * the middle, the single primary action (send / stop) at the right.
 *
 * It used to be a second row UNDER the textarea, so the card held two rows
 * of controls where the reference holds one. The capture is explicit about
 * the reference's shape: "Inside: a `+` glyph at the left, placeholder
 * `Write a message…` … and at the right a voice glyph and a small chevron.
 * When generating, the right-hand control becomes a filled light circle with
 * a dark rounded square inside (the stop button)." One row, one filled
 * control, everything else a borderless glyph.
 *
 * The model + effort control is gone from this toolbar and from the composer
 * below the card; the prop list shrank with it. `selectedModel` and
 * `visibleModels` stay because the send gate and the subagent modal still
 * need them. */

import { useState, useEffect, type ReactNode } from 'react';
import { ArrowUp, Mic, Square } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { WorkbenchSession } from '@/types/workbench';
import type { ModelItem } from '../model-display';
import type { SessionUsageState } from '../hooks/useChatUsage';
import { ComposerActionsMenu } from './ComposerActionsMenu';
import { CameraPopover } from './CameraPopover';
import { SubagentSpawnModal } from './SubagentSpawnModal';
import { CostCeilingChip } from './CostCeilingChip';
import type { AnchorPos } from './useComposerPopovers';
import type { ChatSendFn } from '../hooks/useChatSend';

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
  onVoice,
  onCamera,
  cameraOpen,
  cameraPos,
  onCameraClose,
  onCameraCapture,
  sendKind = 'send',
  onAskParallel,
  onStartDebate,
  children,
}: {
  sessionId: string | null;
  loadedSessionId: string | null;
  input: string;
  attachmentsCount: number;
  /** Disable send while files are still being read. */
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
  onVoice: () => void;
  /** Open the camera capture popover. When omitted, no camera item is shown. */
  onCamera?: () => void;
  /** True while the camera popover is open. */
  cameraOpen?: boolean;
  /** Anchor position for the camera popover (re-uses the actions menu slot). */
  cameraPos?: AnchorPos | null;
  /** Close the camera popover. */
  onCameraClose?: () => void;
  /** Forward a captured webcam frame to the composer attachment pipeline. */
  onCameraCapture?: (file: File) => void;
  sendKind?: 'steer' | 'continue' | 'dispatch' | 'send';
  onAskParallel?: () => void;
  onStartDebate?: () => void;
  /** The message input. It is rendered INSIDE this row — between the `+` and
   *  the primary action — because the reference's composer card is one row,
   *  not an input with a control strip bolted under it. */
  children?: ReactNode;
}) {
  const [spawnOpen, setSpawnOpen] = useState(false);
  useEffect(() => {
    const onOpen = () => setSpawnOpen(true);
    window.addEventListener('august:open-spawn', onOpen);
    return () => window.removeEventListener('august:open-spawn', onOpen);
  }, []);

  const canSend =
    !!sessionId &&
    loadedSessionId === sessionId &&
    !!selectedModel &&
    !attachmentsReading &&
    (input.trim().length > 0 || attachmentsCount > 0);

  return (
    <>
      {/* THE composer row. Three zones, in the reference's order: the `+`
          glyph at the left, the placeholder, the one filled control at the
          right. `items-end` keeps the send circle on the BOTTOM-right as the
          input grows, the way a growing composer card does. */}
      <div className="composer-tools flex items-end gap-1 px-2 py-1">
        {/* LEFT — the reference's only left-hand affordance is the `+`; the
            folder chip is August's own and only rides along while the
            folder binding is still open (a fresh chat). */}
        <div className="flex min-w-0 shrink-0 items-center gap-1">
          {/* REMOVED: the `⌘` "Browse slash commands" trigger.
              The reference composer has no such control — its only left-hand
              affordance is the `+` beside the placeholder. Removed as the button
              itself plus the `onCommands` prop and its call site, not hidden with
              a class, so there is no dead element left in the DOM.

              NOT deleted: `ComposerCommandsDropdown` and the
              `showCommandsDropdown` state it is driven by. This button was its
              ONLY entry point (`ChatThread.tsx:415` exposes the setter on a ref,
              but nothing calls that ref method), and the ⌘K Command Palette does
              not list slash commands. Removing the dropdown as well would delete
              the feature rather than the icon, which is a different decision and
              the user's to make. */}
          <ComposerActionsMenu
            open={actionsOpen}
            pos={actionsPos}
            triggerRef={actionsTriggerRef}
            onToggle={onToggleActions}
            onAttach={onAttach}
            onMention={onMention}
            onVoice={onVoice}
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
        </div>

        {/* MIDDLE — the input. It owns the row's height (a 36 px minimum,
            auto-growing), which is what the card's one row is sized by. */}
        <div className="min-w-0 flex-1">{children}</div>

        {/* RIGHT — the ONE primary action. Neutral and circular, measured off
            the Claude reference: "a circular button, ~32 px, containing an
            up-arrow glyph — a circle, not the blue rounded square August
            uses", and the stop button that replaces it while generating is a
            "filled light circle with a dark rounded square inside". */}
        <div className="flex shrink-0 items-center gap-1">
          {/* The reference's right-hand cluster is a voice glyph beside the
              send circle. August had that glyph AND a chevron here, both wired
              to the SAME `+` menu — two controls advertising two menus, one
              menu. The mic stays because voice input was otherwise buried two
              levels down inside that menu; the chevron goes, because a second
              trigger for one popover is decoration. */}
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={onVoice}
            title="Voice input"
            aria-label="Voice input"
            data-testid="composer-voice-button"
            className="tool-button"
          >
            <Mic aria-hidden />
          </Button>
          {streaming ? (
            <button
              type="button"
              onClick={stop}
              title="Stop"
              aria-label="Stop"
              className="send-button stop size-8 shrink-0"
            >
              <Square className="size-3 fill-current" />
            </button>
          ) : (
            <button
              type="button"
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
              className="send-button size-8 shrink-0 disabled:opacity-30 disabled:pointer-events-none"
            >
              <ArrowUp className="size-3" />
            </button>
          )}
        </div>
      </div>

      {/* The model+effort control lived here once, then below the card, and is now
         * gone entirely by request. This slot stays empty on purpose: a third
         * row inside the card is what made the composer read as a control panel
         * rather than a text field, and nothing may claim it back. */}
      <SubagentSpawnModal
        sessionId={workbenchSession?.id}
        open={spawnOpen}
        onClose={() => setSpawnOpen(false)}
        models={visibleModels}
      />
    </>
  );
}
