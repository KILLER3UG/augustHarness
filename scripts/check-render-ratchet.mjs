#!/usr/bin/env node
/* ── check-render-ratchet.mjs — recurring-work budget, shrink-only ────────
 *
 * Two counters over the desktop renderer that measure background work the
 * app does just by running:
 *
 *   window-keydowns    window/document-level keydown listeners. Each one is
 *                      a global handler every keystroke pays for, and each
 *                      is one more place shortcuts can collide.
 *   interval-timers    setInterval(...) sites. Each is a self-invoking
 *                      periodic job; a component that owns one keeps waking
 *                      the renderer for as long as it is mounted.
 *
 * Like check-design.mjs this is a RATCHET keyed on the counts in
 * render-baseline.json: a number may only go DOWN. A new listener or timer
 * fails the gate; removing some lets you lower the baseline deliberately
 * (edit the JSON in the same commit — the counts are one line each).
 *
 * Usage:  node scripts/check-render-ratchet.mjs [--update]
 */

import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(SCRIPT_DIR, '..');
const SRC = resolve(REPO, 'frontend', 'desktop', 'src');
const BASELINE = join(SCRIPT_DIR, 'render-baseline.json');
const UPDATE = process.argv.includes('--update');

const RULES = [
  {
    id: 'window-keydowns',
    re: /addEventListener\(\s*['"]keydown['"]/g,
    advice:
      'Every window-level keydown handler runs on each keystroke and is one more place\n' +
      '  shortcuts collide. Route through the registry in lib/shortcuts.ts or scope the\n' +
      '  listener to the surface that owns the key.',
  },
  {
    id: 'interval-timers',
    re: /\bsetInterval\(/g,
    advice:
      'A setInterval in a component keeps waking the renderer while mounted. Prefer a\n' +
    '  shared clock (a leaf child re-rendering only the digits) or event-driven updates.',
  },
];

function walkFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) out.push(...walkFiles(full));
    else if (name.endsWith('.ts') || name.endsWith('.tsx')) out.push(full);
  }
  return out;
}

function count() {
  const counts = Object.fromEntries(RULES.map((r) => [r.id, 0]));
  const sites = Object.fromEntries(RULES.map((r) => [r.id, []]));
  for (const file of walkFiles(SRC)) {
    const rel = relative(REPO, file).replace(/\\/g, '/');
    const lines = readFileSync(file, 'utf8').split('\n');
    for (const line of lines) {
      for (const rule of RULES) {
        for (const _m of line.matchAll(new RegExp(rule.re.source, rule.re.flags))) {
          counts[rule.id] += 1;
          sites[rule.id].push(`${rel}:${line.trim()}`);
        }
      }
    }
  }
  return { counts, sites };
}

const { counts, sites } = count();

if (UPDATE) {
  writeFileSync(BASELINE, JSON.stringify(counts, null, 2) + '\n');
  console.log(
    `[check-render-ratchet] baseline updated — ${RULES.map((r) => `${r.id}=${counts[r.id]}`).join(' ')}`,
  );
  process.exit(0);
}

const baseline = existsSync(BASELINE)
  ? JSON.parse(readFileSync(BASELINE, 'utf8'))
  : null;
if (!baseline) {
  console.error(
    '[check-render-ratchet] no baseline — run `node scripts/check-render-ratchet.mjs --update` once, deliberately.',
  );
  process.exit(1);
}

const grown = RULES.filter((r) => counts[r.id] > (baseline[r.id] ?? 0));
if (grown.length === 0) {
  console.log(
    `[check-render-ratchet] ok — ${RULES.map((r) => `${r.id}=${counts[r.id]}/${baseline[r.id]}`).join(' ')} (current/baseline)`,
  );
  process.exit(0);
}

console.error('[check-render-ratchet] FAIL — recurring-work budget grew:\n');
for (const rule of grown) {
  console.error(`  ${rule.id}: ${counts[rule.id]} > baseline ${baseline[rule.id]}`);
  for (const s of sites[rule.id].slice(0, 15)) console.error(`    ${s}`);
  if (sites[rule.id].length > 15) console.error(`    … and ${sites[rule.id].length - 15} more`);
  console.error(`  ${rule.advice}\n`);
}
console.error('Lower the baseline only by deleting recurring work, never to silence a finding.');
process.exit(1);
