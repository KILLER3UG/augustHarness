/* SimulideViewer — renders AND RUNS a SimulIDE .sim/.sim1/.sim2 circuit.
 *
 * The backend parses the XML, lays out exactly what the user drew, and runs
 * August's built-in digital simulation: every Fixed Voltage in the file is
 * a toggle here, and each toggle re-simulates and re-renders — wires light
 * amber for logic high, the seven-segment shows the composed digit. Analog
 * parts and MCUs stay honestly unsimulated (the notes say so).
 */

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, FolderOpen, Loader2 } from 'lucide-react';
import { parseSimulide } from '@/api/simulide';
import { Button } from '@/components/ui/button';
import { clearRightDrawerCircuitSim } from '@/components/shell/RightDrawerState';
import { revealInFolder } from '@/lib/tauri-shell';

export function SimulideViewer({ path, sessionId }: { path: string; sessionId?: string | null }) {
  // Inputs are the user's gestures: every source defaults OFF (SimulIDE
  // saves don't carry runtime toggle state either).
  const [inputs, setInputs] = useState<Record<string, boolean>>({});

  const q = useQuery({
    queryKey: ['simulide', path, sessionId ?? '', JSON.stringify(inputs)],
    queryFn: () => parseSimulide(path, sessionId, inputs),
    retry: false,
    placeholderData: (prev) => prev,
  });

  const sortedInputs = useMemo(() => {
    const list = q.data?.inputComponents ?? [];
    return [...list].sort((a, b) => a.label.localeCompare(b.label));
  }, [q.data]);

  if (q.isLoading && !q.data) {
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
          <p className="truncate text-xs font-semibold">
            {d.name}
            {d.display !== null && (
              <span className="ml-2 rounded bg-muted/60 px-1.5 py-0.5 font-mono text-2xs text-foreground">
                display: {d.display}
              </span>
            )}
          </p>
          <p className="text-2xs text-muted-foreground">
            SimulIDE {d.version || ''} · {d.componentCount} components · {d.wireCount} wires
            {!d.settled && ' · did not settle'}
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

      {sortedInputs.length > 0 && (
        <div className="mb-2 rounded-md border border-border/50 bg-black/10 p-2">
          <p className="mb-1.5 text-2xs text-muted-foreground">
            Inputs — click to toggle a source; the circuit re-simulates instantly
          </p>
          <div className="flex flex-wrap gap-1.5">
            {sortedInputs.map((inp) => (
              <Button
                key={inp.id}
                size="sm"
                variant={inputs[inp.id] ? 'default' : 'outline'}
                onClick={() => setInputs((prev) => ({ ...prev, [inp.id]: !prev[inp.id] }))}
                className="font-mono text-2xs h-7 px-2"
                aria-pressed={Boolean(inputs[inp.id])}
              >
                {inp.label}
              </Button>
            ))}
          </div>
        </div>
      )}

      <div
        className="max-h-[60vh] overflow-auto rounded-md border border-border/50 bg-black/40 p-2 [&_svg]:h-auto [&_svg]:w-full [&_svg]:min-w-[480px]"
        // Backend-generated SVG from the user's own parsed file — the same
        // trust level as the artifact PNGs the drawer renders.
        dangerouslySetInnerHTML={{ __html: d.svg }}
      />

      {d.notes.length > 0 && (
        <ul className="mt-2 space-y-0.5 px-0.5">
          {d.notes.map((n) => (
            <li key={n} className="text-2xs text-muted-foreground">
              · {n}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-1 whitespace-pre-wrap px-0.5 text-2xs leading-relaxed text-muted-foreground">
        {d.description}
      </p>
    </div>
  );
}
