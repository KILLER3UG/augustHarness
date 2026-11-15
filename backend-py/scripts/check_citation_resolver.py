"""Static + live checks for the citation resolver (Research Paper Studio).

Three parts:

  1. Deterministic scoring/threshold tests — no network. Prove the
     verified/candidate boundary behaves, including the short-generic-title
     laundering case and close-but-wrong matches either side of the threshold.
  2. Live resolution tests against Crossref / OpenAlex / arXiv — plausible fake,
     non-resolving DOI, identifier mismatch, arXiv-only and OpenAlex fallbacks.
  3. Workspace in-text vs reference-list cross-check — a citation named in the
     body with no reference entry, and a reference entry the body never cites.

Usage:
    cd backend-py && python scripts/check_citation_resolver.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'backend-py'))

from app.services.citation_resolver import (  # noqa: E402
    _record,
    _score_match,
    resolve_citation,
)
from app.services.tool_registrations.citation_tools import (  # noqa: E402
    _checkPaperCitations,
)

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = '') -> None:
    results.append((bool(ok), name, detail))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{(' — ' + detail) if detail else ''}")


# ── Part 1: deterministic scoring and the verified/candidate boundary ────────
def part1() -> None:
    print('\n-- Part 1 · scoring and the verified/candidate boundary (no network) --')

    # A near-identical title: essentially exact, long enough to be trusted.
    q = 'Deep learning for network intrusion detection using convolutional neural networks'
    near = 'Deep learning for network intrusion detection using convolutional neural networks'
    r = _record('crossref', near, [], '2020', '10.1/x', '', q)
    check(r['status'] == 'verified', 'essentially exact title verifies',
          f'score={r["score"]}')

    # A close-but-WRONG title: shares the topic words but is a different paper.
    close = 'Deep learning for network intrusion detection using recurrent neural networks'
    r = _record('crossref', close, [], '2020', '10.1/x', '', q)
    check(r['status'] == 'candidate',
          'close-but-wrong title is a candidate, not verified',
          f'score={r["score"]} (below the exact-title bar)')

    # The laundering case: a SHORT generic real title that appears inside a
    # longer fabricated citation. Coverage of the candidate is 1.0 but the
    # candidate is too short for the agreement to mean anything.
    short_title = 'Quantum teleportation'
    fabricated = 'Quantum teleportation of coffee cups. Journal of Impossible Things.'
    r = _record('crossref', short_title, [], '2004', '10.1/y', '', fabricated)
    check(r['status'] == 'candidate',
          'short generic title inside a longer fake citation is NOT verified',
          f'score={r["score"]}, title tokens={len(_score_tokens(short_title))}')

    # A score just under and just over the exact-title bar, same token count.
    high = 'deep learning network intrusion detection convolutional'
    low = 'deep learning network intrusion detection recurrent'
    check(_score_match(q, high) >= 0.9, 'score at/over the exact-title bar',
          f'{_score_match(q, high):.2f}')
    check(_score_match(q, low) < 0.9, 'score just under the exact-title bar',
          f'{_score_match(q, low):.2f}')

    # An identifier that resolved with no title to compare: verified.
    r = _record('crossref', '', [], '2015', '10.1038/nature14539', '', '10.1038/nature14539', exact=True)
    check(r['status'] == 'verified', 'bare resolving DOI verifies (nothing to compare)',
          f'score={r["score"]}')


def _score_tokens(text: str) -> set[str]:
    from app.services.citation_resolver import _tokens
    return _tokens(text)


# ── Part 2: live resolution against the real registries ─────────────────────
async def part2() -> None:
    print('\n-- Part 2 · live resolution (Crossref / OpenAlex / arXiv) --')

    # A plausible fake: realistic title, authors, journal — no real record.
    r = await resolve_citation(
        'Okoro, P. A. (2021). Telepathic packet inspection for zero-day worms. '
        'Journal of Impossible Security, 12(4), 77-90.'
    )
    check(r['status'] != 'verified', 'plausible fake is not verified',
          f"status={r['status']}, best={r['title'][:40]!r}")

    # A syntactically valid DOI that does not resolve.
    r = await resolve_citation('10.9999/this.does.not.exist.1234')
    check(r['status'] != 'verified', 'non-resolving DOI is not verified',
          f"status={r['status']}, tried={r.get('tried')}")

    # A REAL DOI cited with the WRONG title: the resolver must return the real
    # title so the mismatch is visible, and must not call the wrong title a
    # verification of the cited one.
    r = await resolve_citation(
        'Deep learning for credit card fraud detection 10.1038/nature14539'
    )
    resolved = (r['title'] or '').lower()
    check(
        r['status'] in ('verified', 'candidate')
        and 'deep learning' in resolved
        and 'credit card' not in resolved,
        'DOI resolving to a different paper returns the REAL title',
        f"status={r['status']}, resolved title={r['title'][:50]!r}",
    )

    # A real paper that lives on arXiv: the arXiv fallback must reach it.
    r = await resolve_citation('arXiv:1706.03762')
    check(r['status'] == 'verified' and r['source'] == 'arxiv',
          'arXiv-only paper resolves through the arXiv fallback',
          f"source={r['source']}, title={r['title'][:40]!r}")

    # A real work reached through OpenAlex (the middle fallback).
    r = await resolve_citation('10.1038/nature14539')
    check(r['status'] == 'verified' and r['source'] in ('crossref', 'openalex'),
          'real DOI resolves (Crossref primary, OpenAlex fallback)',
          f"source={r['source']}, title={r['title'][:40]!r}")


# ── Part 3: in-text vs reference-list cross-check ────────────────────────────
async def part3() -> None:
    print('\n-- Part 3 · in-text vs reference-list cross-check (workspace) --')
    ws = Path(tempfile.mkdtemp(prefix='cite-check-'))
    try:
        (ws / 'drafts').mkdir(parents=True)
        # DOI_A is cited in the body AND in the references (fine).
        # DOI_B is in the references but never cited in the body (padding).
        # DOI_C is cited in the body but missing from the references (dangling).
        (ws / 'drafts' / 'chapter1.md').write_text(
            'Cites A: https://doi.org/10.1000/aaa and C: https://doi.org/10.1000/ccc\n',
            encoding='utf-8',
        )
        (ws / 'references.md').write_text(
            'A: https://doi.org/10.1000/aaa\nB: https://doi.org/10.1000/bbb\n',
            encoding='utf-8',
        )

        report = json.loads(await _checkPaperCitations(str(ws)))
        check(report['inTextMissingFromRefs'] == ['10.1000/ccc'],
              'in-text citation with no reference entry is flagged',
              f"missing={report['inTextMissingFromRefs']}")
        check(report['refsNeverCited'] == ['10.1000/bbb'],
              'reference entry never cited in the body is flagged',
              f"never cited={report['refsNeverCited']}")
        check(not report['noIdentifier'], 'no silent unchecked files',
              f"noIdentifier={report['noIdentifier']}")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


async def main() -> int:
    part1()
    await part2()
    await part3()
    passed = sum(1 for ok, _n, _d in results if ok)
    print(f'\n{passed}/{len(results)} citation check(s) passed')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
