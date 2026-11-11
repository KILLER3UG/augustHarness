#!/usr/bin/env node
/**
 * check-http-clients.mjs — every httpx client builds through http_env.
 *
 * `backend-py/app/services/http_env.py` is the one place that constructs an
 * httpx client. It exists because httpx parses every NO_PROXY entry at
 * CONSTRUCTION, so a bracketed IPv6 in a Windows bypass list ("[::1]")
 * raises InvalidURL before any request — and one ad-hoc construction site
 * meant one more feature a hostile environment could take offline. The
 * 2026-11 sweep moved 27 constructions in 14 files onto the door; this gate
 * keeps the sweep true. A new `httpx.AsyncClient(` / `httpx.Client(` in any
 * Python under backend-py (app or scripts) reintroduces the bug class
 * silently — the environment is only hostile on the machines that have it.
 *
 * Only http_env.py itself may construct. Comment lines are skipped.
 *
 * Exit 1 listing violations. Fails on an empty census: a gate that passes
 * on zero scanned files is not a gate.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = fileURLToPath(new URL('..', import.meta.url));
const pyRoot = join(repoRoot, 'backend-py');
const allowed = (f) => f.replaceAll(sep, '/').endsWith('app/services/http_env.py');

// Both constructors, direct. `httpx.client` and other helpers do not parse
// NO_PROXY at construction and are not the failure mode.
const CONSTRUCTION = /\bhttpx\.(?:Async)?Client\s*\(/;

function listPythonFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (entry === '__pycache__' || entry === '.venv') continue;
      out.push(...listPythonFiles(full));
    } else if (entry.endsWith('.py')) {
      out.push(full);
    }
  }
  return out;
}

const files = listPythonFiles(pyRoot).filter((f) => !allowed(f));
if (files.length === 0) {
  console.error('check-http-clients: EMPTY CENSUS — no .py files found under backend-py/');
  process.exit(1);
}

const violations = [];
for (const file of files) {
  const lines = readFileSync(file, 'utf8').split(/\r?\n/);
  lines.forEach((line, i) => {
    const stripped = line.trim();
    if (!stripped || stripped.startsWith('#')) return;
    if (CONSTRUCTION.test(stripped)) {
      violations.push(
        `${relative(repoRoot, file).split(sep).join('/')}:${i + 1}: ${stripped}`,
      );
    }
  });
}

console.log(`check-http-clients: scanned ${files.length} files under backend-py/`);
if (violations.length > 0) {
  console.error(
    `check-http-clients: ${violations.length} violation(s) — every httpx client builds through app/services/http_env.py:`,
  );
  for (const v of violations) console.error(`  ${v}`);
  console.error(
    '  A direct construction parses NO_PROXY at build time; one broken bypass list',
    'takes the feature offline before any request. Use http_env.client()/client_sync().',
  );
  process.exit(1);
}
console.log('check-http-clients: OK — every client builds through the NO_PROXY-safe door');
