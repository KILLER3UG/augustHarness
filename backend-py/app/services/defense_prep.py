"""Defense prep for the Research Paper Studio: slides and practice questions.

The spec's writing-and-export item is "Defense prep with slides and practice
Q&A". Both are derived from what the paper actually says — the slides from the
chapter drafts, the practice questions from the traceability matrix — so the
defense reflects the paper rather than a generic template.

The slides are a .pptx written to ``exports/`` (python-pptx is already a
dependency for the same reason). The practice questions are written to
``drafts/defense-qa.md``: one per traceability row ("how did you address this
objective?"), plus the standard methodology/limitations/next-steps questions.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pptx import Presentation

logger = logging.getLogger(__name__)

_MAX_BULLETS = 6
_MAX_BULLET_CHARS = 150


def _read(root: Path, rel: str) -> str:
    p = root / rel
    if p.is_file():
        try:
            return p.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return ''
    return ''

def _title(root: Path) -> str:
    state = _read(root, 'paper-state.md')
    for line in state.splitlines():
        if line.startswith('Topic:'):
            return line.split(':', 1)[1].strip()
    for line in state.splitlines():
        if line.startswith('# ') and 'paper state' not in line.lower():
            return line[2:].strip()
    return root.name

def _clean(line: str) -> str:
    """Strip markdown emphasis and hard wraps from a line of a draft."""
    line = re.sub(r'\*\*(.+?)\*\*', r'\1', line)
    line = re.sub(r'\*(.+?)\*', r'\1', line)
    return line.strip()


def _sentences(text: str) -> list[str]:
    """Sentences of a block of text, blank lines and headings removed."""
    body = '\n'.join(line for line in text.splitlines() if not line.strip().startswith('#'))
    body = re.sub(r'\s*\n\s*', ' ', body)
    out = []
    for sentence in re.split(r'(?<=[.!?])\s+', body):
        sentence = _clean(sentence)
        if len(sentence) > 25:
            out.append(sentence)
    return out


def _shorten(text: str, cap: int) -> str:
    """Trim to a word boundary under cap, so slide text never cuts mid-word."""
    text = text.strip()
    if len(text) <= cap:
        return text
    cut = text[: cap - 1].rsplit(' ', 1)[0].rstrip(' ,;:.')
    return cut + '…'


def _bullets(text: str, cap: int = _MAX_BULLETS) -> list[str]:
    """Concise bullets from a chapter, cut at sentence boundaries.

    Cutting on the draft's hard-wrapped lines produces mid-sentence fragments,
    so the text is joined, re-split on sentences and trimmed to a usable length.
    """
    out: list[str] = []
    for sentence in _sentences(text):
        bullet = _shorten(sentence, _MAX_BULLET_CHARS)
        if bullet not in out:
            out.append(bullet)
        if len(out) >= cap:
            break
    return out or ['(no content drafted)']

def _matrixRows(root: Path) -> list[list[str]]:
    """Rows of the traceability table: [objective, method, result, conclusion]."""
    text = _read(root, 'traceability.md')
    rows: list[list[str]] = []
    for line in text.splitlines():
        if not line.strip().startswith('|'):
            continue
        stripped = line.strip().strip('|')
        cells = [c.strip() for c in stripped.split('|')]
        if not any(cells) or all(re.fullmatch(r':?-{2,}:?', c) for c in cells if c):
            continue
        if cells and cells[0].lower().startswith('objective'):
            continue  # header
        rows.append(cells)
    return rows

def _buildSlides(root: Path, title: str) -> Path:
    prs = Presentation()

    title_slide = prs.slides.add_slide(prs.slide_layouts[0])
    title_slide.shapes.title.text = title
    title_slide.placeholders[1].text = 'Defense — ' + datetime.now().strftime('%Y-%m-%d')

    def contentSlide(heading: str, bullets: list[str]) -> None:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = heading
        body = slide.placeholders[1].text_frame
        body.text = bullets[0] if bullets else ''
        for bullet in bullets[1:]:
            body.add_paragraph().text = bullet

    chapters = {name: _read(root, f'drafts/{name}') for name in
                ('chapter1.md', 'chapter3.md', 'chapter4.md', 'chapter5.md')}

    objectives = _bullets(chapters['chapter1.md'])
    contentSlide('Objectives', objectives)
    contentSlide('Methodology', _bullets(chapters['chapter3.md']))
    contentSlide('Results', _bullets(chapters['chapter4.md']))
    contentSlide('Conclusions', _bullets(chapters['chapter5.md']))

    # Practice-oriented slides: the committee probes the chain, not just the prose.
    # One question per traceability row, echoing the practice-Q&A phrasing; the
    # objective is trimmed at a word boundary, never mid-word.
    rows = _matrixRows(root)
    qaBullets = []
    for row in rows:
        if len(row) >= 4 and row[0]:
            qaBullets.append(f"{_shorten(row[0], 90)} — how? what did you find? so what?")
    qaBullets.append('What are the limitations of this work?')
    qaBullets.append('Why these sources, and how do you know they are real?')
    contentSlide('Questions to expect', qaBullets[:6])

    exports = root / 'exports'
    exports.mkdir(parents=True, exist_ok=True)
    out = exports / 'defense-slides.pptx'
    prs.save(str(out))
    return out

def _buildQA(root: Path, title: str) -> Path:
    rows = _matrixRows(root)
    lines = ['# Defense practice questions', '', f'Paper: {title}', '']
    lines.append('Answer from the paper, in your own words. If you cannot answer '
                 'one from the drafts, that is a gap to fix before the defense.')
    lines.append('')
    for row in rows:
        if len(row) >= 4 and row[0]:
            lines.append(f"## {row[0]}")
            if len(row) > 1 and row[1]:
                lines.append(f"- **How?** {row[1]}")
            if len(row) > 2 and row[2]:
                lines.append(f"- **What did you find?** {row[2]}")
            if len(row) > 3 and row[3]:
                lines.append(f"- **So what?** {row[3]}")
            lines.append('')

    lines += [
        '## Standing questions',
        '',
        '- Why did you choose this methodology over the alternatives?',
        '- What are the limitations of your work, and how do they affect your conclusions?',
        '- Which of your sources is the weakest, and why do you still rely on it?',
        '- If you had six more months, what would you do next?',
        '',
    ]

    drafts = root / 'drafts'
    drafts.mkdir(parents=True, exist_ok=True)
    out = drafts / 'defense-qa.md'
    out.write_text('\n'.join(lines), encoding='utf-8')
    return out

def prepare_defense(workspace: str | Path) -> dict[str, Any]:
    """Build the defense slide deck and practice questions for a paper workspace.

    Returns a report with both written paths and the number of traceability
    rows the practice questions were derived from, so the caller can confirm
    the defense materials actually reflect the paper.
    """
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f'not a directory: {workspace}')

    title = _title(root)
    slides = _buildSlides(root, title)
    qa = _buildQA(root, title)
    rows = len(_matrixRows(root))

    logger.info('defense prep: slides + %d practice question row(s) for %s', rows, title)
    return {
        'slides': str(slides),
        'practiceQuestions': str(qa),
        'title': title,
        'traceabilityRows': rows,
    }
