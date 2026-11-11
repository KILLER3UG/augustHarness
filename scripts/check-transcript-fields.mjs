#!/usr/bin/env node
/**
 * check-transcript-fields.mjs — the structured-field allow-lists stay in step.
 *
 * The backend persists UI-shaped fields inside `messages.blocks_json`
 * (STRUCTURED_FIELDS in app/services/memory_store/transcript_blocks.py), but
 * a restored message only receives the fields the desktop's two hand-written
 * allow-lists name: the restore list (session-history.ts) and the sync list
 * (transcript-sync.ts). `rating` shipped as a stored verdict the client never
 * restored — live writer, dead reader — because the lists drifted from the
 * backend tuple by hand. This gate closes that drift: every backend field
 * must be restored, synced, or recorded as deliberately excluded, and a
 * field the client restores must also be synced (a verdict written live and
 * dropped on the trip back is the exact rating bug).
 *
 * The backend tuple is parsed, not re-implemented, with sanity floors so a
 * refactor that defeats the parser fails the gate instead of passing
 * vacuously — a rule that measures nothing is always green.
 *
 * Exit 1 on drift. Deliberate exclusions live in
 * scripts/transcript-fields.pending.json with a reason each.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const repoRoot = fileURLToPath(new URL('..', import.meta.url));
const MANIFEST = resolve(repoRoot, 'scripts', 'transcript-fields.pending.json');

/** Pull the string literals of a `... = ( ... )` or `... = [ ... ]` list,
 *  with comments stripped first — a quoted word inside a comment is prose,
 *  not a field ('up' / 'down' in the rating comment is the live example). */
function extractList(file, anchor, closer, commentRe) {
  const text = readFileSync(file, 'utf8');
  const start = text.indexOf(anchor);
  if (start === -1) return null;
  const [openCh, closeCh] = closer === ')' ? ['(', ')'] : ['[', ']'];
  const begin = text.indexOf(openCh, start);
  if (begin === -1) return null;
  let depth = 0;
  for (let i = begin; i < text.length; i += 1) {
    if (text[i] === openCh) depth += 1;
    else if (text[i] === closeCh) {
      depth -= 1;
      if (depth === 0) {
        const body = text.slice(begin, i + 1).replace(commentRe, '$1');
        return [...body.matchAll(/['"]([A-Za-z0-9_]+)['"]/g)].map((m) => m[1]);
      }
    }
  }
  return null;
}

const backendFile = resolve(
  repoRoot,
  'backend-py',
  'app',
  'services',
  'memory_store',
  'transcript_blocks.py',
);
const restoreFile = resolve(
  repoRoot,
  'frontend',
  'desktop',
  'src',
  'sections',
  'chat',
  'stream',
  'session-history.ts',
);
const syncFile = resolve(
  repoRoot,
  'frontend',
  'desktop',
  'src',
  'sections',
  'chat',
  'stream',
  'transcript-sync.ts',
);

const backend = extractList(backendFile, 'STRUCTURED_FIELDS: tuple[str, ...] =', ')', /(^|\s)#[^\n]*/g);
const restore = extractList(restoreFile, 'const STRUCTURED_FIELDS = [', ']', /(^|\s)\/\/[^\n]*/g);
const sync = extractList(syncFile, 'const STRUCTURED_FIELDS = [', ']', /(^|\s)\/\/[^\n]*/g);

const errors = [];
// Sanity floors: a parser defeated by a refactor must fail loudly, not
// measure nothing. 'rating' and 'blocks' are old, load-bearing members.
for (const [name, list, floor, mustHave] of [
  ['backend STRUCTURED_FIELDS', backend, 15, ['rating', 'blocks']],
  ['restore list', restore, 15, ['rating', 'toolUseId']],
  ['sync list', sync, 15, ['rating', 'toolUseId']],
]) {
  if (!list) {
    errors.push(`${name}: could not parse the list — was its shape changed?`);
    continue;
  }
  if (list.length < floor) {
    errors.push(`${name}: parsed ${list.length} entries, expected at least ${floor}.`);
  }
  for (const k of mustHave) {
    if (!list.includes(k)) errors.push(`${name}: '${k}' is missing — a load-bearing field dropped out.`);
  }
}
if (errors.length > 0) {
  for (const e of errors) console.error(`FAIL  ${e}`);
  process.exit(1);
}

let manifest = { excluded: {} };
try {
  manifest = JSON.parse(readFileSync(MANIFEST, 'utf8'));
} catch (err) {
  console.error(`[check-transcript-fields] ${MANIFEST} is not valid JSON: ${err.message}`);
  process.exit(1);
}
const excluded = manifest.excluded ?? {};

const backendSet = new Set(backend);
const restoreSet = new Set(restore);
const syncSet = new Set(sync);

// a) every backend field is restored, synced, or deliberately excluded
for (const field of backend) {
  if (restoreSet.has(field) || syncSet.has(field) || excluded[field]) continue;
  errors.push(
    `backend persists '${field}' in blocks_json but the desktop neither restores it ` +
      '(session-history.ts) nor syncs it (transcript-sync.ts) — a live writer with a ' +
      'dead reader. Add it to the lists, or record the exclusion with a reason in ' +
      'scripts/transcript-fields.pending.json.',
  );
}
// b) a field the client restores must also be synced — the rating bug
for (const field of restore) {
  if (syncSet.has(field) || excluded[field]) continue;
  errors.push(
    `'${field}' is restored (session-history.ts) but never synced (transcript-sync.ts): ` +
      'a value written live is dropped when the transcript comes back.',
  );
}
// c) the exclusion list stays an exception list, not a rubbish bin
for (const field of Object.keys(excluded)) {
  if (!backendSet.has(field)) {
    errors.push(
      `${MANIFEST} excludes '${field}', which the backend no longer persists — remove the entry.`,
    );
  }
  if (restoreSet.has(field) || syncSet.has(field)) {
    errors.push(
      `${MANIFEST} excludes '${field}' but a client list carries it now — remove the entry.`,
    );
  }
}

if (errors.length > 0) {
  for (const e of errors) console.error(`FAIL  ${e}`);
  console.error(
    `\n${errors.length} transcript-field drift problem(s). The backend tuple and the two ` +
      'client allow-lists are one contract held by hand; the gate holds it instead.',
  );
  process.exit(1);
}

console.log(
  `check-transcript-fields: OK — ${backend.length} backend field(s), ${restore.length} restored, ` +
    `${sync.length} synced, ${Object.keys(excluded).length} deliberately excluded.`,
);
