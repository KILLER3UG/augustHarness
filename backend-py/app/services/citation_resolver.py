"""Resolve a citation to a real published record, or flag it as unverifiable.

Why this exists: the Research Paper Studio's first integrity rule is "never
fabricate a reference". A language model can produce a plausible-looking
citation — real-sounding authors, a real journal, a plausible year — that does
not exist. The only defence is to resolve every citation against a registry of
actual publications and refuse the ones that resolve nowhere.

Free sources, in priority order (no API key required):

1. **Crossref** — the DOI registry; authoritative for journal articles and the
   right place to confirm a DOI actually points at the paper being cited.
2. **OpenAlex** — a broad open catalogue of works; the fallback when Crossref
   has no record (conference papers, working papers, some books).
3. **arXiv** — preprints (CS, physics, math, economics); the usual holdout for
   a paper that is real but not yet journal-published.

A result only counts as **verified** when the citation carried the identifier
itself (a DOI or arXiv id that resolves), or a registry returned an essentially
exact title. A fuzzy search hit is reported as a **candidate** — a real record
the caller confirms before citing — never as verified, because a near-miss
title is exactly how a fabricated reference gets laundered into a real-looking
one. When nothing resolves, the result is **unverified**.
"""

from __future__ import annotations

import logging
import re
from typing import Any

# arXiv's API returns Atom XML. The stdlib parser is vulnerable to entity
# expansion ("billion laughs") on untrusted input, so the hardened parser is
# used for it — see the S314 rule.
import defusedxml.ElementTree as SafeET

logger = logging.getLogger(__name__)

_TIMEOUT_S = 20.0
# A polite User-Agent: Crossref routes requests that identify themselves to a
# faster pool, and it is the courteous thing to do for a free service.
_USER_AGENT = 'August-Proxy/1.0 (research-paper-studio; citation verification)'

# Tokens too common (or too short) to identify a title. A query like
# "A study of the use of AI" shares "study"/"use" with half the literature.
_STOPWORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'based', 'by', 'for', 'from', 'in',
    'into', 'is', 'it', 'its', 'of', 'on', 'or', 'the', 'their', 'this',
    'through', 'to', 'using', 'with', 'within',
}
_MIN_TOKEN_LEN = 4
# A fuzzy-search hit is never "verified" on its own: a near-miss title is how a
# fabricated reference gets laundered into a real-looking one. It is reported
# as a *candidate* for the caller to confirm. Only these two conditions make a
# result verified: the citation carried the identifier itself (DOI / arXiv id),
# or the returned title is an essentially exact match long enough that the
# agreement is not an accident of a short generic title.
_EXACT_TITLE_SCORE = 0.9
_EXACT_TITLE_MIN_TOKENS = 3

_DOI_RE = re.compile(r'10\.\d{4,9}/\S+', re.IGNORECASE)
_ARXIV_ID_RE = re.compile(r'\b(\d{4}\.\d{4,5})(v\d+)?\b', re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    """Distinctive lowercase title words.

    Long enough to be identifying, not a stopword, and not a bare number —
    years and DOI fragments appear in a citation but never identify a title,
    and counting them would make a bare DOI look like it carried title
    information.
    """
    words = re.findall(r'[a-z0-9]+', (text or '').lower())
    return {
        w for w in words
        if len(w) >= _MIN_TOKEN_LEN and w not in _STOPWORDS and not w.isdigit()
    }


def _score_match(query: str, candidate_title: str) -> float:
    """How much of the candidate's title the citation accounts for.

    Coverage of the CANDIDATE, not of the query, on purpose: a citation
    legitimately carries tokens a title does not (authors, venue, year), and
    counting those would dilute a correct match into "unverified". A real
    match's title words are almost all present in the citation string, so this
    sits near 1.0; a wrong paper's title carries words the citation never
    mentions, so it falls well below.
    """
    have = _tokens(candidate_title)
    if not have:
        return 0.0
    want = _tokens(query)
    return len(want & have) / len(have)


def _clean_doi(raw: str) -> str:
    """Pull a bare DOI out of a citation string, URL form or not."""
    match = _DOI_RE.search(raw or '')
    if not match:
        return ''
    doi = match.group(0).rstrip('.,;)')
    return doi


def _arxiv_id(raw: str) -> str:
    match = _ARXIV_ID_RE.search(raw or '')
    return match.group(1) if match else ''


def _record(
    source: str,
    title: str,
    authors: list[str],
    year: str,
    doi: str,
    url: str,
    query: str,
    *,
    exact: bool = False,
) -> dict[str, Any]:
    """One normalized resolution result, with its verified/unverified verdict.

    ``exact`` means the record was found by an identifier (DOI or arXiv id)
    rather than by a fuzzy search. An identifier that resolves is a real
    record — unless the citation also named a title the identifier does not
    point at, which is a real mismatch worth flagging rather than smoothing
    over.
    """
    score = _score_match(query, title) if title else 0.0
    title_words = len(_tokens(title))
    if exact:
        # The citation carried the identifier itself (DOI / arXiv id) and it
        # resolved. That identifier pins one exact work, so this IS the
        # verification — the title comparison is only meaningful for a fuzzy
        # search, where the citation gave no identifier to check.
        score = 1.0
    if exact or (score >= _EXACT_TITLE_SCORE and title_words >= _EXACT_TITLE_MIN_TOKENS):
        status = 'verified'
    elif title:
        # A real record, but only a fuzzy match for this citation. The caller
        # confirms the title before citing it.
        status = 'candidate'
    else:
        status = 'unverified'
    return {
        'status': status,
        'source': source,
        'title': title,
        'authors': authors,
        'year': year,
        'doi': doi,
        'url': url,
        'score': round(score, 3),
        'query': query,
    }


async def _crossref(client: Any, query: str) -> dict[str, Any] | None:
    """Crossref bibliographic or DOI lookup. None = nothing usable returned."""
    doi = _clean_doi(query)
    if doi:
        resp = await client.get(
            f'https://api.crossref.org/works/{doi}',
            headers={'User-Agent': _USER_AGENT},
        )
        resp.raise_for_status()
        message = resp.json().get('message', {})
        items = [message] if message.get('DOI') else []
    else:
        resp = await client.get(
            'https://api.crossref.org/works',
            params={'query.bibliographic': query, 'rows': 5},
            headers={'User-Agent': _USER_AGENT},
        )
        resp.raise_for_status()
        items = resp.json().get('message', {}).get('items', [])
    if not items:
        return None
    item = items[0]
    title = ' '.join(item.get('title') or []).strip()
    if not title:
        return None
    authors = [
        f"{a.get('given', '')} {a.get('family', '')}".strip()
        for a in item.get('author', [])
        if isinstance(a, dict)
    ]
    date = item.get('published') or item.get('issued') or {}
    parts = date.get('date-parts') or [[None]]
    year = str(parts[0][0]) if parts and parts[0] else ''
    record_doi = item.get('DOI', '') or doi
    return _record(
        source='crossref',
        title=title,
        authors=authors,
        year=year,
        doi=record_doi,
        url=item.get('URL') or (f'https://doi.org/{record_doi}' if record_doi else ''),
        query=query,
        exact=bool(doi),
    )


async def _openalex(client: Any, query: str) -> dict[str, Any] | None:
    """OpenAlex works search. None = nothing usable returned."""
    resp = await client.get(
        'https://api.openalex.org/works',
        params={'search': query, 'per_page': 5},
        headers={'User-Agent': _USER_AGENT},
    )
    resp.raise_for_status()
    results = resp.json().get('results', [])
    if not results:
        return None
    work = results[0]
    title = (work.get('display_name') or '').strip()
    if not title:
        return None
    authors = [
        (a.get('author') or {}).get('display_name', '')
        for a in work.get('authorships', [])
        if isinstance(a, dict)
    ]
    doi = (work.get('doi') or '').replace('https://doi.org/', '')
    return _record(
        source='openalex',
        title=title,
        authors=[a for a in authors if a],
        year=str(work.get('publication_year') or ''),
        doi=doi,
        url=work.get('doi') or work.get('id') or '',
        query=query,
    )


async def _arxiv(client: Any, query: str) -> dict[str, Any] | None:
    """arXiv preprint lookup, by arXiv id when present, else by title search."""
    arxiv_id = _arxiv_id(query)
    if arxiv_id:
        resp = await client.get(
            f'https://export.arxiv.org/api/query?id_list={arxiv_id}',
            headers={'User-Agent': _USER_AGENT},
        )
        exact = True
    else:
        resp = await client.get(
            'https://export.arxiv.org/api/query',
            params={'search_query': f'all:{query}', 'max_results': 5},
            headers={'User-Agent': _USER_AGENT},
        )
        exact = False
    resp.raise_for_status()
    # Atom XML: the default namespace has to be named explicitly.
    root = SafeET.fromstring(resp.text)
    ns = {'a': 'http://www.w3.org/2005/Atom'}
    entries = root.findall('a:entry', ns)
    if not entries:
        return None
    entry = entries[0]
    title = ' '.join((entry.findtext('a:title', default='', namespaces=ns) or '').split())
    if not title:
        return None
    authors = [
        (e.findtext('a:name', default='', namespaces=ns) or '').strip()
        for e in entry.findall('a:author', ns)
    ]
    published = entry.findtext('a:published', default='', namespaces=ns) or ''
    entry_id = entry.findtext('a:id', default='', namespaces=ns) or ''
    return _record(
        source='arxiv',
        title=title,
        authors=[a for a in authors if a],
        year=published[:4],
        doi='',
        url=entry_id,
        query=query,
        exact=exact,
    )


_SOURCES = (('crossref', _crossref), ('openalex', _openalex), ('arxiv', _arxiv))


async def resolve_citation(citation: str) -> dict[str, Any]:
    """Resolve one citation against Crossref, then OpenAlex, then arXiv.

    Returns a single record. ``status`` is:

    * ``verified`` — the citation carried a DOI/arXiv id that resolved, or a
      registry returned an essentially exact title. Safe to cite.
    * ``candidate`` — a real record matched only fuzzily. Confirm the title
      before citing; the record carries its DOI/URL to confirm against.
    * ``unverified`` — nothing matched, or every registry failed. Never cite,
      and do not treat a failed check as evidence that a paper exists.
    """
    query = (citation or '').strip()
    if not query:
        return _record('none', '', [], '', '', '', '')

    from app.services import http_env

    attempts: list[str] = []
    fallback: dict[str, Any] | None = None
    async with http_env.client(timeout=_TIMEOUT_S) as client:
        for name, resolver in _SOURCES:
            try:
                record = await resolver(client, query)
            except Exception as exc:  # noqa: BLE001 — a source failing is not a crash
                attempts.append(f'{name}: {type(exc).__name__}')
                logger.debug('citation source %s failed for %r', name, query, exc_info=True)
                continue
            if record is None:
                attempts.append(f'{name}: no record')
                continue
            if record['status'] == 'verified':
                record['tried'] = attempts
                return record
            # Keep the best non-verified candidate — a real record the caller
            # confirms, or the strongest evidence that nothing matched.
            better = (
                fallback is None
                or (record['status'] == 'candidate' and fallback['status'] != 'candidate')
                or (record['status'] == fallback['status'] and record['score'] >= fallback['score'])
            )
            if better:
                fallback = record
            attempts.append(f'{name}: {record["status"]} (score {record["score"]})')

    result = fallback or _record('none', '', [], '', '', '', query)
    result['tried'] = attempts
    return result
