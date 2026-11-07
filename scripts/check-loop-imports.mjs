#!/usr/bin/env node
/**
 * check-loop-imports.mjs — enforce the loop/ one-way import rule.
 *
 * Nothing under backend-py/app/services/workbench/loop/ may import the
 * workbench MODULE (app/services/workbench/workbench.py). loop/ files may
 * import sibling submodules (sessions, permissions, tool_guardrails, …) —
 * only the workbench module itself is off-limits, so the turn loop's
 * pieces stay a library the workbench composes rather than a dependency
 * that reaches back into it. (rule source: loop/surface.py docstring;
 * the old rationale referenced a deleted test suite, so the rule is now
 * enforced here instead of being held together by tests that are gone.)
 *
 * Exit 1 listing violations. Fails on an empty census: a gate that
 * passes on zero scanned files is not a gate.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = fileURLToPath(new URL('..', import.meta.url));
const loopDir = join(repoRoot, 'backend-py', 'app', 'services', 'workbench', 'loop');

// Matches an import whose target resolves to the workbench MODULE
// (workbench.py), not a sibling submodule. Grouped by shape:
//   from app.services.workbench.workbench import …
//   import app.services.workbench.workbench
//   from app.services.workbench import  … workbench …   (name = the module)
//   from . import workbench / from .workbench import … / from ..workbench import …
const VIOLATION_PATTERNS = [
  /from\s+app\.services\.workbench\.workbench\s+import\b/,
  /import\s+app\.services\.workbench\.workbench\b/,
  /from\s+app\.services\.workbench\s+import\s+[^#\n]*\bworkbench\b/,
  /from\s+\.{1,2}\s+import\s+[^#\n]*\bworkbench\b/,
  /from\s+\.{1,2}workbench\s+import\b/,
];

function listPythonFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (entry === '__pycache__') continue;
      out.push(...listPythonFiles(full));
    } else if (entry.endsWith('.py')) {
      out.push(full);
    }
  }
  return out;
}

const files = listPythonFiles(loopDir);
if (files.length === 0) {
  console.error('check-loop-imports: EMPTY CENSUS — no .py files found under loop/');
  process.exit(1);
}

const violations = [];
for (const file of files) {
  const lines = readFileSync(file, 'utf8').split(/\r?\n/);
  lines.forEach((line, i) => {
    const stripped = line.trim();
    if (!stripped || stripped.startsWith('#')) return;
    for (const pattern of VIOLATION_PATTERNS) {
      if (pattern.test(stripped)) {
        violations.push(
          `${relative(repoRoot, file).split(sep).join('/')}:${i + 1}: ${stripped}`,
        );
        break;
      }
    }
  });
}

console.log(`check-loop-imports: scanned ${files.length} files under loop/`);
if (violations.length > 0) {
  console.error(`check-loop-imports: ${violations.length} violation(s) — loop/ must not import the workbench module:`);
  for (const v of violations) console.error(`  ${v}`);
  process.exit(1);
}
console.log('check-loop-imports: OK — one-way import rule holds');
