"""Citation-integrity tools for the Research Paper Studio.

The spec's first integrity rule is "never fabricate a reference", and the
Phase 3 acceptance is "a check that every citation exists". These tools are
that check:

* ``verify_citation`` — resolve ONE citation against Crossref, then OpenAlex,
  then arXiv and say whether it is real. Used while drafting, before a
  reference is written into a chapter.
* ``check_paper_citations`` — walk a paper workspace and verify every
  citation identifier it finds, so a finished draft can be audited in one call
  instead of trusting that each citation was checked as it was written.

Both are read-only: over the network (the public registries) and over the
workspace (files are read, never written).

What the audit can and cannot see: a citation that carries a DOI, an arXiv id
or a URL is checkable. A bare author-year mention with no identifier is NOT —
the audit reports those as "no verifiable identifier" rather than pretending
they were checked, because guessing at a match for a vague mention is exactly
how a fabricated reference gets laundered into a real-looking one.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.services import tool_registry
from app.services.citation_resolver import resolve_citation

logger = logging.getLogger(__name__)

# Verifiable identifiers that can appear inside a citation. A DOI or an arXiv
# id pins one exact work; a doi.org / arxiv.org URL carries the same.
_DOI_RE = re.compile(r'10\.\d{4,9}/[^\s)\]},;]+', re.IGNORECASE)
_ARXIV_RE = re.compile(r'\b(\d{4}\.\d{4,5})(v\d+)?\b')
_DOI_URL_RE = re.compile(r'https?://(?:dx\.)?doi\.org/(10\.\S+)', re.IGNORECASE)
_ARXIV_URL_RE = re.compile(r'arxiv\.org/(?:abs|pdf)/(\S+)', re.IGNORECASE)

# A workspace audit that verifies hundreds of citations would take longer than
# the turn that asked for it. The cap is generous for a real paper — a
# thesis-length reference list is rarely past this — and the report says when
# it was hit rather than quietly stopping.
_MAX_CHECKS = 60

_SKIPPABLE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.pdf', '.docx', '.zip'}


def _identifiers(text: str) -> list[str]:
    """Every verifiable citation identifier in a block of text, deduplicated.

    Order follows the text so the report reads in the order the citations
    appear. DOIs found through a doi.org URL and written bare are the same
    citation, so the second sighting is skipped.
    """
    found: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        key = value.lower().rstrip('.,;)')
        if key and key not in seen:
            seen.add(key)
            found.append(value)

    for match in _DOI_URL_RE.finditer(text):
        _add(match.group(1))
    for match in _ARXIV_URL_RE.finditer(text):
        _add(match.group(1))
    for match in _DOI_RE.finditer(text):
        _add(match.group(0))
    for match in _ARXIV_RE.finditer(text):
        _add(match.group(1))
    return found


def _markdownFiles(root: Path) -> list[Path]:
    """The workspace's markdown files, deepest-first, skipping exports and binaries."""
    if not root.is_dir():
        return []
    files = [
        p for p in root.rglob('*.md')
        if p.is_file()
        and 'exports' not in p.parts
        and p.suffix.lower() not in _SKIPPABLE_SUFFIXES
    ]
    # Deterministic order: the report should not shuffle between runs.
    return sorted(files, key=lambda p: (len(p.parts), p.as_posix().lower()))


async def _checkPaperCitations(path: str, **kwargs: object) -> str:
    """Verify every citation identifier found under a paper workspace."""
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        return json.dumps(
            {
                'ok': False,
                'error': f'not a directory: {path}',
                'hint': 'Pass the paper workspace folder — the one holding paper-state.md and drafts/.',
            },
            ensure_ascii=False,
        )

    files = _markdownFiles(root)
    perFile: dict[str, list[str]] = {}
    # A source record with no DOI, arXiv id or URL cannot be checked at all —
    # and that is exactly where an invented reference hides. Naming them is
    # the point: an audit that stays silent about the unchecked would read as
    # a clean bill of health.
    noIdentifier: list[str] = []
    for file in files:
        try:
            text = file.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        ids = _identifiers(text)
        if ids:
            perFile[str(file.relative_to(root))] = ids
        elif text.strip():
            noIdentifier.append(str(file.relative_to(root)))

    total = sum(len(ids) for ids in perFile.values())
    capped = total > _MAX_CHECKS
    verified: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    checked = 0
    for rel, ids in perFile.items():
        for identifier in ids:
            if checked >= _MAX_CHECKS:
                break
            checked += 1
            record = await resolve_citation(identifier)
            entry = {
                'file': rel,
                'identifier': identifier,
                'status': record['status'],
                'source': record['source'],
                'title': record['title'],
                'year': record['year'],
                'doi': record['doi'],
                'url': record['url'],
                'score': record['score'],
            }
            if record['status'] == 'verified':
                verified.append(entry)
            else:
                unverified.append(entry)
        if checked >= _MAX_CHECKS:
            break

    return json.dumps(
        {
            'ok': True,
            'workspace': str(root),
            'filesScanned': len(files),
            'filesWithCitations': len(perFile),
            'noIdentifier': noIdentifier,
            'citationsChecked': checked,
            'verified': verified,
            'unverified': unverified,
            'capped': capped,
            'note': (
                'Only citations carrying a DOI, an arXiv id or a URL are checkable; '
                'a bare author-year mention needs one of those added before it can '
                'be verified.'
            )
            if perFile
            else 'No citation identifiers found in this workspace.',
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


async def _verifyCitationHandler(citation: str, **kwargs: object) -> str:
    record = await resolve_citation(citation)
    return json.dumps(record, ensure_ascii=False, indent=2, default=str)


def register() -> None:
    """Register the citation-integrity tools (read-only network + file reads)."""

    tool_registry.register(
        'verify_citation',
        (
            'Check whether one citation is a REAL published work before citing it. '
            'Resolves it against Crossref, then OpenAlex, then arXiv, and returns the '
            'matched record with status "verified" or "unverified". Accepts a DOI, an '
            'arXiv id, a doi.org/arxiv.org URL, or a free-text bibliographic string. '
            'Never cite a reference that came back "unverified" — flag it to the user '
            'instead. This is the Research Paper Studio integrity gate.'
        ),
        _verifyCitationHandler,
        {
            'type': 'object',
            'properties': {
                'citation': {
                    'type': 'string',
                    'description': 'A DOI, arXiv id, doi.org/arxiv.org URL, or a free-text citation string.',
                },
            },
            'required': ['citation'],
        },
    )

    tool_registry.register(
        'check_paper_citations',
        (
            'Audit a whole paper workspace: verify every citation identifier (DOI, arXiv id, '
            'URL) found in its markdown and report which are real and which are unverifiable. '
            'Pass the paper workspace folder — the one holding paper-state.md and drafts/. '
            'Read-only. A bare author-year mention with no identifier is reported as needing '
            'one, not guessed at.'
        ),
        _checkPaperCitations,
        {
            'type': 'object',
            'properties': {
                'path': {
                    'type': 'string',
                    'description': 'The paper workspace folder to audit.',
                },
            },
            'required': ['path'],
        },
    )
