"""Fact retrieval for the memory store.

Retrieval is PULL-ONLY. A turn automatically receives the frozen boot memory
index (built in the workbench and already in the system prompt), the always-in
``kind='profile'``/``kind='preference'`` lane (:func:`build_profile_block`), and
the ``new in memory since this chat opened:`` delta
(:func:`build_memory_delta_block`). Nothing else is auto-injected.

There is no automatic ranked recall lane. BM25 (:func:`retrieve_relevant_facts`,
:func:`find_similar_facts`, ``brain_query(store='facts')``) still runs, but only
inside an explicit model-initiated call. The retired per-turn ``Relevant
memory:`` tail was a ranker with no precedent: Claude Code / Claude Desktop
memory is a file directory plus an index the model reads, not a relevance
ranker. Measured on the real 19-fact store, BM25 returned nothing for "what
should I set my editor theme to" (the fact says "Prefers dark mode") and ranked
the wrong fact first for "what am I doing my degree in". The index has no such
failure mode.

No embeddings/vector store: BM25 over a few hundred facts is exact enough for an
explicit search.

OQ4 (ruled 2026-09-04): August commits to NO vectors and reserves no schema
for them — the decisive precedent is the orphaned ``vector_entries`` table
(12 rows, zero code references) that a reserved schema outlived its feature
by; it is now dropped. Adding embeddings later is purely additive, so
reserving anything now would only recreate that dead weight.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from app.json_narrowing import as_int
from app.services.memory_store import mirror as record
from app.services.memory_store.rest import PROFILE_FACT_KIND
from app.services.sensitive_topics import sensitiveMemoryReason
from app.services.tools.retrieval import QUERY_STOPWORDS as _QUERY_STOPWORDS

# One entry's body never crowds out the rest.
_ENTRY_CHAR_CAP = 300
# Queries shorter than this get no BM25 injection (the intake
# brain_index_snippet fallback covers empty/short turns).
_MIN_QUERY_CHARS = 8
# Recency decay: the usage boost halves per 30 days unused —
# often-quoted stale facts stop crowding out fresh ones. '' last_used_at
# (never used) gets NO decay; a fact earns its boost on first use.
_DECAY_HALF_LIFE_DAYS = 30.0
# The always-on profile lane's own budget: ~150 tokens (600 chars). A large
# profile set must never crowd the rest of the turn's context, and this is the
# lane's ONLY bound — a row-count cap would be a second budget, and it would
# drop facts nobody ever named.
_PROFILE_CHAR_CAP = 600
# Lane header: says out loud that this section is NOT keyword-matched, so the
# model does not report it as "only relevant when asked".
_PROFILE_LANE_HEADER = 'Always-in memory (profile and standing preferences):'
# The memory-delta lane: facts written after the session opened, rendered
# AFTER the memory section as a plain "new in memory" section.
# budget — it duplicates nothing the profile lane already carries, and a burst
# of writes must not crowd the keyword lane. ~125 tokens.
_DELTA_CHAR_CAP = 500
_DELTA_MAX_FACTS = 8
_DELTA_HEADER = 'new in memory since this chat opened'
# A receipt naming omitted facts is itself bounded — naming the dropped facts
# must not eat the budget it exists to report on.
_OMITTED_RECEIPT_CHAR_CAP = 220
# ...and it needs at least this much room to say anything at all (the
# count-only form). The fitting pass reserves this, not the full cap, so a
# lane pays for the receipt it can actually carry instead of over-reserving.
_OMITTED_RECEIPT_MIN_ROOM = 64

_lock = threading.Lock()
# M-2: one cached corpus PER SCOPE. A bot-scope corpus is the
# global ∪ bot union, so the union rule is baked into the index and queries
# stay O(1) cache hits. Keyed by normalized scope ('global' = plain store).
_caches: dict[str, dict[str, Any]] = {}


def invalidate_fact_index(scope: str | None = None) -> None:
    """Drop cached indexes on fact write/delete.

    ``scope=None`` clears everything (bulk sweeps, store wipes). With a
    scope: a 'global' write must clear ALL caches (every union corpus
    contains global), while a scoped write only invalidates that scope's
    own corpus — the other unions are untouched by it.
    """
    with _lock:
        if scope is None:
            _caches.clear()
            return
        try:
            from app.services.session_scope import normalize_scope

            norm = normalize_scope(scope)
        except Exception:
            _caches.clear()
            return
        if norm == 'global':
            _caches.clear()
        else:
            _caches.pop(norm, None)


def _fact_body_text(value_raw: object) -> str:
    """Render a stored fact value as plain text for indexing/display."""
    loaded: object
    if isinstance(value_raw, str):
        try:
            loaded = json.loads(value_raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            loaded = value_raw
    elif value_raw is None:
        loaded = ''
    else:
        loaded = value_raw
    if isinstance(loaded, dict):
        parts = [str(loaded.get('fact') or '')]
        details = str(loaded.get('details') or '').strip()
        if details:
            parts.append(details)
        text = ' — '.join(p for p in parts if p)
    else:
        text = str(loaded)
    return ' '.join(text.split())


def _load_index(scope: str = 'global') -> dict[str, Any]:
    """Build the BM25 corpus for one scope (cached per scope).

    M-2 union rule: a scope's corpus is ``global ∪ this-scope`` — a Bot sees
    the user's shared memory plus its own notes, never another Bot's. The
    'global' scope degenerates to global-only (the pre-M-2 behavior, and
    every legacy row carries DEFAULT 'global').
    """
    from app.services.memory_store.rest import _visible
    from app.services.session_scope import normalize_scope

    scope = normalize_scope(scope)
    with _lock:
        cached = _caches.get(scope)
        if cached is not None:
            return cached
    from app.services.tools.retrieval import BM25, _tokenize

    rows: list[dict[str, object]] = []
    corpus: list[list[str]] = []
    try:
        # M-1 usage decoupling: the cached corpus carries tokens + text only
        # — never use_count/last_used_at. Usage is fetched per query for the
        # candidate set (see _usage_for), so touch_fact_usage no longer
        # invalidates this cache and the per-turn full-corpus rebuild cliff
        # is gone. The rows come off the markdown record (one file per fact)
        # through the same visibility predicate every other read door uses.
        factRows = [
            row
            for row in record.load_facts()
            if _visible(row, scope)
        ]
        for r in factRows:
            body = _fact_body_text(r.get('fact_value'))
            title = str(r.get('title') or '').strip()
            key = str(r.get('fact_key') or '')
            desc = ' '.join(str(r.get('description') or '').split())
            _sensitiveFlag = as_int(r.get('sensitive'), 0)
            # Sensitive rows never ride the recall corpus — the always-on
            # profile lane and every explicit search read THIS list, and all of
            # them are surfaces an unrelated turn sees. The durable
            # `sensitive` flag (056) catches rows whose detector wording has
            # since changed; the live detector catches rows written before the
            # flag or by a raw write path. Explicit reads by key stay
            # unfiltered: a model or human who asks by name gets it.
            if _sensitiveFlag or sensitiveMemoryReason(title, key, desc, body):
                continue
            try:
                aliases = json.loads(str(r.get('aliases') or '[]'))
            except (json.JSONDecodeError, TypeError):
                aliases = []
            if not isinstance(aliases, list):
                aliases = []
            aliasText = ' '.join(str(a) for a in aliases)
            # Title + description + aliases + key words + body: titles and the
            # ZCode-parity description hook carry the human phrasing the model
            # is most likely to echo back; explicit aliases are the words the
            # writer said a future message might use.
            text = f"{title} {desc} {aliasText} {key.replace('-', ' ').replace(':', ' ')} {body}"
            tokens = _tokenize(text)
            if not tokens:
                continue
            rows.append(
                {
                    'key': key,
                    'title': title,
                    'description': desc,
                    'body': body,
                    'source': str(r.get('source') or ''),
                    'verified_at': str(r.get('verified_at') or ''),
                    'kind': str(r.get('kind') or 'fact'),
                    'category': str(r.get('category') or 'general'),
                    'aliases': [str(a) for a in aliases],
                    'subject_type': str(r.get('subject_type') or ''),
                    'tag': str(r.get('tag') or ''),
                    'sensitive': bool(_sensitiveFlag),
                    # Carry the row's true scope — recall metrics
                    # and the transcript chip mislabeled bot-private recalls
                    # as 'global' (the hardcoded label).
                    'scope': str(r.get('scope') or 'global'),
                    # Recency for the always-on profile lane. Safe to cache:
                    # updated_at only changes on a fact write, which already
                    # invalidates this corpus (unlike usage, which M-1 keeps
                    # out of the cache — see _usage_for).
                    'updated_at': str(r.get('updated_at') or ''),
                }
            )
            corpus.append(tokens)
    except Exception as exc:
        # A transient failure (a malformed row, an unreadable file) must
        # NOT be cached as "there is no memory". The corpus cache is invalidated
        # only by a fact write, so one bad moment used to disable BM25 search
        # AND the always-on profile lane until the next write — silently, since
        # build_profile_block just returns ''. Degrade for THIS call and let the
        # next one rebuild.
        logging.warning('fact index build failed; recall degraded for this call: %s', exc)
        return {'rows': [], 'tokens': [], 'bm25': None}
    index = {'rows': rows, 'tokens': corpus, 'bm25': BM25(corpus) if corpus else None}
    with _lock:
        _caches[scope] = index
    return index


def find_similar_facts(
    text: str, k: int = 3, *, scope: str = 'global'
) -> list[tuple[float, str, str]]:
    """Top-k existing facts similar to ``text`` as ``(ratio, key, title)``.

    ``ratio = BM25(text→doc) / BM25(doc→doc)`` ∈ (0, 1] — a scale-free
    similarity suited to near-duplicate detection (M5 lesson dedupe, M4
    merge detection). Unlike ``retrieve_relevant_facts`` there is no usage
    boost and no minimum-query gate: callers compare ratios to thresholds.
    M-2: compared within the caller's scope union (a bot dedupes against
    global + its own notes, not other bots).
    """
    from app.services.tools.retrieval import _tokenize

    queryTokens = _tokenize((text or '').strip())
    if not queryTokens:
        return []
    index = _load_index(scope)
    bm25 = index.get('bm25')
    rows = index.get('rows')
    tokens = index.get('tokens')
    if bm25 is None or not rows or not tokens:
        return []
    scored: list[tuple[float, str, str]] = []
    for i, row in enumerate(rows):
        s = bm25.score(queryTokens, i)
        if s <= 0:
            continue
        docTokens = tokens[i]
        selfScore = bm25.score(docTokens, i) if docTokens else 0.0
        ratio = min(1.0, s / selfScore) if selfScore > 0 else 0.0
        scored.append((ratio, str(row.get('key')), str(row.get('title') or '')))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[: max(1, k)]


def _usage_decay(last_used_at: str) -> float:
    """Multiplier on the ``use_count`` boost — halves every 30 idle days.

    ``1.0`` when unused ('' / unparseable: a fact with no usage history has
    no staleness signal yet, so its boost stays at face value), decaying
    toward 0 as time since last use grows. Phase D (Part 17) item 3.
    """
    raw = (last_used_at or '').strip()
    if not raw:
        return 1.0
    try:
        from datetime import datetime, timezone

        # Stored as datetime('now') UTC — tolerate a trailing 'Z'/offset.
        normalized = raw[:-1] + '+00:00' if raw.endswith('Z') else raw
        lastUsed = datetime.fromisoformat(normalized)
        if lastUsed.tzinfo is None:
            lastUsed = lastUsed.replace(tzinfo=timezone.utc)
        days = max(0.0, (datetime.now(timezone.utc) - lastUsed).total_seconds() / 86400.0)
        return 0.5 ** (days / _DECAY_HALF_LIFE_DAYS)
    except (ValueError, TypeError):
        return 1.0


def _rows_by_key() -> dict[str, dict[str, object]]:
    """Every stored row, keyed by fact key (first row wins on a same-key
    different-scope pair — the same precedence a bare key lookup applies).

    One scan serves the four per-candidate fetches below; at this store's
    scale (tens to hundreds of files) a scan per query is the cheap option
    and needs no cache to invalidate.
    """
    out: dict[str, dict[str, object]] = {}
    for row in record.load_facts():
        key = str(row.get('fact_key') or '')
        if key and key not in out:
            out[key] = dict(row)
    return out


def _usage_for(keys: list[str]) -> dict[str, tuple[int, str]]:
    """Fresh ``(use_count, last_used_at)`` for a candidate key set.

    M-1 usage decoupling: usage no longer lives in the cached corpus, so
    ranking fetches it per query for the candidates (≤200 — far above the
    k=5 window, so the boost can only reshuffle within the BM25-strongest
    set).
    """
    out: dict[str, tuple[int, str]] = {}
    uniq = [k for k in dict.fromkeys(keys) if k][:200]
    if not uniq:
        return out
    try:
        rows = _rows_by_key()
        for key in uniq:
            row = rows.get(key)
            if row is not None:
                out[key] = (as_int(row.get('use_count'), 0), str(row.get('last_used_at') or ''))
    except Exception as exc:
        logging.debug('candidate usage fetch failed: %s', exc)
    return out


def _confidence_for(keys: list[str]) -> dict[str, float]:
    """Stored confidence per candidate key — a PRIOR on the fact being right.

    Separate from :func:`_usage_for` on purpose: usage is what happened to a
    fact after it was written, confidence is what the writing door believed when
    it wrote it (`remember` stores 0.7 for a model-authored fact,
    `turn_outcomes` 0.6 for an inferred lesson, an explicit user statement 1.0).
    Missing rows read 1.0, the record default, so a fact with no recorded
    belief is never penalised for the absence.
    """
    out: dict[str, float] = {}
    uniq = [k for k in dict.fromkeys(keys) if k][:200]
    if not uniq:
        return out
    try:
        rows = _rows_by_key()
        for key in uniq:
            row = rows.get(key)
            if row is not None:
                raw = row.get('confidence')
                out[key] = 1.0 if raw is None else float(raw)  # type: ignore[arg-type]
    except Exception as exc:
        logging.debug('candidate confidence fetch failed: %s', exc)
    return out


def _hot_usage() -> dict[str, tuple[int, str]]:
    """``(use_count, last_used_at)`` for EVERY fact with a non-zero use_count.

    A deliberately different shape from :func:`_usage_for`, and the
    reason is item 14. Once the usage boost is allowed to promote a fact that
    did not match lexically, the boost has to be known for the whole corpus and
    not just the BM25-strongest 200 — otherwise the promotion it exists to
    enable is structurally impossible, which is the bug.
    """
    out: dict[str, tuple[int, str]] = {}
    try:
        for key, row in _rows_by_key().items():
            if as_int(row.get('use_count'), 0) > 0:
                out[key] = (as_int(row.get('use_count'), 0), str(row.get('last_used_at') or ''))
    except Exception as exc:
        logging.debug('hot usage fetch failed: %s', exc)
    return out


# The floor below which a usage boost is too decayed to rescue a lexical match
# that the disuse penalty dragged to zero. One use, undecayed, is exactly 0.05
# — the smallest live signal. Zero-overlap facts are filtered before this
# point (usage re-ranks matches; it does not create them).
_MIN_USEFUL_BOOST = 0.05

# How far a stored confidence of 0.0 may dampen a lexical score. Confidence can
# only demote — the factor is exactly 1.0 at the column default, so no fact that
# exists today changes rank because this line was added.
_CONF_FLOOR = 0.85


def _usage_map(keys: list[str]) -> dict[str, tuple[int, str]]:
    """Usage for the BM25 candidates PLUS every fact that has ever been used.

    Two sources, one map: the bounded IN-list still covers the candidates (and
    is the only source for a fact with use_count=0, which simply has no entry),
    while :func:`_hot_usage` supplies the rows outside the top-200 that the
    boost is now allowed to promote.
    """
    merged = dict(_usage_for(keys))
    for k, v in _hot_usage().items():
        # Never downgrade a value the targeted query already fetched.
        if k not in merged:
            merged[k] = v
    return merged


def _disuse_for(keys: list[str]) -> dict[str, int]:
    """``injected_count`` per candidate key (M-7).

    How many times a fact has been INJECTED into a ranked recall. A fact that
    is injected again and again and never quoted is a stale-but-plausible
    entry holding rank: the usage boost only ever rewards, so nothing in the
    scorer ever pushed such a fact down.

    Read fresh (not from the cached corpus) for the same reason usage is:
    the counter changes on every injection, and a cached value would make
    the penalty stale by exactly one turn.
    """
    out: dict[str, int] = {}
    uniq = [k for k in dict.fromkeys(keys) if k][:200]
    if not uniq:
        return out
    try:
        rows = _rows_by_key()
        for key in uniq:
            row = rows.get(key)
            if row is not None:
                out[key] = as_int(row.get('injected_count'), 0)
    except Exception as exc:
        # A record without the counter carries no signal — no penalty.
        logging.debug('candidate disuse fetch failed: %s', exc)
    return out


def _disuse_constants() -> tuple[float, int, int]:
    """``(weight, cap, rescue)`` of the disuse penalty, from brain-config.

    The three constants are the ranker's, so they are config and not
    constants in the scorer: an operator can weaken the penalty without a
    code change. Absent config falls back to the shipped values, and a
    weight of 0 disables the penalty entirely.
    """
    weight, cap, rescue = 0.01, 50, 3
    try:
        from app.services.brain_config_service import getRuntimeConfig

        cfg = getRuntimeConfig()
        rawWeight = cfg.get('memoryDisuseWeight')
        if isinstance(rawWeight, (int, float)) and not isinstance(rawWeight, bool):
            weight = max(0.0, min(1.0, float(rawWeight)))
        rawCap = cfg.get('memoryDisuseCap')
        if isinstance(rawCap, int) and not isinstance(rawCap, bool):
            cap = max(0, min(500, int(rawCap)))
        rawRescue = cfg.get('memoryDisuseRescue')
        if isinstance(rawRescue, int) and not isinstance(rawRescue, bool):
            rescue = max(0, min(20, int(rawRescue)))
    except Exception:
        logging.debug('disuse constants unavailable; using defaults', exc_info=True)
    return weight, cap, rescue


def retrieve_relevant_facts(
    query: str,
    k: int = 5,
    prior_turn: str = '',
    scope: str = 'global',
    min_query_chars: int = _MIN_QUERY_CHARS,
    *,
    exclude_keys: set[str] | None = None,
) -> list[dict[str, object]]:
    """Top-k active facts relevant to ``query``, usage-boosted.

    ``min_query_chars`` separates the two callers, which need opposite rules:
    the automatic per-turn tail must not spend context on a 4-character message
    ("how about now"), while an *explicit* memory search the model chose to make
    ("my gpu", "keanu") must answer rather than silently return nothing. A
    caller that deliberately asked gets 1; an empty query is still refused,
    because "everything" is not a recall result.

    BM25 score plus a small ``use_count`` boost — the cheapest real
    "learns what's useful" signal without embeddings (plan §3.4).

    Phase D (Part 17): ``prior_turn`` (the previous *user* message) joins
    the query tokens — a follow-up like "and the second one?" stops being
    single-message myopic. Cheap: no extra calls, no history payload; the
    current message's tokens still dominate because they are scored
    separately and summed.

    M-2 (Part 21): ``scope`` selects the corpus union — 'global' (default,
    the pre-M-2 behavior) or 'bot:<id>' = global ∪ that bot's notes.

    ``exclude_keys`` drops facts from the corpus BEFORE the top-k window is
    closed, which is the only place it can be dropped usefully. The profile
    lane used to be removed by the caller *after* the window had already been
    filled: any profile fact that ranked into the top k consumed a slot and
    was then discarded, so the keyword lane received ``k - (profile facts in
    the top k)``. Since profile facts are deliberately not row-count capped
    (they are bounded by ``_PROFILE_CHAR_CAP`` instead), the keyword lane got
    thinner the richer a user's profile set grew — with no signal, because the
    budget was being spent on a lane that is injected anyway.
    """
    q = (query or '').strip()
    if len(q) < max(1, int(min_query_chars)):
        return []
    index = _load_index(scope)
    bm25 = index.get('bm25')
    rows = index.get('rows')
    if bm25 is None or not rows:
        return []
    scored: list[tuple[float, dict[str, object]]] = []
    from app.services.tools.retrieval import _tokenize

    queryTokens = [
        t for t in _tokenize(q) if t not in _QUERY_STOPWORDS
    ]
    priorTokens = [
        t
        for t in (_tokenize((prior_turn or '').strip()) if (prior_turn or '').strip() else [])
        if t not in _QUERY_STOPWORDS
    ]
    if not queryTokens:
        # A query made only of function words ("what is the", "the is of")
        # carries nothing to rank on. Measured on a 2-row store, keeping them
        # matched EVERY row — "the" rides in nearly every fact's prose — so
        # "What is the capital of France?" surfaced the whole store as
        # "relevant memory". Nothing injected is the honest empty result.
        return []
    blocked = {str(x) for x in (exclude_keys or set())}
    for i, row in enumerate(rows):
        if blocked and str(row.get('key') or '') in blocked:
            continue
        s = bm25.score(queryTokens, i)
        if priorTokens:
            # Follow-up expansion: prior-turn overlap counts at half
            # weight — context, not a substitute for the current ask.
            s += 0.5 * bm25.score(priorTokens, i)
        # A fact that shares NO scored term with the query is not relevant,
        # however useful it has been. Letting the usage boost create the match
        # made one `remember` (which bumps use_count to 1, boost exactly 0.05)
        # enough to surface that fact on every later turn — "What is the
        # capital of France?" returned the whole small store as "Relevant
        # memory". Usage re-ranks lexical matches; it does not invent one.
        if s <= 0:
            continue
        scored.append((s, row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    # Phase D item 3 + M-1 decoupling: the usage boost decays with idle
    # time (halved at 30 days unused); usage values are fetched fresh for
    # the candidate set — not from the (usage-free) cached corpus.
    usage = _usage_map([str(row.get('key')) for _, row in scored])
    confidence = _confidence_for([str(row.get('key')) for _, row in scored])
    # M-7: the NEGATIVE half of the usage signal. A fact that keeps being
    # injected and is never quoted is a stale-but-plausible entry holding
    # rank — nothing in the scorer ever pushed one down, because the usage
    # boost only ever rewards. `use_count * rescue` is the forgiveness: a
    # quote rescues several injections, so a fact that IS used does not sink
    # for having been shown before.
    disuse = _disuse_for([str(row.get('key')) for _, row in scored])
    disuseWeight, disuseCap, disuseRescue = _disuse_constants()
    boosted: list[tuple[float, dict[str, object]]] = []
    for s, row in scored:
        key = str(row.get('key'))
        use_count, last_used_at = usage.get(key, (0, ''))
        # Dampen by the stored prior BEFORE the usage boost is added, and only
        # ever dampen: at 1.0 (the column default, and every explicitly-stated
        # fact) the factor is exactly 1, so this cannot move an existing
        # ranking. A 0.6 inferred lesson still surfaces when it is the only
        # match — it just loses a tie against a fact the user asserted.
        conf = confidence.get(key, 1.0)
        s *= _CONF_FLOOR + (1.0 - _CONF_FLOOR) * max(0.0, min(1.0, conf))
        # A decayed boost is a tiny POSITIVE float, not zero: a fact last used
        # in 2000 gets ~9e-99, so filtering the final score on `> 0` promoted
        # it anyway. Usage may only promote a fact whose usage signal is still
        # alive — one use with no decay is the floor.
        boost = 0.05 * min(use_count, 20) * _usage_decay(last_used_at)
        # The disuse penalty is bounded (the cap) and can never take a fact
        # below zero: it demotes, it does not delete. A fact with no
        # injection record pays nothing.
        penalty = 0.0
        if disuseWeight > 0:
            unused = max(0, disuse.get(key, 0) - use_count * disuseRescue)
            penalty = disuseWeight * min(unused, disuseCap)
        final = s + boost - penalty
        if final <= 0 and boost < _MIN_USEFUL_BOOST:
            continue
        boosted.append((final, row))
    boosted.sort(key=lambda pair: pair[0], reverse=True)
    return [dict(row) for _, row in boosted[: max(1, k)]]


# ── shared rendering (profile lane + delta) ─────────────────────────────────


_DISTILLED_SOURCE = 'harness'
_DISTILLED_MARK = '[distilled] '

# Query-side function words are shared with the skill ranker — one authority
# decides what a query means (see ``tools/retrieval.QUERY_STOPWORDS``).


def _fact_line(row: dict[str, object], *, entry_cap: int = _ENTRY_CHAR_CAP) -> str:
    """Render one corpus row as its ``- title: body`` line.

    Used by BOTH automatic lanes (profile + delta) so the two can never drift
    in shape, and so one long body never crowds out its neighbours.

    Marking is skipped when a person has confirmed the row. `verified_at` is set
    by exactly three human/UI doors and was read by NOTHING — the column existed,
    the distinction existed, and the read path threw it away. Consulting it here
    is the whole fix: a distilled lesson a human later confirmed stops being
    presented as the model's own unverified guess.

    Nothing is refused. That is deliberate: the distiller has no probe step, so
    it can never *earn* the distinction harness2 requires (an execution isolating
    one cause), only disclose it. Marking is the honest half of a lift
    measurement August does not have.
    """
    title = str(row.get('title') or '').strip()
    body = str(row.get('body') or '').strip()
    if len(body) > entry_cap:
        body = body[:entry_cap].rstrip() + '…'
    label = title or str(row.get('key') or '')
    verified = bool(str(row.get('verified_at') or '').strip())
    mark = (
        ''
        if verified
        else (_DISTILLED_MARK if str(row.get('source') or '') == _DISTILLED_SOURCE else '')
    )
    text = f'{mark}{label}: {body}' if body else f'{mark}{label}'
    return f'- {text}'


def _row_identity(row: dict[str, object]) -> tuple[str, str]:
    """``(key, title)`` — the pair usage feedback and the receipt both speak."""
    return (str(row.get('key') or ''), str(row.get('title') or '').strip())


def _fit_lines(
    rows: list[dict[str, object]], budget: int
) -> tuple[list[str], list[dict[str, object]], list[tuple[str, str]]]:
    """Budget-pack already-ranked fact lines: ``(lines, included, omitted)``.

    Rank order is preserved — the first line that does not fit stops the lane
    (a later short line never jumps ahead of a dropped stronger one) and
    everything from there on comes back in ``omitted``, so the caller can
    NAME it instead of vanishing it. The first line always ships (the legacy
    rule): dropping the strongest match silently is worse than one
    over-budget line.
    """
    lines: list[str] = []
    included: list[dict[str, object]] = []
    omitted: list[tuple[str, str]] = []
    cost = 0
    for i, row in enumerate(rows):
        line = _fact_line(row)
        if lines and cost + len(line) + 1 > budget:
            omitted = [_row_identity(r) for r in rows[i:]]
            break
        cost += len(line) + 1
        lines.append(line)
        included.append(row)
    return lines, included, omitted


def _omitted_receipt(omitted: list[tuple[str, str]], max_chars: int, *, label: str) -> str:
    """The budget-honesty line: how many entries were dropped, and WHICH.

    A model shown a partial recall with no receipt concludes the omitted
    facts do not exist. Bounded by ``max_chars`` (as many titles named as
    fit, then ``(+N more)``), and it degrades to a count-only line under a
    tiny budget rather than dropping the receipt entirely.
    """
    if not omitted or max_chars <= 0:
        return ''
    names = [title or key or '(untitled)' for key, title in omitted]

    def _render(kept: int) -> str:
        more = len(names) - kept
        base = f'{label}: {len(names)} omitted for budget'
        shown = '; '.join(names[:kept])
        if kept and more:
            return f'{base}: {shown} (+{more} more)'
        if kept:
            return f'{base}: {shown}'
        return f'{base} (titles truncated)'

    for kept in range(len(names), -1, -1):
        line = _render(kept)
        if len(line) <= max_chars:
            return line
    return _render(0)[: max(0, max_chars - 1)] + '…'


def _rank_profile_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Order profile rows with the EXISTING recall signals — no new scoring.

    The store's newest-first ``updated_at`` sequence, then the same decayed
    ``use_count`` boost :func:`retrieve_relevant_facts` applies
    (``0.05 * min(use_count, 20) * _usage_decay``) as a stable sort key, so an
    often-quoted profile fact leads the lane and a never-quoted one falls back
    to plain recency.
    """
    ranked = sorted(rows, key=lambda r: str(r.get('updated_at') or ''), reverse=True)
    usage = _usage_for([str(r.get('key') or '') for r in ranked])

    def _boost(row: dict[str, object]) -> float:
        use_count, last_used_at = usage.get(str(row.get('key') or ''), (0, ''))
        return 0.05 * min(use_count, 20) * _usage_decay(last_used_at)

    return sorted(ranked, key=_boost, reverse=True)


def build_profile_block(
    *,
    scope: str = 'global',
    budget_chars: int = _PROFILE_CHAR_CAP,
) -> tuple[str, list[dict[str, object]]]:
    """The always-on "who the user is" lane — never keyword-gated.

    A fact about the *user* must not have to win a BM25 slot against every
    other fact to be recalled: "user is a backend engineer in Seoul" shares no
    tokens with "why is my connection pool exhausting", so the keyword lane can
    surface it only by accident (the reported "August does not remember me").
    This lane returns the active, unexpired ``kind='profile'`` facts visible to
    ``scope`` with NO query at all.

    One authority per rule: the rows come from the same cached per-scope corpus
    :func:`_load_index` builds for BM25 — i.e. the same active + unexpired +
    ``global ∪ bot:<id>`` visibility, no second store, no second scope
    resolver — ordered by the existing recency/usage signals
    (:func:`_rank_profile_rows`) and bounded by its own explicit
    ``budget_chars``. A profile fact that does not fit is NAMED in the returned
    text, never silently dropped — and when a budget is too small to carry both
    the bound and that receipt, the receipt wins (an honest over-long lane
    beats a tidy one that hides facts).

    Returns ``(lane_text, rows)`` where ``rows`` are the structured corpus
    entries actually included (``key``/``title``/``body``/``kind``/
    ``category``/``scope``), so the caller can fold them into its own
    injected/usage lists without re-deriving anything. ``('', [])`` when the
    store holds no profile facts.
    """
    index = _load_index(scope)
    corpus = index.get('rows') or []
    profileRows = [
        r for r in corpus if str(r.get('kind') or '') in (PROFILE_FACT_KIND, 'preference')
    ]
    if not profileRows:
        return '', []
    ranked = _rank_profile_rows(profileRows)
    budget = max(0, int(budget_chars))
    lineBudget = max(0, budget - (len(_PROFILE_LANE_HEADER) + 1))
    lines, included, omitted = _fit_lines(ranked, lineBudget)
    if omitted:
        # Re-pack once with room reserved for the receipt that names them —
        # a receipt that itself overflows the lane is not honest, just longer.
        # Reserve what a receipt NEEDS to say something (_OMITTED_RECEIPT_MIN_ROOM),
        # not its full cap, so the lane never wastes budget reserving text it
        # will not write.
        lines, included, omitted = _fit_lines(
            ranked, max(0, lineBudget - _OMITTED_RECEIPT_MIN_ROOM)
        )
    usedChars = len(_PROFILE_LANE_HEADER) + 1 + sum(len(line) + 1 for line in lines)
    # The receipt gets whatever room the lane has left — except under a budget
    # too small to carry even a count-only line, where it takes its full cap:
    # a silently missing profile fact is the exact failure this lane exists to
    # fix, so honesty outranks tidiness and the lane goes long instead.
    receiptRoom = budget - usedChars
    if omitted and receiptRoom < _OMITTED_RECEIPT_MIN_ROOM:
        receiptRoom = _OMITTED_RECEIPT_CHAR_CAP
    receipt = _omitted_receipt(omitted, receiptRoom, label='profile lane partial')
    parts = [_PROFILE_LANE_HEADER, *lines]
    if receipt:
        parts.append(receipt)
    return '\n'.join(parts), [dict(r) for r in included]


def build_profile_memory_block(
    *,
    scope: str = 'global',
    budget_chars: int = _PROFILE_CHAR_CAP,
) -> tuple[str, list[dict[str, object]]]:
    """The profile lane as a ready-to-inject tail section.

    ``build_profile_block`` returns lane *content*; this wraps it in the plain
    header a standalone tail section needs, so the model reads durable memory as
    its own section rather than as part of the user's sentence. One place
    defines that header.
    """
    # The tail is a plain section, not an XML envelope: no model is trained on
    # `<memory>`, so the tag bought tokens and could invite the model to read
    # the block as third-party metadata. The header says what the section is;
    # the producer owns the wording (DSH's rule, which this follows).
    lane, rows = build_profile_block(scope=scope, budget_chars=budget_chars)
    if not lane:
        return '', rows
    return f'Relevant memory:\n{lane}', rows


def build_memory_delta_block(
    *,
    session_started_at: str = '',
    scope: str = 'global',
    budget_chars: int = _DELTA_CHAR_CAP,
    exclude_keys: frozenset[str] | set[str] | None = None,
) -> str:
    """Facts written after this session opened, as a plain tail section.

    A projection over ``facts.updated_at``, not a second store and not a
    cursor: the rows are read live on every turn, so a retired fact cannot be
    resurrected and an UPDATE to an old key is delivered like a new write. The
    section rides the tail (patched onto the last user message and stripped
    before persistence), so the cached prefix is untouched.

    ``session_started_at`` is the session's ISO creation stamp. Empty or
    unparseable is a REFUSAL, not "since the beginning of time": a bad stamp
    must not dump the whole store into the tail. The comparison keeps
    microseconds (see ``timeutil``), so a write in the session's opening
    second is still newer than the opening.
    """
    since = _sqlite_utc(session_started_at)
    budget = max(0, int(budget_chars))
    headerCost = len(_DELTA_HEADER) + 1
    if not since or budget <= headerCost:
        return ''
    try:
        from app.services.memory_store.rest import _visible

        if scope and scope != 'global':
            union = scope
        else:
            union = 'global'
        candidates = [
            row
            for row in record.load_facts()
            if _visible(row, union)
            # Fixed-width UTC text sorts chronologically; string comparison
            # keeps microseconds, which the SQL era's julianday() rounded to
            # milliseconds.
            and str(row.get('updated_at') or '') > since
            and as_int(row.get('sensitive'), 0) == 0
            and str(row.get('subject_type') or '') != 'person'
        ]
        # The SQL ORDER BY (updated_at DESC, id DESC) — same deterministic
        # tie-break list_facts pages by.
        candidates.sort(
            key=lambda r: (str(r.get('updated_at') or ''), as_int(r.get('id'), 0)),
            reverse=True,
        )
        rows = candidates[:_DELTA_MAX_FACTS]
    except Exception as exc:
        # A transient read failure must not look like "nothing was written";
        # next turn retries, and the warning names the degradation.
        logging.warning('memory delta read failed; no new-facts section this turn: %s', exc)
        return ''
    lines: list[str] = []
    cost = headerCost
    skipped = exclude_keys or frozenset()
    for r in rows:
        if as_int(r.get('sensitive'), 0) or sensitiveMemoryReason(
            str(r.get('title') or ''), str(r.get('fact_key') or ''),
            str(r.get('description') or ''), _fact_body_text(r.get('fact_value')),
        ):
            continue
        row: dict[str, object] = {
            'key': str(r.get('fact_key') or ''),
            'title': str(r.get('title') or '').strip(),
            'body': _fact_body_text(r.get('fact_value')),
            'source': str(r.get('source') or ''),
            'verified_at': str(r.get('verified_at') or ''),
            'kind': str(r.get('kind') or 'fact'),
            'category': str(r.get('category') or 'general'),
            'scope': str(r.get('scope') or 'global'),
        }
        if row['key'] in skipped:
            continue
        line = _fact_line(row)
        # The same legacy rule as _fit_lines: the newest write always ships,
        # even when it alone exceeds the budget.
        if lines and cost + len(line) + 1 > budget:
            break
        cost += len(line) + 1
        lines.append(line)
    if not lines:
        return ''
    return '\n'.join([f'{_DELTA_HEADER}:', *lines])


def _sqlite_utc(raw: str) -> str:
    """Normalise an ISO-8601 stamp to SQLite's UTC ``YYYY-MM-DD HH:MM:SS.ffffff``.

    Session stamps are ``datetime.now(timezone.utc).isoformat()...Z``; facts
    store the fixed-width microsecond stamp from ``timeutil``. Both are UTC but
    their spellings differ, so a lexical comparison would silently compare the
    wrong things. The full six fractional digits are kept because a fact
    written in the same microsecond as the session must still sort after it.
    """
    from datetime import datetime, timezone

    text = (raw or '').strip()
    if not text:
        return ''
    try:
        normalized = text[:-1] + '+00:00' if text.endswith('Z') else text
        stamp = datetime.fromisoformat(normalized)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
    except (ValueError, TypeError):
        return ''


def memory_context_preview(
    query: str = '',
    workspace: str = '',
    scope: str = 'global',
    session_started_at: str = '',
) -> dict[str, object]:
    """Render the durable-memory text a turn receives.

    The Memory settings page lists stored ROWS; this returns the rendered
    SECTION — the same bytes :mod:`app.services.workbench.workbench` injects,
    produced by the same builders. Retrieval is pull-only now, so the automatic
    turn text is the always-in profile lane plus the `new in memory since this
    chat opened` delta; there is no ranked tail to preview. Re-formatting rows
    into a prettier view here would make Settings a second authority on what
    the model sees, which is the one thing a preview must not be.

    The delta is a projection over writes made since a session opened, and a
    preview has no session unless the caller passes ``session_started_at``. With
    the default empty stamp :func:`build_memory_delta_block` refuses (it must
    not dump the whole store), so the preview shows the profile lane alone.

    ``bootIndex`` and ``projectBlock`` are the session-start pair. Both are
    frozen per session in a live chat, so this is what a NEW session would
    start with — and ``modelMemoryRead`` is reported rather than applied here,
    because the tool-gating that suppresses the index lives in one other place
    (``workbench`` builds it only when the memory read tools are offered).
    """
    from app.services import brain_config_service as _bc

    try:
        cfg = _bc.getRuntimeConfig()
    except Exception:
        cfg = {}

    turnBlock, laneRows = build_profile_memory_block(scope=scope)
    injected = [
        (str(r.get('key') or ''), str(r.get('title') or '')) for r in laneRows
    ]
    deltaBlock = build_memory_delta_block(
        session_started_at=session_started_at,
        scope=scope,
        exclude_keys={k for k, _ in injected},
    )
    if deltaBlock:
        turnBlock = f'{turnBlock}\n\n{deltaBlock}' if turnBlock else deltaBlock

    bootIndex = ''
    projectBlock = ''
    try:
        from app.services.memory_store.brain import brain_index_snippet

        bootIndex = brain_index_snippet(scope).strip()
    except Exception:
        logging.debug('memory preview boot index failed', exc_info=True)
    if workspace and bool(cfg.get('projectMemory', True)):
        try:
            from app.services import project_memory as _pm

            projectBlock = _pm.project_block(workspace)
        except Exception:
            logging.debug('memory preview project block failed', exc_info=True)

    return {
        'query': (query or '').strip(),
        'workspace': workspace,
        'scope': scope,
        'modelMemoryRead': bool(cfg.get('modelMemoryRead', True)),
        'turnBlock': turnBlock,
        'bootIndex': bootIndex,
        'projectBlock': projectBlock,
        'injectedFacts': [{'key': k, 'title': t} for k, t in injected if k or t],
    }
