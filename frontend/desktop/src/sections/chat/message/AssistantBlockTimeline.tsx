import { phaseLabel } from '@/lib/phase-labels';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useParams } from 'react-router-dom';
import { Brain, ChevronDown } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';
import { isTransportError } from '@/lib/error-copy';
import { restartBackendFromShell } from '@/api/client';
import { Button } from '@/components/ui/button';
import { Task, TaskContent, TaskTrigger } from '@/components/ui/task';
import { ToolCallItemBody } from '@/components/chat/ToolCallItem';
import {
  extractAgentId,
  extractCommand,
  extractDiffData,
  extractFilename,
  extractSubject,
} from '@/components/chat/tool/extractors';
import { PromptDisclosure } from '@/components/chat/PromptDisclosure';
import { ThoughtStep } from '@/components/chat/ThoughtStep';
import { ToolStepRow } from '@/components/chat/ToolStepRow';
import { EditRailRow } from '@/components/chat/EditRailRow';
import { MemoryEditRow } from '@/components/chat/MemoryEditRow';
import { SearchResultsTask } from '@/components/chat/SearchResultsCard';
import { isSubagentToolName } from '@/components/chat/subagent-tools';
import { SubagentDelegateRow } from '@/components/chat/SubagentDelegateRow';
import { ActivityGroup } from '@/components/chat/ActivityGroup';
import { countSharedFiles, summarizeTurnActivity } from '@/lib/tool-summary';
import { splitThinkingTexts, summarizeThoughtHeader } from '@/lib/process-summary';
import { collectSnapshots, fromSnapshot, readSnapshot } from '../stream/subagent-blocks';
import { classifyTool, normalizeToolName } from '@/lib/tool-classify';
import { Markdown } from '../ChatMarkdown';
import { useSmoothReveal } from '../hooks/useSmoothReveal';
import type { ChatMessage, MessageBlock, SubagentSnapshot } from '@/types/chat';
import type { SubagentBlockState } from '../chat-stream-manager';
import { buildProcessSummaryLine } from '@/lib/process-summary';
import {
  clearLiveActivity,
  publishLiveActivity,
  type LiveActivityItem,
  type LiveActivityKind,
} from '@/store/liveActivity';
import { getToolLabel } from '@/lib/tool-labels';
import { useVerboseMode } from '@/lib/verbose-mode';
import { resolveUiSessionId } from '../stream/session-id-map';

/** Restart the supervised backend from a "Cannot reach August" bubble, then let
 *  the caller re-send the turn. This goes through the shell's IPC because HTTP
 *  cannot reach the thing that needs killing. The escalation is deliberate: a
 *  backend that will not come back needs a fresh August, and only that failing
 *  needs a reboot. */
async function restartBackendThenRetry(retry?: () => void): Promise<void> {
  toast.message('Restarting the August backend…');
  const ok = await restartBackendFromShell();
  if (ok) {
    toast.success('Backend restarted');
    retry?.();
    return;
  }
  toast.error(
    'The backend did not come back — restart August. If a fresh launch still cannot reach it, restart the computer.',
  );
}

/** A stalled tool is a failed tool: the backend emits `stalled` when a call
 *  timed out, and every run-level summary must treat it the same as `error`
 *  or a run containing only timeouts looks healthy. */
function isToolFailure(status: string | undefined): boolean {
  return status === 'error' || status === 'stalled';
}

type DisplayBlock = MessageBlock;

/** Whole thoughts kept in the live feed per thinking block. A long turn can
 *  emit many thought paragraphs; the working indicator only shows the last
 *  few lines, so the rest is ballast. */
const MAX_LIVE_THOUGHTS = 6;

/** update_state phase → present-continuous title for the "Working…" row. */
const PHASE_ACTION_TITLES: Record<string, string> = {
  research: 'Researching',
  plan: 'Planning',
  implement: 'Implementing',
  review: 'Reviewing',
  complete: 'Finalizing',
};

/** Answer markdown with the smooth character reveal applied while `live`.
 *  A component (not a hook call in a `.map`) so each final block owns its
 *  own rAF loop; when `live` is false it renders the full text directly. */
function RevealedMarkdown({ content, live }: { content: string; live: boolean }) {
  const revealed = useSmoothReveal(content, live);
  return <Markdown content={revealed} variant="assistant" live={live} />;
}

function isFinalOutput(block: DisplayBlock): boolean {
  return (
    block.type === 'finalOutput' &&
    !!(block.content && String(block.content).trim())
  );
}

/**
 * A tool block that can join an activity group.
 *
 * The reference transcript collapses the WHOLE run of tool work into one
 * line — a read, a command, a read is one thing that happened — and the
 * phrase names each kind. Three kinds stay OUT of a group:
 *
 *   - a subagent launch: it renders its own delegate row (and its own
 *     drawer), and that row is already a disclosure
 *   - `update_state`: bookkeeping, represented by the phase marker
 *   - a call that is waiting for approval, or that carries an action
 *     widget: an INTERACTIVE control the user has to see and click, and
 *     a collapsed line hides both
 *
 * A FAILURE is groupable. The reference groups failures and marks the
 * line, and the group OPENS on one, so the red is on screen without a
 * click. The old rule refused to group a failure at all, on the grounds
 * that a failure hidden behind a collapsed line is one nobody sees —
 * that reasoning is answered by opening the group, not by keeping the
 * failure out of it.
 */
function isInteractiveActionTool(tool: NonNullable<DisplayBlock['tool']>): boolean {
  return !!(
    tool.pendingApproval ||
    tool.actionNeeded ||
    tool.providerSetup ||
    tool.integrationSetup ||
    tool.secretRequest
  );
}

/**
 * A tool call that renders as NOTHING at all.
 *
 * `update_state` is bookkeeping: the phase marker already says the turn
 * moved on, so a duplicate row is noise. A block that renders as nothing
 * must not BREAK a run either — the blocks on either side of it are
 * adjacent on screen, and adjacent work is one line. A FAILED
 * `update_state` does render, so it is not invisible bookkeeping and it
 * does break the run.
 */
function isInvisibleBookkeeping(block: DisplayBlock): boolean {
  return (
    block.type === 'toolCall' &&
    !!block.tool &&
    normalizeToolName(block.tool.name) === 'update_state' &&
    block.tool.status !== 'error'
  );
}

/** One memo per string for pure JSON-arg extractors. The timeline re-renders
 *  the last row on every stream flush, and each render used to JSON.parse
 *  every visible tool's args again (measured cost, audit 2026-09-27). */
function memoizeByString<A extends string | undefined, R>(fn: (a: A) => R): (a: A) => R {
  const cache = new Map<string, R>();
  return (a: A): R => {
    const key = a ?? '';
    if (cache.has(key)) return cache.get(key) as R;
    const value = fn(a);
    if (cache.size > 200) cache.clear();
    cache.set(key, value);
    return value;
  };
}

/** Pull the search query out of a web_search tool's JSON args. */
function extractSearchQuery(context?: string): string {
  if (!context) return 'Search';
  try {
    const parsed = JSON.parse(context) as Record<string, unknown>;
    for (const key of ['query', 'q', 'searchQuery', 'search_query', 'search_terms']) {
      const v = parsed?.[key];
      if (typeof v === 'string' && v.trim()) return v.trim();
      if (Array.isArray(v) && v.length > 0 && typeof v[0] === 'string' && v[0].trim()) {
        return v[0].trim();
      }
    }
  } catch {
    /* not JSON */
  }
  return 'Search';
}

/** Best-effort task/agent extraction from a spawn_subagent tool's JSON args —
 *  used by the inline delegate row when the live container is gone (reloaded
 *  transcript). Returns '' task when the context isn't parseable. */
function parseSubagentToolContext(context?: string): { task: string; agentId: string } {
  if (!context) return { task: '', agentId: '' };
  try {
    const parsed = JSON.parse(context) as Record<string, unknown>;
    const task = ['task', 'prompt', 'goal', 'description', 'userMessage', 'message']
      .map((k) => parsed?.[k])
      .find((v): v is string => typeof v === 'string' && !!v.trim());
    const agent = ['agentId', 'agent_id', 'agent', 'role']
      .map((k) => parsed?.[k])
      .find((v): v is string => typeof v === 'string' && !!v.trim());
    return { task: (task || '').trim().slice(0, 160), agentId: (agent || '').trim() };
  } catch {
    return { task: '', agentId: '' };
  }
}

const extractSearchQueryCached = memoizeByString(extractSearchQuery);
const parseSubagentToolContextCached = memoizeByString(parseSubagentToolContext);

/** Split blocks into process (thinking/tools) vs final answer. Error blocks
 *  are pulled out entirely — they render as a standalone message bubble below
 *  the timeline, never buried inside the collapsible activity pack (a failed
 *  turn left the only visible trace behind a collapsed "Task completed" row).
 *  System notices (retry warnings, context pressure, infos) are likewise
 *  pulled out — the thinking pack holds ONLY model chain-of-thought. */
function splitProcessAndFinal(blocks: DisplayBlock[]): {
  processBlocks: DisplayBlock[];
  finalBlocks: DisplayBlock[];
  errorBlocks: DisplayBlock[];
  noticeBlocks: DisplayBlock[];
  subagentBlocks: DisplayBlock[];
  hasFinalOutput: boolean;
} {
  let lastFinalIdx = -1;
  for (let i = 0; i < blocks.length; i++) {
    if (isFinalOutput(blocks[i])) lastFinalIdx = i;
  }
  const processBlocks: DisplayBlock[] = [];
  const finalBlocks: DisplayBlock[] = [];
  const errorBlocks: DisplayBlock[] = [];
  const noticeBlocks: DisplayBlock[] = [];
  const subagentBlocks: DisplayBlock[] = [];
  for (let i = 0; i < blocks.length; i++) {
    const block = blocks[i];
    if (block.type === 'error') {
      errorBlocks.push(block);
    } else if (block.type === 'system') {
      noticeBlocks.push(block);
    } else if (block.type === 'subagent') {
      subagentBlocks.push(block);
    } else if (isFinalOutput(block)) {
      if (i === lastFinalIdx) finalBlocks.push(block);
      else processBlocks.push({ ...block, type: 'thinking' });
    } else {
      processBlocks.push(block);
    }
  }
  return {
    processBlocks,
    finalBlocks,
    errorBlocks,
    noticeBlocks,
    subagentBlocks,
    hasFinalOutput: finalBlocks.length > 0,
  };
}

/** Plan §4.1 plan-tree group: one `update_state` phase and the tool rows
 *  that ran under it. Finished subtrees auto-collapse (expanded defaults
 *  come from the parent); the active group stays open and highlighted. */
export type SubagentPromptEntry = {
  content: string;
  systemPrompt: string;
  userMessage: string;
  tokens: number;
  subagentId?: string;
  jobId?: string;
};

export type ToolProgressMap = Map<
  string,
  ReadonlyArray<{ path: string; status: 'reading' | 'read' }>
>;

/** A settled or active sub-agent whose live block was evicted (reloaded
 *  transcript, SSE close, drawer reset). Renders inline as a one-row stub
 *  so the user can still see "what ran" at the spawn call's position. */
export interface InlineSubagentRosterEntry {
  jobId: string;
  agentId: string;
  task: string;
  status: 'running' | 'pending' | 'completed' | 'failed' | 'cancelled' | 'partial' | 'done' | 'error';
  startedAt?: number;
  finishedAt?: number;
  workstream?: string;
}

/** Interleaved process timeline (thinking/tools) + final answer. */
export function AssistantBlockTimeline({
  displayBlocks,
  message,
  isLast,
  streaming,
  showPendingThinking,
  toolProgress,
  subagentPrompts,
  subagentBlocks,
  subagentRoster,
  modelId,
  sessionId,
  onRetryTurn,
  onSwitchModel,
  onDismissError,
}: {
  displayBlocks: DisplayBlock[];
  message: ChatMessage;
  isLast?: boolean;
  streaming?: boolean;
  showPendingThinking: boolean;
  toolProgress?: ToolProgressMap;
  subagentPrompts?: Map<string, SubagentPromptEntry>;
  /** Keyed by sub-agent jobId; rendered inline via SubagentDelegateRow
   *  (and in the persistent right-drawer roster). */
  subagentBlocks?: Map<string, SubagentBlockState>;
  /** Settled/active runs whose live SSE block is gone (the keystone of
   *  delegation visibility after reload). Merged with subagentBlocks so
   *  a spawn call always renders at least one row. */
  subagentRoster?: ReadonlyArray<InlineSubagentRosterEntry>;
  /** Parent session model id — shown as muted tag on subagent launch rows. */
  modelId?: string | null;
  /** Chat session id — keys the per-session /verbose flag. */
  sessionId?: string | null;
  /** Rendered on error blocks: re-run the turn from the last user prompt. */
  onRetryTurn?: () => void;
  /** Rendered on error blocks: open the "answer with another model" picker. */
  onSwitchModel?: () => void;
  /** Rendered on error blocks: dismiss the error bubble (removes the block). */
  onDismissError?: () => void;
}) {
  const { sessionId: routeSessionId } = useParams<{ sessionId?: string }>();
  const liveSessionKey = resolveUiSessionId(routeSessionId || message.id);
  // /verbose: raw tool output renders inline for this
  // session until turned off. Rendering policy only — data layer unchanged.
  const verbose = useVerboseMode(sessionId);
  // Kept in the public props for message-pane compatibility; subagent
  // progress no longer renders an inline model label.
  void modelId;

  const {
    processBlocks,
    finalBlocks,
    errorBlocks,
    noticeBlocks,
    subagentBlocks: persistedSubagentBlocks,
    hasFinalOutput,
  } = splitProcessAndFinal(displayBlocks);

  // One row per worker, from three sources that overlap by design:
  //   1. the live `subagentBlocks` map (SSE, this session),
  //   2. the `subagent` blocks on this message (persisted with the transcript),
  //   3. the backend roster (settled runs whose SSE block was evicted).
  // A job already rendered from a live container is skipped by the later
  // sources, which is what stops a reconnected session from showing the same
  // worker twice — once live, once restored.
  const liveSubagentStates = useMemo(
    () => new Map(subagentBlocks ?? []),
    [subagentBlocks],
  );
  const persistedSubagentStates = useMemo(
    () => collectSnapshots(persistedSubagentBlocks).map(fromSnapshot),
    [persistedSubagentBlocks],
  );
  /** Every worker this message can show, live snapshot preferred. */
  const allSubagentStates = useMemo(() => {
    const out = new Map<string, SubagentBlockState>();
    for (const state of persistedSubagentStates) out.set(state.jobId, state);
    for (const [jobId, state] of liveSubagentStates) out.set(jobId, state);
    return new Map(
      [...out.entries()].sort((a, b) => (a[1].startedAt || 0) - (b[1].startedAt || 0)),
    );
  }, [persistedSubagentStates, liveSubagentStates]);

  // Id-keyed expand overrides; missing key → default from status.
  // Tools: running → open, else collapsed. Thoughts: collapsed+clamped by
  // default; the user's explicit expand/collapse WINS until the final
  // answer starts streaming (2026-08-25 fix: mid-turn streaming gaps must
  // NOT wipe manual expansion — only a real final response closes it).
  const [expandOverrides, setExpandOverrides] = useState<Record<string, boolean>>(
    {},
  );

  // When the FINAL ANSWER starts generating, drop expand overrides so
  // thoughts re-collapse (user requirement: thinking closes ONLY when the
  // user closes it manually, or when the final response is generating).
  // Partial-final turns (streaming flag flickers between tool rounds) do
  // not reset anything — only a genuine final output block does.
  useEffect(() => {
    if (hasFinalOutput) {
      setExpandOverrides({});
    }
  }, [hasFinalOutput]);

  const toggleExpand = (id: string, next: boolean) => {
    setExpandOverrides((prev) => ({ ...prev, [id]: next }));
  };

  const isToolExpanded = (
    toolId: string,
    status: string | undefined,
    tool?: MessageBlock['tool'],
  ) => {
    if (toolId in expandOverrides) return expandOverrides[toolId];
    // Open while running; once complete the block keeps whatever state the
    // user left it in (ToolStepRow never force-collapses on completion).
    if (status === 'running') return true;
    // Plan §4.1: edit diffs, search hits, and memory writes are
    // expanded-by-default rows (capped bodies, still collapsible).
    if (tool) {
      const bucket = classifyTool(tool.name);
      if (bucket === 'edit' && extractDiffData(tool)) return true;
      if (tool.searchHits && tool.searchHits.length > 0) return true;
      if (bucket === 'memoryWrite') return true;
    }
    return false;
  };

  const isThoughtExpanded = (thoughtId: string) => {
    if (thoughtId in expandOverrides) return expandOverrides[thoughtId];
    // Default to clamped ("Show more") whether streaming or settled — a long
    // thought clamps once it passes the threshold instead of pushing the whole
    // layout. Only an explicit Show more expands it.
    return false;
  };

  const thinkingParts = processBlocks
    .filter((b) => b.type === 'thinking' && b.content?.trim())
    .map((b) => b.content!.trim());
  const processSummary = buildProcessSummaryLine(thinkingParts);

  let anyToolRunning = false;
  let seqStart = Number.POSITIVE_INFINITY;
  let seqEnd = 0;
  for (const block of processBlocks) {
    if ((block.type === 'toolCall' || block.type === 'command') && block.tool) {
      const tool = block.tool;
      if (tool.status === 'running') anyToolRunning = true;
      if (tool.startedAt) {
        seqStart = Math.min(seqStart, tool.startedAt);
        seqEnd = Math.max(seqEnd, tool.startedAt + (tool.duration ?? 0));
      }
    }
  }
  // Coalesced consecutive thoughts count as one ThoughtStep in the UI.
  const coalescedThoughtCount = (() => {
    let n = 0;
    let i = 0;
    while (i < processBlocks.length) {
      if (processBlocks[i].type === 'thinking') {
        n += 1;
        while (i < processBlocks.length && processBlocks[i].type === 'thinking') i++;
        continue;
      }
      i++;
    }
    return n;
  })();

  const livePacked = !!(
    isLast &&
    streaming &&
    (processBlocks.length > 0 || showPendingThinking)
  );
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!livePacked) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [livePacked]);
  const summedToolMs = processBlocks.reduce(
    (sum, b) =>
      sum +
      ((b.type === 'toolCall' || b.type === 'command') && b.tool
        ? (b.tool.duration ?? 0)
        : 0),
    0,
  );

  const { liveDetail, liveItems } = useMemo(() => {
    const items: LiveActivityItem[] = [];
    // The "Working…" row shows a TITLE of what the model is doing — never a
    // thinking snippet (the CoT already streams above the composer in the
    // WorkingIndicator). Priority: running tool > update_state phase >
    // last completed tool > generic Thinking….
    let runningTitle = '';
    let completedTitle = '';
    let phaseTitle = '';
    // Only the final thinking block can still be in flight. Earlier blocks are
    // settled, so they must never be handed to the splitter as "streaming" —
    // otherwise a settled thought that happens to lack a trailing full stop
    // would render a phantom running line.
    const lastThinkingBlock = [...displayBlocks].reverse().find((b) => b.type === 'thinking');
    for (const block of displayBlocks) {
      const inFlight =
        isLast && streaming && !hasFinalOutput && block === lastThinkingBlock;
      if (block.type === 'thinking' && block.content?.trim()) {
        // ONE item per whole thought. Boundaries come from the thought
        // splitter (blank lines / headings / separate thinking blocks), never
        // from periods, so a five-sentence thought is one line. The still
        // streaming thought arrives as `tail` and is the only running item.
        const { thoughts, tail } = splitThinkingTexts(block.content.trim(), inFlight);
        const completed = thoughts.slice(-MAX_LIVE_THOUGHTS);
        const blockKey = block.id || `think_${items.length}`;
        for (let si = 0; si < completed.length; si++) {
          items.push({
            id: `${blockKey}_s${si}`,
            kind: 'thinking',
            label: 'Thinking',
            detail: summarizeThoughtHeader(completed[si], false),
            status: 'done',
            at: Date.now(),
          });
        }
        if (tail) {
          items.push({
            id: `${blockKey}_tail`,
            kind: 'thinking',
            label: 'Thinking',
            detail: summarizeThoughtHeader(tail, true),
            status: inFlight ? 'running' : 'done',
            at: Date.now(),
          });
        }
      }
      if (block.type === 'phase') {
        const phase = (block.content || '').trim().toLowerCase();
        phaseTitle = PHASE_ACTION_TITLES[phase] || '';
      }
      if ((block.type === 'toolCall' || block.type === 'command') && block.tool) {
        const bucket = classifyTool(block.tool.name) as LiveActivityKind;
        const kind: LiveActivityKind =
          bucket === 'view' || bucket === 'edit' || bucket === 'run' ? bucket : 'tool';
        const toolStatus =
          isToolFailure(block.tool.status)
            ? ('error' as const)
            : block.tool.status === 'running'
              ? ('running' as const)
              : ('done' as const);
        const label = getToolLabel(block.tool.name, {
          status: toolStatus,
          command: extractCommand(block.tool.context) ?? undefined,
          filename: extractFilename(block.tool.context) ?? undefined,
          agentId: extractAgentId(block.tool.context) ?? undefined,
          subject: extractSubject(block.tool.context) ?? undefined,
        });
        const detail =
          block.tool.preview?.slice(-120) ||
          block.tool.summary ||
          block.tool.context?.slice(0, 100) ||
          undefined;
        items.push({
          id: block.tool.id || block.id || `tool_${items.length}`,
          kind,
          label,
          detail,
          status:
            isToolFailure(block.tool.status)
              ? 'error'
              : block.tool.status === 'running'
                ? 'running'
                : 'done',
          at: block.tool.startedAt || Date.now(),
        });
        if (block.tool.status === 'running') {
          runningTitle = label;
        } else {
          completedTitle = label;
        }
      }
    }
    let liveDetail = runningTitle || phaseTitle || completedTitle;
    if (showPendingThinking && !liveDetail) liveDetail = 'Thinking…';
    if (items.length > 0) {
      const last = items[items.length - 1];
      if (last.status === 'running' || (isLast && streaming)) {
        items[items.length - 1] = {
          ...last,
          status: last.status === 'error' ? 'error' : 'running',
        };
      }
    }
    return { liveDetail, liveItems: items };
  }, [displayBlocks, showPendingThinking, isLast, streaming, hasFinalOutput]);

  useEffect(() => {
    if (!livePacked) {
      if (isLast && !streaming) {
        clearLiveActivity(liveSessionKey);
      }
      return;
    }
    publishLiveActivity({
      sessionId: liveSessionKey,
      headline: liveDetail || processSummary || 'Working…',
      items: liveItems,
    });
  }, [livePacked, liveSessionKey, liveDetail, liveItems, processSummary, isLast, streaming]);

  const buildTurnActivityLayout = () => {
    const foldedNodes: ReactNode[] = [];
    const exposedNodes: ReactNode[] = [];
    const foldedTools: NonNullable<DisplayBlock['tool']>[] = [];
    const claimed = new Set<string>();
    let settledSubagentsCount = 0;
    let settledSubagentFailed = false;
    let liveSubagentRunning = false;
    let settledSubagentMs = 0;

    if (showPendingThinking) {
      foldedNodes.push(
        <ThoughtStep
          key="pending_think"
          content=""
          isGenerating
          showFull={isThoughtExpanded('pending_think')}
          onToggle={() =>
            toggleExpand('pending_think', !isThoughtExpanded('pending_think'))
          }
          collapsedDefault={false}
        />,
      );
    }

    let ti = 0;
    while (ti < processBlocks.length) {
      const block = processBlocks[ti];

      if (block.type === 'thinking') {
        const parts: string[] = [];
        const start = ti;
        while (ti < processBlocks.length && processBlocks[ti].type === 'thinking') {
          const c = (processBlocks[ti].content || '').trim();
          if (c) parts.push(c);
          ti++;
        }
        const isGenerating = !!(
          isLast &&
          streaming &&
          !hasFinalOutput &&
          ti === processBlocks.length
        );
        const thoughtId = block.id || `think_${start}`;
        const thoughtExpanded = isThoughtExpanded(thoughtId);
        foldedNodes.push(
          <ThoughtStep
            key={thoughtId}
            content={parts.join('\n\n')}
            isGenerating={isGenerating}
            showFull={thoughtExpanded}
            onToggle={() => toggleExpand(thoughtId, !thoughtExpanded)}
            collapsedDefault={false}
          />,
        );
        continue;
      }

      if (block.type === 'phase' && block.content && block.content.trim()) {
        foldedNodes.push(
          <div
            key={block.id || `phase_${ti}`}
            className="pt-0.5 text-2xs font-medium uppercase tracking-wider text-[var(--dt-fg-3)]"
            data-testid="plan-phase-head"
          >
            {phaseLabel(block.content.trim())}
            {typeof block.step === 'number' ? ` · step ${block.step}` : ''}
          </div>,
        );
        ti++;
        continue;
      }

      if ((block.type === 'toolCall' || block.type === 'command') && block.tool) {
        const tool = block.tool;
        const isCommand = block.type === 'command';
        const isSubagentCall = !isCommand && isSubagentToolName(tool.name);
        const toolId = tool.id || block.id || `tool_${ti}`;

        if (isSubagentCall) {
          const liveContainers = subagentBlocks
            ? Array.from(subagentBlocks.values())
                .filter((s) => s.parentToolId === tool.id)
                .sort((a, b) => a.startedAt - b.startedAt)
            : [];
          for (const c of liveContainers) claimed.add(c.jobId);
          const persistedForTool = persistedSubagentStates.filter((s) => {
            if (claimed.has(s.jobId)) return false;
            if (s.parentToolId !== tool.id) return false;
            claimed.add(s.jobId);
            return true;
          });
          const rosterForTool = (subagentRoster ?? []).filter((r) => {
            if (!r.jobId || claimed.has(r.jobId)) return false;
            claimed.add(r.jobId);
            return true;
          });

          const workers: Array<{
            key: string;
            jobId?: string;
            agentId: string;
            task: string;
            status: InlineSubagentRosterEntry['status'];
            startedAt?: number;
            finishedAt?: number;
            workstream?: string;
            state?: SubagentBlockState;
          }> = [];

          if (liveContainers.length > 0) {
            for (const c of liveContainers) {
              workers.push({
                key: c.jobId,
                jobId: c.jobId,
                agentId: c.agentId,
                task: c.task || '',
                status: c.status,
                startedAt: c.startedAt,
                finishedAt: c.finishedAt,
                workstream: c.workstream,
                state: c,
              });
            }
          } else if (persistedForTool.length > 0) {
            for (const s of persistedForTool) {
              workers.push({
                key: `sub_block_${s.jobId}`,
                jobId: s.jobId,
                agentId: s.agentId,
                task: s.task || '',
                status: s.status,
                startedAt: s.startedAt,
                finishedAt: s.finishedAt,
                workstream: s.workstream,
                state: s,
              });
            }
          } else if (rosterForTool.length > 0) {
            for (const r of rosterForTool) {
              workers.push({
                key: `roster_${r.jobId}`,
                jobId: r.jobId,
                agentId: r.agentId,
                task: r.task,
                status: r.status,
                startedAt: r.startedAt,
                finishedAt: r.finishedAt,
                workstream: r.workstream,
                state: allSubagentStates.get(r.jobId),
              });
            }
          } else {
            const ctx = parseSubagentToolContextCached(tool.context);
            workers.push({
              key: toolId,
              agentId: ctx.agentId || 'general',
              task: ctx.task,
              status: isToolFailure(tool.status)
                ? 'failed'
                : tool.status === 'running'
                  ? 'running'
                  : 'completed',
              startedAt: tool.startedAt,
            });
          }

          for (const w of workers) {
            const isWorkerLive = w.status === 'running' || w.status === 'pending';
            const row = (
              <SubagentDelegateRow
                key={w.key}
                jobId={w.jobId}
                agentId={w.agentId}
                task={w.task}
                status={w.status}
                startedAt={w.startedAt}
                finishedAt={w.finishedAt}
                workstream={w.workstream}
                state={w.state}
                defaultExpanded={isWorkerLive}
                sessionId={liveSessionKey}
              />
            );
            if (isWorkerLive) {
              liveSubagentRunning = true;
              exposedNodes.push(row);
            } else {
              settledSubagentsCount += 1;
              if (w.status === 'failed' || w.status === 'error') {
                settledSubagentFailed = true;
              }
              if (w.startedAt && w.finishedAt && w.finishedAt > w.startedAt) {
                settledSubagentMs += w.finishedAt - w.startedAt;
              }
              foldedNodes.push(row);
            }
          }
          ti++;
          continue;
        }

        if (isInvisibleBookkeeping(block)) {
          ti++;
          continue;
        }

        const promptEntries =
          tool.id && subagentPrompts
            ? Array.from(subagentPrompts.entries())
                .filter(([k]) => k === tool.id)
                .map(([, v]) => v)
            : [];
        const agentId =
          promptEntries[0]?.subagentId ??
          extractAgentId(tool.context) ??
          undefined;
        const filename = !isCommand ? extractFilename(tool.context) : null;
        const label = getToolLabel(tool.name, {
          agentId: agentId ?? undefined,
          filename: filename ?? undefined,
          command: isCommand ? extractCommand(tool.context) ?? undefined : undefined,
          subject: extractSubject(tool.context) ?? undefined,
          status: tool.status,
        });

        // Approval prompts and interactive action widgets stay visible outside
        // the folded activity line so the user can always see and act on them.
        if (isInteractiveActionTool(tool)) {
          const expanded =
            toolId in expandOverrides ? expandOverrides[toolId] : true;
          exposedNodes.push(
            <ToolStepRow
              key={toolId}
              tool={tool}
              label={label}
              isCommand={isCommand}
              expanded={expanded}
              verbose={verbose}
              onToggle={(next) => toggleExpand(toolId, next)}
              progress={tool.id ? toolProgress?.get(tool.id) : undefined}
            >
              <ToolCallItemBody
                tool={tool}
                progress={tool.id ? toolProgress?.get(tool.id) : undefined}
                verbose={verbose}
              />
            </ToolStepRow>,
          );
          ti++;
          continue;
        }

        foldedTools.push(tool);
        const expanded =
          toolId in expandOverrides
            ? expandOverrides[toolId]
            : isToolFailure(tool.status) || isToolExpanded(toolId, tool.status, tool);

        if (!isCommand && tool.searchHits && tool.searchHits.length > 0) {
          foldedNodes.push(
            <SearchResultsTask
              key={toolId}
              query={extractSearchQueryCached(tool.context)}
              hits={tool.searchHits}
              expanded={expanded}
              onToggle={(next) => toggleExpand(toolId, next)}
            />,
          );
          ti++;
          continue;
        }

        if (!isCommand && classifyTool(tool.name) === 'edit') {
          foldedNodes.push(
            <EditRailRow key={toolId} tool={tool} expanded={expanded} />,
          );
          ti++;
          continue;
        }

        if (!isCommand && classifyTool(tool.name) === 'memoryWrite') {
          foldedNodes.push(
            <MemoryEditRow key={toolId} tool={tool} expanded={expanded} />,
          );
          ti++;
          continue;
        }

        foldedNodes.push(
          <ToolStepRow
            key={toolId}
            tool={tool}
            label={label}
            isCommand={isCommand}
            expanded={expanded}
            verbose={verbose}
            onToggle={(next) => toggleExpand(toolId, next)}
            progress={tool.id ? toolProgress?.get(tool.id) : undefined}
            afterRow={
              promptEntries.length > 0 ? (
                <div className="mt-1.5 flex flex-col gap-1">
                  {promptEntries.map((p, pi) => (
                    <PromptDisclosure
                      key={`${toolId}-prompt-${pi}`}
                      content={p.content}
                      tokens={p.tokens}
                      label={
                        p.subagentId
                          ? `SUB-AGENT PROMPT · ${p.subagentId}`
                          : 'SUB-AGENT PROMPT'
                      }
                    />
                  ))}
                </div>
              ) : null
            }
          >
            <ToolCallItemBody
              tool={tool}
              progress={tool.id ? toolProgress?.get(tool.id) : undefined}
              verbose={verbose}
            />
          </ToolStepRow>,
        );
        ti++;
        continue;
      }

      if (block.type === 'memoryNotice') {
        foldedNodes.push(
          <div
            key={block.id || `memory_${ti}`}
            data-testid="memory-notice-chip"
            className="inline-flex max-w-full items-center gap-1.5 text-2xs text-[var(--dt-fg-3)]"
            title={block.content || undefined}
          >
            <Brain className="size-3 shrink-0 opacity-70" aria-hidden />
            <span className="truncate">{block.content || 'Memory updated'}</span>
          </div>,
        );
        ti++;
        continue;
      }

      ti++;
    }

    // Orphan persisted sub-agent blocks attached directly to this message.
    const orphanSnapshots = persistedSubagentBlocks
      .map((block) => readSnapshot(block))
      .filter((s): s is SubagentSnapshot => !!s && !claimed.has(s.jobId));
    for (const snap of orphanSnapshots) {
      const resolved = allSubagentStates.get(snap.jobId) ?? fromSnapshot(snap);
      const isWorkerLive =
        resolved.status === 'running' || resolved.status === 'pending';
      const row = (
        <SubagentDelegateRow
          key={`orphan_${resolved.jobId}`}
          jobId={resolved.jobId}
          agentId={resolved.agentId}
          task={resolved.task || ''}
          status={resolved.status}
          startedAt={resolved.startedAt}
          finishedAt={resolved.finishedAt}
          workstream={resolved.workstream}
          state={resolved}
          defaultExpanded={isWorkerLive}
          sessionId={liveSessionKey}
        />
      );
      if (isWorkerLive) {
        liveSubagentRunning = true;
        exposedNodes.push(row);
      } else {
        settledSubagentsCount += 1;
        if (resolved.status === 'failed') {
          settledSubagentFailed = true;
        }
        if (
          resolved.startedAt &&
          resolved.finishedAt &&
          resolved.finishedAt > resolved.startedAt
        ) {
          settledSubagentMs += resolved.finishedAt - resolved.startedAt;
        }
        foldedNodes.push(row);
      }
    }

    // D13: fold stacked post-turn status notices into the activity timeline
    // so they never clutter the main transcript as stacked status strips.
    noticeBlocks.forEach((block, index) => {
      foldedNodes.push(
        <div
          key={block.id || `notice_${index}`}
          data-testid="chat-system-notice"
          className="text-2xs leading-relaxed text-[var(--dt-fg-3)]"
        >
          {block.content}
        </div>,
      );
    });

    const sharedFiles = countSharedFiles(foldedTools);
    if (sharedFiles > 0) {
      foldedNodes.push(
        <div
          key="activity-presented"
          className="py-0.5 text-2xs text-[var(--dt-fg-3)]"
          data-testid="activity-group-presented"
        >
          Presented {sharedFiles} {sharedFiles === 1 ? 'file' : 'files'}
        </div>,
      );
    }

    const activityFailed =
      foldedTools.some((t) => isToolFailure(t.status)) || settledSubagentFailed;
    const activityRunning =
      anyToolRunning ||
      (!!isLast && !!streaming && !hasFinalOutput && !liveSubagentRunning);
    const activityElapsedMs =
      Number.isFinite(seqStart) && (anyToolRunning || seqEnd > seqStart)
        ? (anyToolRunning ? nowMs : seqEnd) - seqStart
        : summedToolMs + settledSubagentMs;

    return {
      foldedNodes,
      exposedNodes,
      activityFailed,
      activityRunning,
      activityElapsedMs,
      summary: summarizeTurnActivity({
        tools: foldedTools,
        settledSubagents: settledSubagentsCount,
        thoughtCount: coalescedThoughtCount || (showPendingThinking ? 1 : 0),
        noticeCount: noticeBlocks.length,
        running: activityRunning,
      }),
    };
  };

  const {
    foldedNodes,
    exposedNodes,
    activityFailed,
    activityRunning,
    activityElapsedMs,
    summary: activitySummary,
  } = buildTurnActivityLayout();
  const turnGroupKey = `turn_${message.id}`;
  const activityExpanded =
    turnGroupKey in expandOverrides ? expandOverrides[turnGroupKey] : activityFailed;

  const renderFinal = (blocks: DisplayBlock[]) =>
    blocks.map((block, index) => {
      if (!block.content) return null;
      const key = block.id || `final_${index}`;
      const isFinalStreaming = !!(isLast && streaming);
      return (
        <div
          key={key}
          className={cn(
            'chat-streaming-block',
            isFinalStreaming && 'chat-streaming-block--live',
          )}
        >
          <div
            className={cn(
              'chat-message-text text-foreground/90 space-y-3 max-w-none',
              isFinalStreaming && 'streaming-markdown-content',
            )}
          >
            <RevealedMarkdown content={block.content} live={isFinalStreaming} />
          </div>
        </div>
      );
    });

  const renderErrorBubble = (blocks: DisplayBlock[]) =>
    blocks.map((block, index) => {
      // A provider/turn failure rendered as its OWN message bubble — left
      // aligned like the model's replies, with the friendly copy up front,
      // the raw upstream text behind an expandable details, a Try again
      // action, and a corner ✕ to dismiss. Never collapsed into the activity
      // pack: a failed turn must be impossible to miss.
      const raw = block.rawContent;
      // "Failed to fetch" is the one error a backend restart can fix. The
      // restart goes through the shell's IPC because HTTP cannot reach the
      // thing that needs killing.
      const transport = isTransportError(raw);
      const key = block.id || `error_${index}`;
      return (
        <div
          key={key}
          role="alert"
          data-testid="chat-error-bubble"
          className="mt-1.5 flex w-full items-start gap-2.5"
        >
          <span
            aria-hidden="true"
            className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border border-danger/30 bg-danger/15 text-[0.8125rem] text-danger-fg"
          >
            !
          </span>
          <div className="relative min-w-0 flex-1 rounded-2xl border border-danger/30 bg-danger/[0.07] px-4 py-3">
            {onDismissError ? (
              <button
                type="button"
                onClick={onDismissError}
                aria-label="Dismiss error"
                data-testid="chat-error-dismiss"
                className="absolute right-2 top-2 flex size-5 items-center justify-center rounded text-danger-fg transition hover:bg-danger/15 hover:text-danger-fg"
              >
                ✕
              </button>
            ) : null}
            <div className="pr-5 text-[0.8125rem] leading-relaxed text-danger-fg">
              {block.content || 'Generation failed.'}
            </div>
            {raw ? (
              <Task defaultOpen={false} className="mt-1.5 block text-[0.75rem] text-danger-fg">
                <TaskTrigger asChild>
                  <Button type="button" variant="ghost" size="sm" className="h-auto gap-1.5 px-0 py-0 text-[0.75rem] font-normal">
                    <ChevronDown className="disclosure-chevron shrink-0 transition-transform group-data-[state=open]:rotate-180" aria-hidden />
                    Show provider details
                  </Button>
                </TaskTrigger>
                <TaskContent className="mt-1">
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-danger/40 p-2 font-mono text-2xs">
                    {raw}
                  </pre>
                </TaskContent>
              </Task>
            ) : null}
            {(onRetryTurn || onSwitchModel || transport) && (
              <div className="mt-2.5 flex flex-wrap items-center gap-2">
                {onRetryTurn ? (
                  <button
                    type="button"
                    onClick={onRetryTurn}
                    data-testid="chat-error-retry"
                    className="inline-flex items-center gap-1 rounded-lg border border-danger/40 bg-danger/15 px-2.5 py-1 text-2xs font-medium text-danger-fg transition hover:bg-danger/25"
                  >
                    ↻ Try again
                  </button>
                ) : null}
                {transport ? (
                  <Button
                    variant="bare"
                    type="button"
                    onClick={() => void restartBackendThenRetry(onRetryTurn)}
                    data-testid="chat-error-restart-backend"
                    title="Kill and relaunch the local August backend, then retry"
                    className="inline-flex items-center gap-1 rounded-lg border border-danger/40 bg-danger/15 px-2.5 py-1 text-2xs font-medium text-danger-fg transition hover:bg-danger/25"
                  >
                    ⟳ Restart backend
                  </Button>
                ) : null}
                {onSwitchModel ? (
                  <button
                    type="button"
                    onClick={onSwitchModel}
                    className="rounded-lg border border-danger/30 px-2.5 py-1 text-2xs font-medium text-danger-fg transition hover:bg-danger/10"
                  >
                    Switch model
                  </button>
                ) : null}
              </div>
            )}
          </div>
        </div>
      );
    });

  return (
    <div className="process-timeline flex flex-col gap-2" data-slot="process-timeline">
      {foldedNodes.length > 0 && (
        <ActivityGroup
          groupKey={turnGroupKey}
          summary={activitySummary}
          elapsedMs={activityElapsedMs > 0 ? activityElapsedMs : undefined}
          running={activityRunning}
          failed={activityFailed}
          expanded={activityExpanded}
          onToggle={(next) => toggleExpand(turnGroupKey, next)}
        >
          {foldedNodes}
        </ActivityGroup>
      )}
      {exposedNodes.length > 0 && (
        <div className="flex flex-col gap-1.5" data-testid="exposed-activity-rows">
          {exposedNodes}
        </div>
      )}
      {hasFinalOutput && renderFinal(finalBlocks)}
      {errorBlocks.length > 0 && renderErrorBubble(errorBlocks)}
    </div>
  );
}
