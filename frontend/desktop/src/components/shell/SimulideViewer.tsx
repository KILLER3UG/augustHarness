/* SimulideViewer — renders a SimulIDE .sim/.sim1/.sim2 schematic as SVG in
 * the Circuit pane. The backend parses the XML and lays out exactly what the
 * user drew (components at their saved coordinates, wires as saved
 * polylines); this pane draws it and offers the escape hatches (reveal in
 * folder, open in SimulIDE) because running the simulation stays SimulIDE's
 * job. */

import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, FolderOpen, Loader2 } from 'lucide-react';
import { parseSimulide } from '@/api/simulide';
import { Button } from '@/components/ui/button';
import { clearRightDrawerCircuitSim } from '@/components/shell/RightDrawerState';
import { revealInFolder } from '@/lib/tauri-shell';

export function SimulideViewer({ path, sessionId }: { path: string; sessionId?: string | null }) {
  const q = useQuery({
    queryKey: ['simulide', path, sessionId ?? ''],
    queryFn: () => parseSimulide(path, sessionId),
    retry: false,
  });

  if (q.isLoading) {
    return (
      <div className="flex items-center gap-2 p-4 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" /> Reading {path.split(/[/\\]/).pop()}…
      </div>
    );
  }

  if (q.error || !q.data) {
    return (
      <div className="p-4 text-xs text-destructive">
        Could not render the circuit: {q.error instanceof Error ? q.error.message : 'unknown error'}
      </div>
    );
  }

  const d = q.data;

  return (
    <div className="rounded-lg border border-border/60 bg-card/60 p-2.5" data-testid="simulide-viewer">
      <div className="mb-2 flex items-center justify-between gap-2 px-0.5">
        <div className="min-w-0">
          <p className="truncate text-xs font-semibold">{d.name}</p>
          <p className="text-2xs text-muted-foreground">
            SimulIDE {d.version || ''} · {d.componentCount} components · {d.wireCount} wires
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button size="sm" variant="outline" onClick={() => void revealInFolder(path)} title="Reveal in folder">
            <FolderOpen className="size-3" />
          </Button>
          <Button size="sm" variant="outline" onClick={clearRightDrawerCircuitSim} title="Back to session circuits">
            <ArrowLeft className="size-3" /> Back
          </Button>
        </div>
      </div>

      <div
        className="max-h-[60vh] overflow-auto rounded-md border border-border/50 bg-black/40 p-2 [&_svg]:h-auto [&_svg]:w-full [&_svg]:min-w-[480px]"
        // Backend-generated SVG from the user's own parsed file — the same
        // trust level as the artifact PNGs the drawer renders.
        dangerouslySetInnerHTML={{ __html: d.svg }}
      />

      <p className="mt-2 whitespace-pre-wrap px-0.5 text-2xs leading-relaxed text-muted-foreground">
        {d.description}
      </p>
    </div>
  );
}
