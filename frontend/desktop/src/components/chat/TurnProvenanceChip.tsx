/* ── TurnProvenanceChip — what the shell injected for this answer ────── */
/* Audit A6 / #15. A turn's `turnTelemetry` frame names the skills and facts
 * that went into its prompt and the failure families it hit. Without that on
 * screen, a surprising answer has no visible cause: the user sees the model
 * "know" something and can only guess whether it recalled it, was handed a
 * skill, or simply made it up.
 *
 * Reference shape (the Claude pass): ONE quiet bordered card with a chevron
 * header — "Recalled memory ▾" — collapsed by default, whose body lists the
 * injected facts under their own `<key>.md` filenames (the store of record is
 * one markdown file per fact) and the injected skills under their names. A
 * turn that injected nothing renders NOTHING: the backend omits those keys
 * entirely when the lists are empty, so a "0 facts" card would be a claim the
 * shell never made, on every single turn.
 *
 * Failure families are NOT memory — they stay a separate one-line warning
 * under the card rather than borrowing its label.
 */

import { useId, useState } from 'react';
import { AlertTriangle, Brain, ChevronDown, SquareLibrary } from 'lucide-react';
import type { WorkbenchTurnProvenance } from '@/types/workbench';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export function TurnProvenanceChip({ provenance }: { provenance?: WorkbenchTurnProvenance }) {
  const skills = provenance?.skillsInjected ?? [];
  const facts = provenance?.factsInjected ?? [];
  const families = provenance?.errorFamilies ?? [];

  if (skills.length === 0 && facts.length === 0 && families.length === 0) return null;

  return <ProvenanceCard skills={skills} facts={facts} families={families} />;
}

function ProvenanceCard({
  skills,
  facts,
  families,
}: {
  skills: string[];
  facts: string[];
  families: string[];
}) {
  const [open, setOpen] = useState(false);
  const uid = useId();
  const headId = `provenance-head-${uid}`;
  const bodyId = `provenance-body-${uid}`;

  const hasMemory = facts.length > 0;
  const hasSkills = skills.length > 0;
  const label = hasMemory ? 'Recalled memory' : 'Loaded skills';

  return (
    <div className="min-w-0" data-testid="turn-provenance-chip">
      <div className="memory-card" data-open={open} data-slot="memory-card">
        <Button
          variant="bare"
          type="button"
          id={headId}
          className="memory-card-head"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls={bodyId}
        >
          {hasMemory ? (
            <Brain className="size-3.5 shrink-0" aria-hidden />
          ) : (
            <SquareLibrary className="size-3.5 shrink-0" aria-hidden />
          )}
          <strong>{label}</strong>
          <span className="memory-card-count">
            {facts.length > 0 && (
              <span data-testid="provenance-facts">
                {facts.length} fact{facts.length === 1 ? '' : 's'}
              </span>
            )}
            {facts.length > 0 && skills.length > 0 ? ' · ' : ''}
            {skills.length > 0 && (
              <span data-testid="provenance-skills">
                {skills.length} skill{skills.length === 1 ? '' : 's'}
              </span>
            )}
          </span>
          <ChevronDown className="memory-card-chevron size-3" aria-hidden />
        </Button>
        {/* The region stays mounted and `hidden` in the closed state — the
            same pattern ThinkingDisclosure uses, so the trigger advertises a
            real region in BOTH states rather than a body that only exists
            while open. */}
        <div
          className="memory-card-body"
          id={bodyId}
          role="region"
          aria-labelledby={headId}
          hidden={!open}
          data-testid="turn-provenance-detail"
        >
          {hasMemory && (
            <>
              <p className="memory-card-note !mt-0">Injected into this turn&apos;s prompt</p>
              {facts.map((f) => (
                <div key={f} className="memory-card-entry">
                  <span className="memory-card-key">{f}.md</span>
                </div>
              ))}
            </>
          )}
          {hasSkills && (
            <>
              <p className={cn('memory-card-note', hasMemory && '!mt-1.5')}>
                {hasMemory ? 'Skills loaded alongside' : 'Loaded into this turn'}
              </p>
              {skills.map((s) => (
                <div key={s} className="memory-card-entry">
                  <SquareLibrary className="size-3 self-center" aria-hidden />
                  <span className="memory-card-key">{s}</span>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
      {families.length > 0 && (
        <p
          className="mt-1 inline-flex items-center gap-1 text-2xs text-warning/90"
          data-testid="provenance-errors"
        >
          <AlertTriangle className="size-3" aria-hidden />
          {families.length} failure famil{families.length === 1 ? 'y' : 'ies'} hit this turn:{' '}
          <span className="font-mono">{families.join(', ')}</span>
        </p>
      )}
    </div>
  );
}
