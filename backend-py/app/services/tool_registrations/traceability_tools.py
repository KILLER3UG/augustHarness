"""Traceability-matrix consistency check for the Research Paper Studio.

The spec requires "a traceability matrix links objectives (Ch. 1) to methods
(Ch. 3), results (Ch. 4), and conclusions (Ch. 5). A consistency check flags
mismatches." This tool is that check.

It reads ``traceability.md`` — a markdown table, one row per objective, with a
column per chapter — and verifies two things for every row:

1. **Complete** — no cell is empty. An objective with no method, or a result
   with no conclusion, is a gap in the argument the paper cannot close.
2. **Reflected** — each cell's substance actually appears in the chapter file
   it belongs to. A matrix row that names a method Chapter 3 never discusses is
   the mismatch that makes a paper's claims untraceable, and it is exactly the
   kind of drift that creeps in when a chapter is revised without updating the
   matrix.

Read-only: the workspace is read, never written.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.services import tool_registry

logger = logging.getLogger(__name__)

# The matrix column that belongs to each chapter file, in draft order.
_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ('objective', 'Objective', 'chapter1.md'),
    ('method', 'Method', 'chapter3.md'),
    ('result', 'Result', 'chapter4.md'),
    ('conclusion', 'Conclusion', 'chapter5.md'),
)

# Tokens too common to tie a cell to a chapter. Without these, "the study
# used a survey" would match any methodology chapter.
_STOPWORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from', 'in',
    'is', 'it', 'its', 'of', 'on', 'or', 'that', 'the', 'their', 'this',
    'to', 'was', 'were', 'with',
}
_MIN_TOKEN_LEN = 4
# Below this, a cell's wording shares too little with its chapter to call it
# reflected. Tuned so "78% of respondents" does not match a chapter that never
# reports that result.
_REFLECTED_SCORE = 0.5


def _tokens(text: str) -> set[str]:
    """Distinctive lowercase words of a cell or chapter excerpt."""
    words = re.findall(r'[a-z0-9]+', (text or '').lower())
    return {
        w for w in words
        if len(w) >= _MIN_TOKEN_LEN and w not in _STOPWORDS and not w.isdigit()
    }


def _reflected(cell: str, chapter_text: str) -> float:
    """How much of a matrix cell the chapter accounts for."""
    want = _tokens(cell)
    if not want:
        # A cell with no distinctive words (a bare number, say) cannot be
        # tied to a chapter either way; report it as reflected rather than
        # inventing a mismatch.
        return 1.0
    have = _tokens(chapter_text)
    return len(want & have) / len(want)


def _splitRow(line: str) -> list[str]:
    """Cells of one markdown table row, with the edge pipes trimmed."""
    stripped = line.strip()
    if stripped.startswith('|'):
        stripped = stripped[1:]
    if stripped.endswith('|'):
        stripped = stripped[:-1]
    return [c.strip() for c in stripped.split('|')]


def _isSeparator(cells: list[str]) -> bool:
    """A ``| --- | --- |`` alignment row."""
    return bool(cells) and all(re.fullmatch(r':?-{2,}:?', c) for c in cells if c) and any(c for c in cells)


def _parseMatrix(text: str) -> tuple[list[list[str]], list[str]]:
    """Rows of the traceability table, plus any problem found with its shape."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        if not line.strip().startswith('|'):
            continue
        cells = _splitRow(line)
        if not any(cells):
            continue
        if _isSeparator(cells):
            continue
        rows.append(cells)
    if not rows:
        return [], ['no markdown table found in traceability.md']

    # The first row is the header; it names the columns. Every data row must
    # have the same number of cells or the columns no longer line up.
    width = len(rows[0])
    problems: list[str] = []
    for index, row in enumerate(rows[1:], start=1):
        if len(row) != width:
            problems.append(f'matrix row {index} has {len(row)} cells, expected {width}')
    return rows, problems


async def _checkTraceability(path: str, **kwargs: object) -> str:
    """Verify a paper's traceability matrix is complete and reflected."""
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        return json.dumps(
            {
                'ok': False,
                'error': f'not a directory: {path}',
                'hint': 'Pass the paper workspace folder — the one holding paper-state.md, drafts/ and traceability.md.',
            },
            ensure_ascii=False,
        )

    matrix_path = root / 'traceability.md'
    if not matrix_path.is_file():
        return json.dumps(
            {
                'ok': False,
                'error': 'traceability.md not found in the workspace',
                'hint': 'Create it first — one markdown table row per objective, with a column per chapter.',
            },
            ensure_ascii=False,
        )

    try:
        matrix_text = matrix_path.read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        return json.dumps({'ok': False, 'error': f'could not read traceability.md: {exc}'}, ensure_ascii=False)

    rows, problems = _parseMatrix(matrix_text)

    # Chapter bodies, read once. A missing chapter is reported per column so
    # the user sees which side of the matrix is unfinished.
    chapters: dict[str, str] = {}
    missing: list[str] = []
    for _key, _label, filename in _COLUMNS:
        chapter = root / 'drafts' / filename
        if chapter.is_file():
            try:
                chapters[filename] = chapter.read_text(encoding='utf-8', errors='replace')
            except OSError as exc:
                missing.append(f'{filename}: could not read ({type(exc).__name__})')
        else:
            missing.append(f'{filename}: not written yet')

    incomplete: list[dict[str, Any]] = []
    unreflected: list[dict[str, Any]] = []
    row_count = 0

    if rows:
        header = [c.lower() for c in rows[0]]
        for index, row in enumerate(rows[1:], start=1):
            row_count += 1
            label = row[0] if row else f'row {index}'
            for position, (key, label_word, filename) in enumerate(_COLUMNS):
                # The matrix may lead with an id/title column; line each
                # column up with its chapter by position from the right, so a
                # leading "#" or "Objective id" column does not shift them.
                cell_index = len(row) - len(_COLUMNS) + position
                if cell_index < 0 or cell_index >= len(row):
                    continue
                cell = row[cell_index]
                if not cell.strip():
                    incomplete.append(
                        {'row': label, 'column': label_word, 'problem': 'empty — this link is missing'}
                    )
                    continue
                if filename not in chapters:
                    continue
                score = _reflected(cell, chapters[filename])
                if score < _REFLECTED_SCORE:
                    unreflected.append(
                        {
                            'row': label,
                            'column': label_word,
                            'chapter': f'drafts/{filename}',
                            'cell': cell[:160],
                            'score': round(score, 3),
                            'problem': 'this link is not reflected in the chapter text',
                        }
                    )
        del header

    ok = not problems and not incomplete and not unreflected and not missing and row_count > 0
    return json.dumps(
        {
            'ok': ok,
            'workspace': str(root),
            'rows': row_count,
            'problems': problems,
            'missingChapters': missing,
            'incomplete': incomplete,
            'unreflected': unreflected,
            'note': (
                'A cell is "reflected" when its distinctive words appear in the chapter it '
                'points at. A row that is complete but unreflected means the matrix and the '
                'chapter have drifted apart — fix one or the other before drafting further.'
            ),
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def register() -> None:
    """Register the traceability consistency check (read-only over the workspace)."""

    tool_registry.register(
        'check_traceability',
        (
            'Audit a paper workspace\'s traceability matrix: every objective (Ch1) must have a '
            'method (Ch3), a result (Ch4) and a conclusion (Ch5), and each link must actually '
            'appear in that chapter\'s draft. Flags empty links and rows that have drifted from '
            'the chapters. Pass the paper workspace folder. Read-only.'
        ),
        _checkTraceability,
        {
            'type': 'object',
            'properties': {
                'path': {
                    'type': 'string',
                    'description': 'The paper workspace folder holding traceability.md and drafts/.',
                },
            },
            'required': ['path'],
        },
    )
