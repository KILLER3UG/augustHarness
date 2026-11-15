"""Export a Research Paper Studio workspace to a formatted ``.docx``.

The spec's v1 target is "export to a formatted docx only" (LaTeX is deferred).
The paper already lives as markdown — ``paper-state.md``, ``drafts/chapter1-5.md``,
``abstract.md``, ``references.md``, ``appendices.md`` — so the export is a
faithful markdown→Word conversion of what was actually drafted, in the order a
paper is read. Nothing is re-flowed or rewritten: the .docx is the drafts, formatted.

Deliberately out of scope: page-number-per-section cross-references, floating
figure placement and citation-style reformatting. Those are the parts of Word
formatting that silently break, and v1 ships the reliable core instead.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from docx import Document
from docx.shared import Pt

logger = logging.getLogger(__name__)

# The document order a paper is read in. Only the files that exist are
# exported, so a paper mid-way through still exports cleanly.
_CHAPTER_FILES: tuple[str, ...] = (
    'abstract.md',
    'chapter1.md',
    'chapter2.md',
    'chapter3.md',
    'chapter4.md',
    'chapter5.md',
    'references.md',
    'appendices.md',
)

_HEADING_RE = re.compile(r'^(#{1,4})\s+(.*)$')
_BULLET_RE = re.compile(r'^[-*]\s+(.*)$')
_NUMBERED_RE = re.compile(r'^\d+[.)]\s+(.*)$')
# The title lives in paper-state.md as "Topic: ..." or as a first heading.
_TOPIC_RE = re.compile(r'^Topic:\s*(.+)$', re.MULTILINE)


def _slug(text: str) -> str:
    """A filename-safe slug of the paper title."""
    words = re.findall(r'[a-zA-Z0-9]+', (text or '').lower())
    return '-'.join(words[:8]) or 'paper'


def _paperTitle(root: Path) -> str:
    """The paper title from paper-state.md, or the folder name."""
    state = root / 'paper-state.md'
    if state.is_file():
        try:
            text = state.read_text(encoding='utf-8', errors='replace')
        except OSError:
            text = ''
        for line in text.splitlines():
            match = _TOPIC_RE.match(line.strip())
            if match:
                return match.group(1).strip()
        for line in text.splitlines():
            if line.startswith('# ') and line.strip() != '# Paper state':
                return line[2:].strip()
    return root.name


def _addRuns(paragraph: Any, text: str) -> None:
    """Add ``text`` to a paragraph, honouring **bold** and *italic* inline marks.

    A deliberately small inline grammar — bold, italic and plain — because that
    is what chapter drafts actually use. Anything else (links, footnotes,
    tables) falls through as plain text rather than being mangled.
    """
    # Split on **bold** first, then *italic* inside the plain runs.
    for bold_part in re.split(r'\*\*(.+?)\*\*', text):
        is_bold = text.count(f'**{bold_part}**') > 0 and bold_part != text
        for italic_part in re.split(r'\*(.+?)\*', bold_part):
            run = paragraph.add_run(italic_part)
            run.bold = is_bold or None
            # A segment wrapped in a single pair of asterisks is italic.
            if not is_bold and f'*{italic_part}*' in text and italic_part:
                run.italic = True


def _addMarkdown(document: Any, markdown: str) -> None:
    """Convert one markdown file's body into document elements."""
    inCode = False
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()

        if line.strip().startswith('```'):
            inCode = not inCode
            continue
        if inCode:
            # Code lines keep their literal spacing in a monospace paragraph.
            p = document.add_paragraph()
            run = p.add_run(raw_line)
            run.font.name = 'Consolas'
            run.font.size = Pt(9)
            continue

        if not line.strip():
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            document.add_heading(heading.group(2).strip(), level=len(heading.group(1)))
            continue

        bullet = _BULLET_RE.match(line.strip())
        if bullet:
            p = document.add_paragraph(style='List Bullet')
            _addRuns(p, bullet.group(1))
            continue

        numbered = _NUMBERED_RE.match(line.strip())
        if numbered:
            p = document.add_paragraph(style='List Number')
            _addRuns(p, numbered.group(1))
            continue

        p = document.add_paragraph()
        _addRuns(p, line.strip())


def export_paper_docx(workspace: str | Path) -> dict[str, Any]:
    """Export a paper workspace to ``exports/<slug>-<date>.docx``.

    Returns a report: the written path, which sections were included, and the
    word count — so the caller can confirm the export matches the drafts
    rather than trusting that a file simply appeared.
    """
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f'not a directory: {workspace}')

    title = _paperTitle(root)
    document = Document()

    # Title page: the paper title, then the date. Keep it to one heading so
    # the chapters' own Heading 1s remain the top level of the body.
    document.add_heading(title, level=0)
    document.add_paragraph(
        datetime.now(timezone.utc).strftime('Exported %Y-%m-%d')
    )

    included: list[str] = []
    skipped: list[str] = []
    word_count = 0
    for name in _CHAPTER_FILES:
        chapter = root / 'drafts' / name
        if not chapter.is_file():
            skipped.append(name)
            continue
        try:
            markdown = chapter.read_text(encoding='utf-8', errors='replace')
        except OSError as exc:
            skipped.append(f'{name} (unreadable: {type(exc).__name__})')
            continue
        _addMarkdown(document, markdown)
        included.append(name)
        word_count += len(markdown.split())

    if not included:
        raise ValueError(
            'no chapter files found under drafts/ — nothing to export. '
            'Expected at least one of: ' + ', '.join(_CHAPTER_FILES)
        )

    exports = root / 'exports'
    exports.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d')
    out = exports / f'{_slug(title)}-{stamp}.docx'
    document.save(str(out))

    logger.info('paper export: wrote %s (%d sections, ~%d words)', out, len(included), word_count)
    return {
        'path': str(out),
        'title': title,
        'sections': included,
        'skipped': skipped,
        'approxWords': word_count,
    }
