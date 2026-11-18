/* ── ChatThreadComposer ───────────────────────────────────────────────── */
/* Floating pill message box: attachments, @skills/tools, /commands,       */
/* model/effort menu, send / mid-run steer, stop.                          */

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { WorkbenchSession } from '@/types/workbench';
import type { ChatMessage, FileAttachment } from '@/types/chat';
import type { WorkbenchGuardMode } from '@/components/chat/WorkbenchModeSelector';
import {
  normalizeSandboxMode,
  type WorkbenchSandboxMode,
} from '@/components/chat/SandboxModeSelector';
import {
  setWorkbenchGuardMode,
  setWorkbenchSandboxMode,
  setWorkbenchAgentMode,
} from '@/api/workbench';
import { QueuePills } from './QueuePills';
import { StreamLinkBanner } from './StreamLinkBanner';
import { ArenaLaunchModal } from './composer/ArenaLaunchModal';
import { DebateLaunchModal } from './debate/DebateLaunchModal';
import type { DebateRun } from './debate/debate-store';
import { WifiOff } from 'lucide-react';
import type { QueuedUserMessage } from './queue-store';
import { Markdown } from './ChatMarkdown';
import { ScrollToBottomButton } from '@/components/chat/ScrollToBottomButton';
import type { ModelItem } from './model-display';
import type { SessionUsageState } from './hooks/useChatUsage';
import type { EffortLevel } from './hooks/useChatSend';
import { ModelEffortMenu } from './composer/ModelEffortMenu';
import { WorkspacePill } from './composer/WorkspacePill';
import { AccessPill } from './composer/AccessPill';
import { ContextLengthMenu } from './composer/ContextLengthMenu';
import {
  useComposerPopovers,
  type ComposerDropdownApi,
} from './composer/useComposerPopovers';
import { ComposerAttachmentChips } from './composer/ComposerAttachmentChips';
import { ComposerMentionsDropdown } from './composer/ComposerMentionsDropdown';
import { ComposerCommandsDropdown } from './composer/ComposerCommandsDropdown';
import { ComposerToolbar } from './composer/ComposerToolbar';
import { FontChooserToast } from './composer/FontChooserToast';
import { toast } from 'sonner';
import { useQuery } from '@tanstack/react-query';
import { useFocusedSubagent } from '@/components/chat/focused-subagent';
import { setContinueWorkstream, useContinueWorkstream } from '@/components/chat/composer-intent';
import { normalizeHarnessMode, type HarnessAgentMode } from '@/components/chat/HarnessModeChip';
import { continueWorkstream, listWorkstreams, runRoutine, steer as steerSubagent } from '@/api/subagents';
import type { ChatSendFn, ChatSendOptions } from './hooks/useChatSend';

export type { ComposerDropdownApi };

export interface ChatThreadComposerProps {
  sessionId: string | null;
  loadedSessionId: string | null;
  input: string;
  setInput: Dispatch<SetStateAction<string>>;
  attachments: FileAttachment[];
  /** True while any attachment is still being read/extracted. */
  attachmentsReading?: boolean;
  /** Count of ready attachments (excludes in-progress / failed). */
  readyAttachmentsCount?: number;
  removeAttachment: (index: number) => void;
  handleComposerPaste: (e: React.ClipboardEvent<HTMLTextAreaElement>) => void;
  handleFileUpload: (e: React.ChangeEvent<HTMLInputElement>) => void | Promise<void>;
  /** Push a fresh File (e.g. camera capture) into the attachment pipeline. */
  attachFiles?: (files: FileList | File[]) => Promise<void> | void;
  messages: ChatMessage[];
  streaming: boolean;
  send: ChatSendFn;
  stop: () => void;
  /** Optional: lets the composer toolbar append a synthetic handoff-notice card. */
  /** Creates the backend workbench session on demand (before the first
   *  send on a fresh chat). */
  ensureWorkbenchSession: () => Promise<WorkbenchSession | null>;
  queuedMessages: QueuedUserMessage[];
  workbenchSession: WorkbenchSession | null;
  setWorkbenchSession: (
    session:
      | WorkbenchSession
      | null
      | ((prev: WorkbenchSession | null) => WorkbenchSession | null),
  ) => void;
  workbenchMode: WorkbenchGuardMode;
  setWorkbenchMode: Dispatch<SetStateAction<WorkbenchGuardMode>>;
  workspacePath?: string | null;
  activeWorkbenchSessionId?: string | null;
  /** Context ring inputs */
  sessionUsage: SessionUsageState;
  visibleModels: ModelItem[];
  selectedModel: ModelItem | null;
  /** The muted word after the model name on the footer line. The reference
   *  shows `Sonnet 5.5  High` there — effort in muted, no chip — but it stays
   *  changeable through the effort pane (`onEffortChange`); an earlier round
   *  of the redesign made this read-only and the reference re-read undid it. */
  effort: EffortLevel;
  onSelectModel: (m: ModelItem) => void;
  onEffortChange: (v: EffortLevel) => void;
  /** Opens the Edit Models (visibility) modal in ChatThread. Without it the
   *  model pane's "Manage models" footer never renders and a hidden model
   *  cannot be un-hidden — the hide action would be irreversible. */
  onEditModels?: () => void;
  thinkingEnabled: boolean;
  onThinkingChange: (v: boolean) => void;
  /** Arena ("ask in parallel"): run the prompt on 2–3 models in forks. */
  onArenaLaunch?: (targets: ModelItem[], prompt: string) => void;
  /** Structured debate (A5): two models argue in this chat, optional judge. */
  onDebateLaunch?: (
    a: DebateRun['models'][number],
    b: DebateRun['models'][number],
    judge: DebateRun['models'][number] | null,
    rounds: number,
    prompt: string,
  ) => void;
  /** Offline compose (C9): queued messages waiting for the backend. */
  offlineCount?: number;
  onFlushOffline?: () => void;
  /** Jump-to-bottom affordance anchored to the composer card's top border. */
  scrolledFromBottom: boolean;
  showNewContentPill: boolean;
  onScrollToBottom: () => void;
  /** Optional: parent registers send-path popover closers. */
  dropdownApiRef?: MutableRefObject<ComposerDropdownApi | null>;
}

/**
 * Bottom (or empty-state) message composer: textarea, popovers, toolbar.
 */
export function ChatThreadComposer(props: ChatThreadComposerProps) {
  const {
    sessionId,
    loadedSessionId,
    input,
    setInput,
    attachments,
    attachmentsReading = false,
    readyAttachmentsCount,
    removeAttachment,
    handleComposerPaste,
    handleFileUpload,
    attachFiles,
    streaming,
    send,
    stop,
    ensureWorkbenchSession,
    queuedMessages,
    workbenchSession,
    setWorkbenchSession,
    workbenchMode,
    setWorkbenchMode,
    workspacePath,
    activeWorkbenchSessionId,
    sessionUsage,
    visibleModels,
    selectedModel,
    effort,
    onSelectModel,
    onEffortChange,
    onEditModels,
    thinkingEnabled,
    onThinkingChange,
    onArenaLaunch,
    onDebateLaunch,
    offlineCount = 0,
    onFlushOffline,
    scrolledFromBottom,
    showNewContentPill,
    onScrollToBottom,
    dropdownApiRef,
  } = props;

  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const focusedSubagent = useFocusedSubagent();
  const continueName = useContinueWorkstream();
  const continueHint = useQuery({
    queryKey: ['workstreams', workbenchSession?.id],
    queryFn: () => listWorkstreams(workbenchSession!.id),
    enabled: !!workbenchSession?.id && !!continueName,
  });
  const ghostNext =
    continueHint.data?.find((w) => w.name === continueName)?.latest?.next?.trim() || '';
  const harnessMode = normalizeHarnessMode(workbenchSession?.agentMode);
  const sendKind =
    focusedSubagent ? 'steer' : continueName ? 'continue' : harnessMode === 'orchestrator' ? 'dispatch' : 'send';
  const sendOrSteer = useCallback<ChatSendFn>(
    async (textOverride?: string, options?: ChatSendOptions) => {
      const text = (textOverride ?? input).trim();
      const laneAt = text.match(/^@lane:([^\s]+)\s*([\s\S]*)$/);
      if (laneAt && workbenchSession?.id) {
        try {
          await continueWorkstream(
            workbenchSession.id,
            laneAt[1],
            laneAt[2].trim() || ghostNext || 'Continue from the last episode.',
          );
          toast.success(`Continuing ${laneAt[1]}`);
          setInput('');
        } catch (e) {
          toast.error(e instanceof Error ? e.message : 'Continue failed');
          return 'skipped';
        }
        return 'sent';
      }
      const routineAt = text.match(/^@routine:([^\s]+)\s*/);
      if (routineAt && workbenchSession?.id) {
        try {
          await runRoutine(workbenchSession.id, routineAt[1]);
          toast.success('Routine running');
          setInput('');
        } catch (e) {
          toast.error(e instanceof Error ? e.message : 'Routine failed');
          return 'skipped';
        }
        return 'sent';
      }
      if (focusedSubagent && text) {
        const running = focusedSubagent.running !== false;
        try {
          if (running) {
            await steerSubagent(focusedSubagent.jobId, text);
            toast.success(`Steering ${focusedSubagent.title} (next round)`);
          } else if (focusedSubagent.workstream && workbenchSession?.id) {
            await continueWorkstream(workbenchSession.id, focusedSubagent.workstream, text);
            toast.success(`Continuing ${focusedSubagent.workstream}`);
            setContinueWorkstream(null);
          } else {
            await steerSubagent(focusedSubagent.jobId, text);
            toast.success(`Queued for ${focusedSubagent.title}`);
          }
          setInput('');
        } catch (e) {
          const msg = e instanceof Error ? e.message : 'Steer failed';
          if (focusedSubagent.workstream && workbenchSession?.id) {
            try {
              await continueWorkstream(workbenchSession.id, focusedSubagent.workstream, text);
              toast.success(`Worker idle — continued ${focusedSubagent.workstream}`);
              setInput('');
              setContinueWorkstream(null);
              return 'sent';
            } catch {
              /* fall through */
            }
          }
          toast.error(msg);
          return 'skipped';
        }
        return 'sent';
      }
      if (continueName && workbenchSession?.id) {
        try {
          await continueWorkstream(
            workbenchSession.id,
            continueName,
            text || ghostNext || 'Continue from the last episode.',
          );
          toast.success(`Continuing ${continueName}`);
          setInput('');
          setContinueWorkstream(null);
        } catch (e) {
          toast.error(e instanceof Error ? e.message : 'Continue failed');
          return 'skipped';
        }
        return 'sent';
      }
      return send(textOverride, options);
    },
    [focusedSubagent, continueName, ghostNext, input, send, setInput, workbenchSession?.id],
  );
  // Live markdown preview is opt-in — Ctrl/Cmd+Shift+P toggles it.
  const [showPreview, setShowPreview] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const cmd = e.metaKey || e.ctrlKey;
      if (cmd && e.shiftKey && !e.altKey && e.key === 'P') {
        e.preventDefault();
        setShowPreview(v => !v);
      }
      // Quick Entry — Cmd/Ctrl+Shift+Space focuses composer from anywhere
      if (cmd && e.shiftKey && (e.code === 'Space' || e.key === ' ')) {
        e.preventDefault();
        taRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const popovers = useComposerPopovers({
    input,
    setInput,
    taRef,
    dropdownApiRef,
    send: sendOrSteer,
    stop,
    streaming,
    sessionId,
    workbenchSessionId: workbenchSession?.id,
  });

  // Camera capture popover — anchors over the composer actions menu slot.
  const [cameraOpen, setCameraOpen] = useState(false);
  const handleCameraCapture = useCallback(
    (file: File) => {
      if (!attachFiles) return;
      void attachFiles([file]);
      setCameraOpen(false);
    },
    [attachFiles],
  );

  /* The command palette's "Switch model…" bumped a counter that opened
   * `ModelEffortMenu`, which only existed in the removed model row. Both the
   * counter and the palette item are gone with it — leaving either would be a
   * command that dispatches an action nothing listens for.
   *
   * `handoffPreparing` went with the rest: the row was the only caller of its
   * setter, so the toolbar's "Preparing handoff…" notice could never render.
   * That is a dead branch, not a temporarily-disabled feature. */

  // Arena ("ask in parallel") modal state.
  const [arenaOpen, setArenaOpen] = useState(false);
  // Debate (A5) modal state.
  const [debateOpen, setDebateOpen] = useState(false);

  // Value-driven auto-grow so clearing input after send collapses height
  // (onChange alone never fires for controlled setInput('')).
  // This cap and the textarea's inline max-height must agree: the height set
  // here is clamped by max-height while the overflow test reads the raw
  // scrollHeight, so a cap above max-height silently hides every line
  // between the two (no scrollbar — overflow stays `hidden`).
  const MAX_COMPOSER_H = 240;
  const resizeTextarea = useCallback(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const next = Math.min(el.scrollHeight, MAX_COMPOSER_H);
    el.style.height = `${next}px`;
    el.style.overflowY = el.scrollHeight > MAX_COMPOSER_H ? 'auto' : 'hidden';
  }, []);

  useLayoutEffect(() => {
    resizeTextarea();
  }, [input, resizeTextarea]);

  const [sandboxModeLocal, setSandboxModeLocal] = useState<WorkbenchSandboxMode>(() => {
    try {
      return normalizeSandboxMode(localStorage.getItem('august_last_sandbox_mode'));
    } catch {
      return 'workspace-write';
    }
  });
  const sandboxMode = workbenchSession?.sandboxMode
    ? normalizeSandboxMode(workbenchSession.sandboxMode)
    : sandboxModeLocal;

  const applyAccessChange = (next: {
    guard?: WorkbenchGuardMode;
    sandbox?: WorkbenchSandboxMode;
  }) => {
    const wbId = workbenchSession?.id;
    let network: boolean | undefined;
    if (next.sandbox) {
      setSandboxModeLocal(next.sandbox);
      localStorage.setItem('august_last_sandbox_mode', next.sandbox);
      try {
        if (
          next.sandbox === 'workspace-write' &&
          localStorage.getItem('august_sandbox_network_default') === '1'
        ) {
          network = true;
        }
      } catch {
        /* ignore */
      }
    }
    if (next.guard) {
      setWorkbenchMode(next.guard);
      localStorage.setItem('august_last_workbench_guard_mode', next.guard);
    }
    setWorkbenchSession((prev) => {
      if (!prev) return prev;
      let updated = prev;
      if (next.guard) {
        updated =
          next.guard === 'full'
            ? {
                ...updated,
                plan: null,
                approved: false,
                approvedAt: null,
                guardMode: 'full',
                agentId: 'build',
              }
            : {
                ...updated,
                guardMode: next.guard,
                agentId: next.guard === 'plan' ? 'plan' : 'build',
              };
      }
      if (next.sandbox) {
        updated = {
          ...updated,
          sandboxMode: next.sandbox,
          sandboxNetwork:
            next.sandbox === 'danger-full-access'
              ? true
              : network ?? updated.sandboxNetwork,
        };
      }
      return updated;
    });
    if (wbId) {
      const sync = async () => {
        let latest: WorkbenchSession | null = null;
        if (next.guard) {
          latest = await setWorkbenchGuardMode(wbId, next.guard);
        }
        if (next.sandbox) {
          latest = await setWorkbenchSandboxMode(wbId, next.sandbox, network);
        }
        if (latest) setWorkbenchSession(latest);
      };
      void sync().catch((error) => {
        console.warn('[ChatThread] Failed to persist access mode:', error);
      });
    }
  };

  const handleGuardModeChange = (mode: WorkbenchGuardMode) => {
    applyAccessChange({ guard: mode });
  };

  const persistHarnessMode = (next: HarnessAgentMode) => {
    const apply = (sid: string) => {
      setWorkbenchSession((prev) => (prev ? { ...prev, agentMode: next } : prev));
      void setWorkbenchAgentMode(sid, next)
        .then((updated) => {
          if (updated) setWorkbenchSession(updated);
        })
        .catch((error) => {
          console.warn('[ChatThread] Failed to persist agent mode:', error);
        });
    };
    if (!workbenchSession?.id) {
      void ensureWorkbenchSession()
        .then((session) => {
          if (session?.id) apply(session.id);
        })
        .catch((error) => {
          console.warn('[ChatThread] Failed to create session for harness mode:', error);
        });
      return;
    }
    apply(workbenchSession.id);
  };

  const handleSandboxChange = (mode: WorkbenchSandboxMode) => {
    applyAccessChange({ sandbox: mode });
  };

  return (
    <div
      className="august-composer-shell relative mx-auto w-full max-w-[47.5rem] pb-3"
      ref={popovers.composerRootRef}
    >
      <input
        type="file"
        ref={fileInputRef}
        onChange={(e) => {
          void handleFileUpload(e);
        }}
        multiple
        className="hidden"
      />

      <ComposerMentionsDropdown
        open={popovers.showToolsDropdown || popovers.showMentionsDropdown}
        pos={popovers.toolsPos}
        mentionQuery={popovers.mentionQuery}
        mentionItems={popovers.mentionItems}
        skillMentions={popovers.skillMentions}
        skillsError={popovers.skillsError}
        conversationMentions={popovers.conversationMentions}
        skillsLoading={popovers.skillsLoading}
        highlightedMentionIndex={popovers.highlightedMentionIndex}
        onPick={popovers.insertMention}
        onInsertToolText={(text) => {
          popovers.insertText(text);
          popovers.setShowToolsDropdown(false);
        }}
      />

      <ComposerCommandsDropdown
        open={popovers.showCommandsDropdown}
        pos={popovers.commandsPos}
        input={input}
        highlightedCommandIndex={popovers.highlightedCommandIndex}
        onPick={(name) => {
          popovers.insertCommand(name);
          popovers.setShowCommandsDropdown(false);
        }}
      />

      {/* SSE link dropped while the backend is otherwise fine. When messages
          are parked offline, the offline banner already explains the outage. */}
      {offlineCount === 0 ? (
        <StreamLinkBanner
          sessionId={workbenchSession?.id || activeWorkbenchSessionId || sessionId}
        />
      ) : null}

      {/* Offline compose banner (C9) */}
      {offlineCount > 0 ? (
        <div
          className="mb-1.5 flex items-center gap-2 rounded-md border border-warning/40 bg-warning/10 px-2.5 py-1.5 text-2xs text-warning"
          data-testid="offline-banner"
        >
          <WifiOff className="size-3 shrink-0" />
          <span className="flex-1 min-w-0">
            Offline — {offlineCount} message{offlineCount === 1 ? '' : 's'} queued; sending when
            the backend returns.
          </span>
          <Button
            type="button"
            variant="bare"
            onClick={onFlushOffline}
            className="rounded bg-warning/20 px-2 py-0.5 hover:bg-warning/30"
            data-testid="offline-flush-now"
          >
            Send now
          </Button>
        </div>
      ) : null}

      {queuedMessages.length > 0 && sessionId && (
        <QueuePills
          sessionId={sessionId}
          workbenchSessionId={
            workbenchSession?.id || activeWorkbenchSessionId || sessionId
          }
          items={queuedMessages}
        />
      )}

      <FontChooserToast />

      <div
        className={cn(
          'august-composer relative w-full min-w-0 overflow-visible rounded-[14px] border border-[var(--dt-line)] bg-[var(--dt-card)] transition-colors focus-within:border-[var(--dt-line-strong)]',
        )}
      >
        <div className="pointer-events-none absolute inset-x-0 -top-3 z-20 flex justify-center">
          <ScrollToBottomButton
            visible={scrolledFromBottom}
            onClick={onScrollToBottom}
            showNewContentPill={showNewContentPill}
          />
        </div>

        <ComposerToolbar
          sessionId={sessionId}
          loadedSessionId={loadedSessionId}
          input={input}
          attachmentsCount={readyAttachmentsCount ?? attachments.length}
          attachmentsReading={attachmentsReading}
          streaming={streaming}
          send={sendOrSteer}
          stop={stop}
          sendKind={streaming ? 'steer' : sendKind}
          workbenchSession={workbenchSession}
          sessionUsage={sessionUsage}
          visibleModels={visibleModels}
          selectedModel={selectedModel}
          actionsOpen={popovers.showComposerActionsDropdown}
          actionsPos={popovers.composerActionsPos}
          actionsTriggerRef={popovers.composerActionsTriggerRef}
          onToggleActions={() => {
            popovers.setPillPopoverOpen(
              'actions',
              popovers.activePopover !== 'actions',
            );
          }}
          onAttach={() => {
            fileInputRef.current?.click();
            popovers.setShowComposerActionsDropdown(false);
          }}
          onMention={popovers.openMentionPicker}
          onCamera={
            attachFiles
              ? () => {
                  popovers.setShowComposerActionsDropdown(false);
                  setCameraOpen((value) => !value);
                }
              : undefined
          }
          cameraOpen={cameraOpen}
          cameraPos={popovers.composerActionsPos}
          onCameraClose={() => setCameraOpen(false)}
          onCameraCapture={handleCameraCapture}
          onAskParallel={() => setArenaOpen(true)}
          onStartDebate={() => setDebateOpen(true)}
          leftExtras={
            <>
              <WorkspacePill
                sessionId={sessionId}
                workbenchSessionId={workbenchSession?.id || activeWorkbenchSessionId || null}
                workspacePath={workspacePath}
                open={popovers.activePopover === 'workspace'}
                onOpenChange={(next) => popovers.setPillPopoverOpen('workspace', next)}
              />
              <AccessPill
                guardMode={workbenchMode}
                onGuardChange={handleGuardModeChange}
                sandboxMode={sandboxMode}
                onSandboxChange={handleSandboxChange}
                onAccessChange={applyAccessChange}
                harnessMode={harnessMode}
                onHarnessChange={persistHarnessMode}
                open={popovers.activePopover === 'access'}
                onOpenChange={(next) => popovers.setPillPopoverOpen('access', next)}
              />
            </>
          }
          rightExtras={
            <>
              <ModelEffortMenu
                visibleModels={visibleModels}
                selected={selectedModel}
                onSelect={onSelectModel}
                onEditModels={onEditModels}
                effort={effort}
                onEffortChange={onEffortChange}
                thinkingEnabled={thinkingEnabled}
                onThinkingChange={onThinkingChange}
                open={popovers.activePopover === 'model'}
                onOpenChange={(next) => popovers.setPillPopoverOpen('model', next)}
              />
              <ContextLengthMenu
                selectedModel={selectedModel}
                open={popovers.activePopover === 'context'}
                onOpenChange={(next) => popovers.setPillPopoverOpen('context', next)}
              />
            </>
          }
        >
          <ComposerAttachmentChips
            attachments={attachments}
            onRemove={removeAttachment}
          />

          {sendKind === 'steer' || sendKind === 'continue' ? (
            <div
              className="px-4 pt-2 text-2xs text-[var(--dt-fg-3)]"
              data-testid="composer-harness-chip"
            >
              {sendKind === 'steer'
                ? `Steering ${focusedSubagent?.workstream || focusedSubagent?.title || 'worker'} (live)`
                : `Will continue ${continueName || focusedSubagent?.workstream || 'workstream'}`}
              {ghostNext && sendKind === 'continue' ? ` · ${ghostNext}` : ''}
            </div>
          ) : null}

          <textarea
            ref={taRef}
            value={input}
            onChange={(e) => {
              popovers.handleInputChange(e.target.value);
            }}
            onKeyDown={popovers.onKey}
            onPaste={handleComposerPaste}
            aria-label="Chat message"
            placeholder={
              streaming
                ? 'Keep typing to queue follow-up changes'
                : focusedSubagent
                  ? focusedSubagent.running === false && focusedSubagent.workstream
                    ? ghostNext || `Continue ${focusedSubagent.workstream}…`
                    : `Steer ${focusedSubagent.title} (next round)`
                  : continueName
                    ? ghostNext || `Continue workstream ${continueName}…`
                    : harnessMode === 'orchestrator'
                      ? 'Plan the next wave — or open Dispatch from +'
                      : 'Write a message, or tell August what to do...'
            }
            rows={1}
            className="block max-h-[240px] min-h-0 w-full resize-none bg-transparent px-4 py-3.5 text-sm leading-5 outline-none placeholder:text-[var(--dt-fg-3)]"
          />

          {showPreview && input.trim() && (
            <div
              className="max-h-[240px] overflow-y-auto border-t border-[var(--dt-line)] bg-[var(--dt-background)]/40 px-4 py-2 text-[var(--dt-fg-1)]"
              aria-label="Message preview"
              data-testid="composer-preview"
            >
              <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-[var(--dt-fg-3)]">
                Preview
              </div>
              <Markdown content={input} />
            </div>
          )}
        </ComposerToolbar>
      </div>

      {debateOpen ? (
        <DebateLaunchModal
          models={visibleModels}
          initialPrompt={input}
          onClose={() => setDebateOpen(false)}
          onLaunch={(a, b, judge, rounds, prompt) => {
            setDebateOpen(false);
            onDebateLaunch?.(a, b, judge, rounds, prompt);
          }}
        />
      ) : null}

      {arenaOpen ? (
        <ArenaLaunchModal
          models={visibleModels}
          initialPrompt={input}
          onClose={() => setArenaOpen(false)}
          onLaunch={(targets, prompt) => {
            setArenaOpen(false);
            onArenaLaunch?.(targets, prompt);
          }}
        />
      ) : null}
    </div>
  );
}
