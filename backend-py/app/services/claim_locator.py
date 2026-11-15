"""Locate the source and passage behind a claim — "show me where this came from".

The Research Paper Studio's integrity rules make the agent cite real sources,
but a citation only points at a paper. It does not show the user WHERE in their
library a specific claim came from. This module is the locator: given a claim,
it finds the source records in the paper's library that best match it and pulls
out the matching passage, so the user can check the claim by hand.

It is a RETRIEVAL aid, not a fact-checker. It shows which source a claim's
wording came from; it cannot judge whether that source actually supports the
claim. That reading stays with the user — which is the point.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_MIN_TOKEN_LEN = 4

_STOPWORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from', 'in',
    'is', 'it', 'its', 'of', 'on', 'or', 'that', 'the', 'their', 'this',
    'to', 'was', 'were', 'with',
}


def _tokens(text: str) -> set[str]:
    """Distinctive lowercase words, skipping stopwords, numbers and short words."""
    words = re.findall(r'[a-z0-9]+', (text or '').lower())
    return {
        w for w in words
        if len(w) >= _MIN_TOKEN_LEN and w not in _STOPWORDS and not w.isdigit()
    }


def _passages(text: str) -> list[str]:
    """Split a source record into passages: paragraphs, then sentences.

    A paragraph is the unit a reader checks against, but a long paragraph can
    cover several unrelated claims, so its sentences are offered too. The
    matcher scores both and keeps the best, which is what "show me the
    passage" means in practice.
    """
    passages: list[str] = []
    for paragraph in re.split(r'\n\s*\n', text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        passages.append(paragraph)
        for sentence in re.split(r'(?<=[.!?])\s+', paragraph):
            sentence = sentence.strip()
            if len(sentence) > 40:
                passages.append(sentence)
    return passages


def _extractCitationBlock(text: str) -> str:
    """The citation block of a source record, for display beside the passage."""
    match = re.search(r'Citation[^\n]*:\n((?:\s+[^\n]*\n?)+)', text)
    if match:
        return match.group(1).strip()
    return ''


def locate_claim(claim: str, workspace: str | Path, limit: int = 3) -> dict[str, Any]:
    """Find the library records and passages that best match a claim.

    Returns the top ``limit`` matches, each with the source file, its resolved
    citation block, the matching passage and a ``score`` — the fraction of the
    claim's distinctive words that the passage accounts for. A low score means
    the library does not contain the claim's wording, which is itself the
    answer: the claim is not grounded in this library and needs a source.
    """
    claim_text = (claim or '').strip()
    root = Path(workspace).expanduser().resolve()
    if not claim_text or not root.is_dir():
        return {'claim': claim_text, 'matches': [], 'error': 'empty claim or missing workspace'}

    want = _tokens(claim_text)
    sources_dir = root / 'sources'
    files = sorted(sources_dir.glob('*.md')) if sources_dir.is_dir() else []
    if not files:
        return {
            'claim': claim_text,
            'matches': [],
            'error': 'no source library found (expected a sources/ folder with one record per source)',
        }

    matches: list[dict[str, Any]] = []
    for file in files:
        try:
            text = file.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        citation = _extractCitationBlock(text)
        best_for_file: dict[str, Any] | None = None
        for passage in _passages(text):
            have = _tokens(passage)
            if not have:
                continue
            score = len(want & have) / len(want) if want else 0.0
            if best_for_file is None or score > best_for_file['score']:
                best_for_file = {
                    'file': str(file.relative_to(root)),
                    'score': round(score, 3),
                    'passage': passage[:400],
                    'citation': citation,
                }
        if best_for_file is not None and best_for_file['score'] > 0:
            matches.append(best_for_file)

    matches.sort(key=lambda m: m['score'], reverse=True)
    for m in matches:
        m['grounded'] = m['score'] >= 0.5
    return {
        'claim': claim_text,
        'matches': matches[:limit],
        'note': (
            'A match shows where the claim\'s wording comes from in this library. '
            'It does not prove the source supports the claim — read the passage.'
        ),
    }
