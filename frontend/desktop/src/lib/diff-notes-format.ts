/* Deterministic, quote-safe serialization of review notes into the one
 * queued message the agent reads. Every surface that packages notes goes
 * through here, so the format cannot drift between the drawer, the
 * changes card, and anything added later.
 *
 * Shape (one note, 3 lines; a multi-note message joins them with a blank
 * line):
 *   File: <path>
 *   Lines: A-B   |   Line: N   |   Scope: file   (lineNumber 0)
 *   User comment: "<body, \ " CR LF escaped>"
 */

export interface ReviewNoteLike {
  filePath: string;
  /** 0 means a whole-file note. */
  lineNumber: number;
  startLine?: number;
  body: string;
}

function escapeNoteBody(body: string): string {
  return body
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/\r/g, '\\r')
    .replace(/\n/g, '\\n');
}

export function formatReviewNote(note: ReviewNoteLike): string {
  const locationLabel =
    note.lineNumber === 0
      ? 'Scope: file'
      : note.startLine !== undefined && note.startLine !== note.lineNumber
        ? `Lines: ${note.startLine}-${note.lineNumber}`
        : `Line: ${note.lineNumber}`;
  return [
    `File: ${note.filePath}`,
    locationLabel,
    `User comment: "${escapeNoteBody(note.body)}"`,
  ].join('\n');
}

/** Sort by path then line so a multi-file send reads in file order
 * regardless of the order the user wrote the notes in. */
export function sortNotesForSend<T extends ReviewNoteLike>(notes: readonly T[]): T[] {
  return [...notes].sort(
    (a, b) =>
      a.filePath.localeCompare(b.filePath) || a.lineNumber - b.lineNumber,
  );
}

export function formatReviewNotes(notes: readonly ReviewNoteLike[]): string {
  return sortNotesForSend(notes)
    .map(formatReviewNote)
    .join('\n\n');
}
