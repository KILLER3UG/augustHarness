import { Check, Copy, RefreshCw, Play, Pause, GitBranch, ArrowLeftRight, GitCompare, ThumbsDown, ThumbsUp } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { MessageTimestamp } from '@/components/chat/MessageTimestamp';

/** Speak / copy / re-answer / regenerate / fork controls under an assistant message. */
export function AssistantMessageActions({
  showActions,
  copied,
  speaking,
  streaming,
  isRegenerating,
  onSpeak,
  onCopy,
  onRegen,
  onFork,
  onReanswer,
  reanswerOpen,
  onCompare,
  rating,
  onRate,
  modelLabel,
  timestamp,
}: {
  showActions: boolean;
  copied: boolean;
  speaking: boolean;
  isLast?: boolean;
  streaming?: boolean;
  isRegenerating: boolean;
  onSpeak: () => void;
  onCopy: () => void;
  onRegen: () => void;
  onFork?: () => void;
  /** "Answer this with another model" — toggles the model list in the bubble. */
  onReanswer?: () => void;
  reanswerOpen?: boolean;
  /** "Compare" — re-run the prompt on 2–3 models side by side (Arena lanes). */
  onCompare?: () => void;
  /** The stored verdict on this reply, or null/undefined for "not rated". */
  rating?: 'up' | 'down' | null;
  /** Record (or clear, when it repeats) the verdict for this reply. The pair
   *  is rendered only when a caller supplies this, so an unrated row never
   *  advertises a door that goes nowhere. */
  onRate?: (value: 'up' | 'down') => void;
  /** The model that produced THIS turn. It used to sit in a head row above the
   *  answer; the composer footer only ever names the model selected NOW, so a
   *  scrolled-back turn has to carry its own. */
  modelLabel?: string | null;
  /** Age stamp, rendered as the last item on this row. The reference's only
   *  time marker is the muted `5 minutes ago` after the buttons. */
  timestamp?: string | null;
}) {
  return (
    <div className={cn(
      "flex items-center gap-1 mt-1 transition-opacity duration-150 self-start",
      /* `group-focus-within:` was dead here — no ancestor carries `group`, so
         keyboard focus landed on four invisible buttons. Revealing on the row's
         own `:focus-within` needs no ancestor and matches DisclosureRow. */
      showActions ? "opacity-100" : "opacity-0 focus-within:opacity-100"
    )}>
      <button
        onClick={onSpeak}
        aria-label={speaking ? "Pause reading" : "Read aloud"}
        className={cn(
          "p-1.5 rounded-md transition-colors",
          speaking
            ? "bg-primary/10 text-primary hover:bg-primary/20"
            : "hover:bg-accent/50 text-muted-foreground hover:text-foreground"
        )}
        title={speaking ? "Pause reading" : "Read aloud"}
      >
        {speaking ? (
          <Pause className="size-3" />
        ) : (
          <Play className="size-3" />
        )}
      </button>
      <button
        onClick={onCopy}
        className="p-1.5 rounded-md hover:bg-accent/50 text-muted-foreground hover:text-foreground transition-colors relative"
        title="Copy"
        aria-label="Copy"
      >
        <div className={cn("transition-transform duration-200", copied ? "scale-110 text-success" : "scale-100")}>
          {copied ? (
            <Check className="size-3" />
          ) : (
            <Copy className="size-3" />
          )}
        </div>
      </button>
      {onReanswer && (
        <button
          onClick={onReanswer}
          disabled={streaming}
          className={cn(
            "p-1.5 rounded-md hover:bg-accent/50 text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50",
            reanswerOpen && "text-primary",
          )}
          title="Answer this with another model"
          aria-label="Answer this with another model"
          aria-expanded={reanswerOpen}
          data-testid="reanswer-open"
        >
          <ArrowLeftRight className="size-3" />
        </button>
      )}
      {onCompare && (
        <button
          onClick={onCompare}
          disabled={streaming}
          className="p-1.5 rounded-md hover:bg-accent/50 text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
          title="Compare models — re-run this prompt on 2–3 models side by side"
          aria-label="Compare models side by side"
          data-testid="compare-open"
        >
          <GitCompare className="size-3" />
        </button>
      )}
      <button
        onClick={onRegen}
        disabled={streaming || isRegenerating}
        className="p-1.5 rounded-md hover:bg-accent/50 text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
        title="Retry / Regenerate"
        aria-label="Retry / Regenerate"
      >
        <RefreshCw
          className={cn("size-3", isRegenerating && "animate-spin")}
        />
      </button>
      {onFork && (
        <button
          onClick={onFork}
          disabled={streaming}
          className="p-1.5 rounded-md hover:bg-accent/50 text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
          title="Fork conversation from here"
          aria-label="Fork conversation from here"
        >
          <GitBranch className="size-3" />
        </button>
      )}
      {/* The verdict pair. `aria-pressed` carries the state rather than a
          colour, so the settled choice reads in the accessibility tree and in
          the dim-until-hover row alike; pressing the same thumb again clears it. */}
      {onRate && (
        <>
          <Button
            variant="bare"
            type="button"
            onClick={() => onRate('up')}
            disabled={streaming}
            aria-pressed={rating === 'up'}
            className={cn(
              "p-1.5 rounded-md hover:bg-accent/50 transition-colors disabled:opacity-50",
              rating === 'up' ? "text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
            title={rating === 'up' ? "Good answer — tap to clear" : "Good answer"}
            aria-label={rating === 'up' ? "Clear good-answer rating" : "Mark as good answer"}
          >
            <ThumbsUp className="size-3" />
          </Button>
          <Button
            variant="bare"
            type="button"
            onClick={() => onRate('down')}
            disabled={streaming}
            aria-pressed={rating === 'down'}
            className={cn(
              "p-1.5 rounded-md hover:bg-accent/50 transition-colors disabled:opacity-50",
              rating === 'down' ? "text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
            title={rating === 'down' ? "Bad answer — tap to clear" : "Bad answer"}
            aria-label={rating === 'down' ? "Clear bad-answer rating" : "Mark as bad answer"}
          >
            <ThumbsDown className="size-3" />
          </Button>
        </>
      )}
      {/* The age stamp, as the LAST item on the same row — matching the
          reference, where the only time marker on a message is the muted
          `5 minutes ago` that follows the action buttons. It used to render as
          a sibling row 26px below them. */}
      {modelLabel ? (
        <span className="ml-1 pl-1 text-2xs text-muted-foreground/55">{modelLabel}</span>
      ) : null}
      {timestamp ? (
        <span className="ml-1 pl-1">
          <MessageTimestamp timestamp={timestamp} />
        </span>
      ) : null}
    </div>
  );
}
