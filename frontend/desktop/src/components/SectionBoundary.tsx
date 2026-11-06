import { Component, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';

interface SectionBoundaryProps {
  /** Display name of the section (e.g. "Traffic", "Brain", "Skills"). */
  name: string;
  /** Optional custom fallback. Defaults to a section-specific error card. */
  fallback?: ReactNode;
  /** Compact pane variant for drawer panes: smaller card, Retry + optional
   *  dismiss, no navigation escape hatch (the pane sits over the chat). */
  compact?: boolean;
  /** Shown as "Dismiss" in the compact fallback (e.g. close the pane). */
  onDismiss?: () => void;
  children?: ReactNode;
}

interface SectionBoundaryState {
  hasError: boolean;
  error: Error | null;
  retryKey: number;
}

/**
 * Per-section error boundary that prevents one crashed section from
 * taking down the entire app. Shows a section-specific fallback with
 * retry and escape-hatch navigation.
 *
 * Usage in routes.ts:
 *   <SectionBoundary name="Traffic"><LazyTraffic /></SectionBoundary>
 */
export class SectionBoundary extends Component<SectionBoundaryProps, SectionBoundaryState> {
  state: SectionBoundaryState = { hasError: false, error: null, retryKey: 0 };

  static getDerivedStateFromError(error: Error): Partial<SectionBoundaryState> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error) {
    console.error(`[SectionBoundary:${this.props.name}]`, error);
  }

  private handleRetry = () => {
    this.setState((s) => ({ hasError: false, error: null, retryKey: s.retryKey + 1 }));
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      if (this.props.compact) {
        return (
          <PaneCrashFallback
            name={this.props.name}
            message={this.state.error?.message}
            onRetry={this.handleRetry}
            onDismiss={this.props.onDismiss}
          />
        );
      }
      return (
        <SectionCrashFallback
          name={this.props.name}
          message={this.state.error?.message}
          onRetry={this.handleRetry}
        />
      );
    }
    return <div key={this.state.retryKey} className="contents">{this.props.children}</div>;
  }
}

/** Compact fallback for a docked pane. Same honesty as the page-level
 * card, scoped to the pane: the rest of the app is fine and says so. */
function PaneCrashFallback({
  name,
  message,
  onRetry,
  onDismiss,
}: {
  name: string;
  message?: string;
  onRetry: () => void;
  onDismiss?: () => void;
}) {
  const truncated = message && message.length > 200 ? message.slice(0, 200) + '…' : message;
  return (
    <div className="grid h-full place-items-center p-4" data-testid={`pane-crash-${name.toLowerCase()}`}>
      <div className="max-w-xs text-center">
        <div className="mx-auto mb-2 flex h-8 w-8 items-center justify-center rounded-full bg-destructive/10">
          <span className="text-base">⚠️</span>
        </div>
        <h2 className="text-sm font-semibold">The {name} pane hit an error</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          The rest of the app is still running.
        </p>
        {truncated && (
          <p className="mt-2 rounded-md bg-muted p-2 text-left text-2xs text-muted-foreground font-mono">
            {truncated}
          </p>
        )}
        <div className="mt-3 flex items-center justify-center gap-2">
          <Button size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
          {onDismiss && (
            <Button size="sm" variant="ghost" onClick={onDismiss}>
              Dismiss
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

/** Inner fallback UI with navigation (needs router context). */
function SectionCrashFallback({
  name,
  message,
  onRetry,
}: {
  name: string;
  message?: string;
  onRetry: () => void;
}) {
  const navigate = useNavigate();
  const truncated = message && message.length > 200 ? message.slice(0, 200) + '…' : message;

  return (
    <div className="grid h-full place-items-center p-6" data-testid={`section-crash-${name.toLowerCase()}`}>
      <div className="max-w-md text-center">
        <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-destructive/10">
          <span className="text-lg">⚠️</span>
        </div>
        <h2 className="text-base font-semibold">{name} encountered an error</h2>
        {truncated && (
          <p className="mt-2 rounded-md bg-muted p-2 text-xs text-muted-foreground font-mono">
            {truncated}
          </p>
        )}
        <div className="mt-4 flex items-center justify-center gap-2">
          <Button size="sm" onClick={onRetry}>
            Retry
          </Button>
          <Button size="sm" variant="ghost" onClick={() => { void navigate('/'); }}>
            Go to Chat
          </Button>
        </div>
      </div>
    </div>
  );
}
