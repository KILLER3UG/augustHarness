"""Facts, proposals, lifecycle, topics, usage, timeline, stats — file-backed.

Every public signature is unchanged from the SQL era; the engine underneath is
``filestore`` + the markdown record (``mirror.py``). Facts live one file per
fact under ``memory/``; the smaller tables live as JSON row stores under
``state/`` and JSONL ledgers under ``telemetry/`` (``tables.py`` names every
home). Rows keep their snake_case column names; ``_row_as_wire`` camelCases on
the way out exactly as it did when the columns were SQLite columns.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import cast

from app.json_narrowing import as_int, as_str
from app.services.hooks.secret_guard import scan_secrets
from app.services.memory_store import mirror as record
from app.services.memory_store import tables
from app.services.memory_store.timeutil import utc_now_text
from app.services.memory_store.wire import _json, _row_as_wire
from app.services.sensitive_topics import sensitiveMemoryReason
from app.type_aliases import FactDict, JsonValue, ProposalDict

logger = logging.getLogger('august.memory_store.rest')

# The one durable-entry-kind vocabulary (the write door validates against it;
# retrieval reads from it). '' on a write means "unspecified" — see save_fact.
#
# 'profile' (2026-09-15) is the always-on "who the user is" lane: a fact about
# the user must not compete with every other fact for a keyword-matched BM25
# slot, so recall for this kind is never gated on lexical overlap — see
# fact_retrieval.build_profile_block. The record frontmatter carries the kind
# as plain text, so this frozenset (plus the `remember` tool's schema enum) is
# the whole surface.
PROFILE_FACT_KIND = 'profile'
_FACT_KINDS = frozenset({'fact', 'lesson', 'preference', 'skill-note', 'person', PROFILE_FACT_KIND})

# The record lock: every read-modify-write over a fact file holds it, which is
# what replaces SQLite's write lock for the CAS (if_updated_at) semantics.
_RECORD_LOCK_NAME = 'facts.record'


def _record_lock():
    return tables.store_lock(_RECORD_LOCK_NAME)


def _as_row_int(value: object, default: int = 0) -> int:
    """An int column read off a record row (the SQL era read these as typed
    columns; the JSON files hold whatever a hand edit or the import wrote)."""
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return default


class FactVersionConflict(ValueError):
    """An ``if_updated_at`` write lost its compare-and-swap; nothing was written.

    Carries the row as it stands NOW so the caller can merge and retry inside the
    same turn rather than overwrite a change it never read. It subclasses
    ``ValueError`` because every existing caller already treats that as "this
    write was refused" — a caller that has not learned the new class still refuses
    the write instead of treating it as success.
    """

    def __init__(self, factKey: str, current: FactDict) -> None:
        super().__init__(
            f'fact "{factKey}" changed since the version this write expected; nothing was written'
        )
        self.factKey = factKey
        self.current = current


def derive_fact_title(text: str) -> str:
    """Short human label for a fact (readability ruling 2026-08-26: titled
    entries, not raw blobs). First sentence/clause, capped at 60 chars."""
    cleaned = ' '.join((text or '').split())
    for sep in ('. ', '! ', '? ', '; ', ' — ', ': '):
        at = cleaned.find(sep)
        if 8 <= at <= 80:
            cleaned = cleaned[:at]
            break
    if len(cleaned) > 60:
        cleaned = cleaned[:60].rstrip() + '…'
    return cleaned


def _expired(row: dict[str, object]) -> bool:
    """julianday semantics: an unparseable expires_at never expires (the old
    ``julianday(...) IS NOT NULL AND ... <= now`` predicate kept such rows)."""
    raw = str(row.get('expires_at') or '')
    if not raw:
        return False
    from datetime import datetime, timezone

    text = raw.strip()
    try:
        normalized = text[:-1] + '+00:00' if text.endswith('Z') else text
        stamp = datetime.fromisoformat(normalized)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp <= datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def _visible(
    row: dict[str, object],
    scope: str,
    *,
    include_retired: bool = False,
) -> bool:
    """2.2: the shared read-visibility predicate for the facts store —
    active + unexpired + the M-2 scope union (global ∪ this-scope). Every
    non-ranked facts read (list_facts / search_facts) must apply it, or a Bot
    sees every other Bot's private keys plus superseded/retired/expired rows.

    ``include_retired`` drops ONLY the status test, for the model's audit
    door: a row parked by consolidation is still stored memory, and an actor
    that cannot see it can neither correct it nor explain why the store looks
    empty. Expiry and scope still apply — those describe whose memory it is,
    not whether it is parked.
    """
    from app.services.session_scope import GLOBAL_SCOPE, normalize_scope

    s = normalize_scope(scope)
    rowScope = str(row.get('scope') or 'global')
    if s == GLOBAL_SCOPE:
        if rowScope != GLOBAL_SCOPE:
            return False
    elif rowScope not in (GLOBAL_SCOPE, s):
        return False
    if not include_retired and str(row.get('status') or 'active') != 'active':
        return False
    return not _expired(row)


_TAG_VALUES = ('stated', 'observed', 'inferred')
# Other people's details stay out of the automatic index/tail unless the user
# raises them. A relation word is reliable; a possessive proper name is a
# heuristic, with sentence-start/common words excluded so "Today's plan" or
# "The user's editor" is not a person fact.
_THIRD_PARTY_REL_RE = re.compile(
    r'\b(?:my|our)\s+(?:friend|colleague|coworker|co-worker|boss|manager|partner|wife|husband|son|daughter|child|kid|mother|father|brother|sister|neighbor|neighbour|roommate)\b',
    re.IGNORECASE,
)
_THIRD_PARTY_POSSESSIVE_RE = re.compile(
    r"\b(?!Today|This|That|It|He|She|They|We|You|The|User)[A-Z][a-z]+'s\b"
)


def _thirdPartyDetail(*texts: object) -> str:
    blob = ' '.join(str(t) for t in texts if t)
    match = _THIRD_PARTY_REL_RE.search(blob) or _THIRD_PARTY_POSSESSIVE_RE.search(blob)
    return match.group(0) if match else ''


_SUBJECT_TYPES = ('profile', 'preference', 'topic', 'area', 'person')


def _deriveFactTag(source: str) -> str:
    """The record's provenance tag, derived from the writer's code path.

    ``stated`` is the chat agent's and the human's door; ``inferred`` is the
    distiller/promotion path; ``observed`` is a bench/import observation.
    Never a model-supplied value — a tag the model can write is a claim
    nothing can check.
    """
    src = (source or '').strip().lower()
    if src in ('user', 'model', ''):
        return 'stated'
    if src == 'bench' or src.startswith('imported'):
        return 'observed'
    return 'inferred'


def _deriveSubjectType(kind: str, category: str) -> str:
    k = (kind or '').strip().lower()
    c = (category or '').strip().lower()
    if k == 'profile':
        return 'profile'
    if k == 'preference':
        return 'preference'
    if k == 'person' or c == 'person':
        return 'person'
    if k == 'project' or c == 'project':
        return 'area'
    return 'topic'


def _normalizedStringList(values: object, *, cap: int = 8, item_cap: int = 60) -> list[str]:
    if isinstance(values, str):
        raw: list[object] = [v.strip() for v in values.split(',')]
    elif isinstance(values, (list, tuple)):
        raw = list(values)
    else:
        raw = []
    out: list[str] = []
    for v in raw:
        s = ' '.join(str(v or '').split())[:item_cap]
        if s and s.lower() not in {x.lower() for x in out}:
            out.append(s)
        if len(out) >= cap:
            break
    return out


def _factRow(row: dict[str, object]) -> FactDict:
    """A fact row with the record shape normalized on read.

    Legacy rows or hand-edited files can leave ``tag`` / ``subject_type``
    empty; derive the honest value from the fields that already encode it
    rather than printing a blank marker. ``aliases`` and ``derivedFrom`` are
    stored as JSON text and handed out as lists.
    """
    d = cast(dict[str, object], _row_as_wire(row))
    if not d.get('tag'):
        d['tag'] = _deriveFactTag(str(d.get('source') or ''))
    if not d.get('subjectType'):
        d['subjectType'] = _deriveSubjectType(
            str(d.get('kind') or ''), str(d.get('category') or '')
        )
    for field in ('aliases', 'derivedFrom'):
        raw = d.get(field)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                parsed = []
            d[field] = parsed if isinstance(parsed, list) else []
    d['sensitive'] = bool(d.get('sensitive'))
    return cast(FactDict, d)


def get_fact(factKey: str) -> FactDict | None:
    """Get a fact by key (the global home first, then any scoped row)."""
    row = record.load_fact(factKey)
    if row is None:
        return None
    return _factRow(row)


def _get_row_scoped(factKey: str, scope: str) -> dict[str, object] | None:
    """The row whose identity is exactly (scope, key) — the upsert target."""
    wanted_scope = str(scope or 'global')
    exact = record.read_fact_file(record.fact_path(factKey, wanted_scope))
    if exact is not None and str(exact.get('fact_key') or '') == factKey:
        return exact
    for row in record.load_facts():
        if str(row.get('fact_key') or '') == factKey and str(row.get('scope') or 'global') == wanted_scope:
            return row
    return None


def save_fact(
    factKey: str,
    factValue: JsonValue,
    category: str = 'general',
    source: str = '',
    confidence: float = 1.0,
    expires_at: str | None = None,
    title: str = '',
    kind: str = '',
    scope: str = 'global',
    allow_scope_override: bool = False,
    description: str = '',
    verified: bool = False,
    if_updated_at: str = '',
    aliases: list[str] | tuple[str, ...] | str | None = None,
    subject_type: str = '',
    tag: str = '',
    source_session: str = '',
    sensitive: bool | None = None,
    derived_from: list[str] | tuple[str, ...] | str | None = None,
) -> None:
    """Save a structured fact. ``expires_at`` (ISO-8601 TEXT) is optional; the
    cognitive boot sweep purges facts whose expiry has passed.

    Upsert over the row's identity — ``(scope, fact_key)`` since 055, when
    ``fact_key`` stopped being unique store-wide. An update keeps the row id,
    ``created_at`` and usage counters, and only overwrites ``title``/``kind``/
    ``description`` when the caller actually supplies them (plan §3.3 — facts
    are titled, typed entries; 044 adds the ZCode-parity one-line recall
    ``description`` hook).

    M-2 (Part 21): ``scope`` ('global' | 'bot:<agentId>') stamps the row's
    memory home on INSERT, and is normalized here — the same normalizer
    retrieval uses — so a scope nothing can produce cannot be written either.
    An update never rewrites scope — a fact keeps the home it was born in.
    Part 26 6.2 (tightened): a key has exactly one home *per scope*. A write is
    only accepted when it comes from the row's own scope: a bot may update a
    GLOBAL fact by re-affirming it through the model doors
    *in a global session*, but a ``bot:<id>``-scoped write against a
    GLOBAL row is refused (it would otherwise replace the shared value while
    the row stayed ``global``), and a write against a row from a DIFFERENT
    private scope is likewise refused.

    Part 26 6.5: a write whose caller scope differs from the existing row's
    NON-GLOBAL scope is refused (raises ``ValueError``) instead of silently
    overwriting another bot's private value under its original scope —
    reachable from the Settings-UI manage endpoint, bulk import, and rollback
    restore. Callers that legitimately move/merge rows across scopes
    (consolidation) pass ``allow_scope_override=True``.

    2.4 (Part 25): an upsert resets ``status`` to 'active' — re-remembering a
    key that consolidation had superseded/retired must revive it, or the model
    believes it saved while retrieval keeps filtering the stale row out.

    ``if_updated_at`` turns the write into a compare-and-swap: the row is only
    updated while its ``updated_at`` still equals that stamp (or its ``version``
    still equals that integer). The stamp is the one ``list_facts`` and the
    Memory page already hand out and it carries microseconds (see timeutil).
    A mismatch raises ``FactVersionConflict`` with the row as it stands now; an
    ``if_updated_at`` against no row at all is refused, because the token is a
    claim that the entry exists — letting it insert would write a row the
    caller believes it is updating.
    """
    from app.lib.paths import assertPytestDataDirIsolated
    from app.services.session_scope import GLOBAL_SCOPE, normalize_scope

    # The per-writer isolation guard `save_episode`, `save_internal` and the
    # workbench session writer all call. `save_fact` — the door with the most
    # callers and the only one that can fill the always-on profile lane — was
    # the one without it. The `dataDir()` hard raise added on 2026-09-26 is what
    # stopped the leak; this names the site so the next one is greppable rather
    # than inferred.
    assertPytestDataDirIsolated('memory_store.save_fact')

    # Scope is normalized here, once, by the only authority — a caller cannot
    # write a scope retrieval could never produce (see normalize_scope).
    scope = normalize_scope(scope)

    if_updated_at = (if_updated_at or '').strip()
    if not allow_scope_override:
        existing = get_fact(factKey)
        if existing is not None:
            existingScope = str(existing.get('scope') or GLOBAL_SCOPE)
            requested = normalize_scope(scope)
            if existingScope != GLOBAL_SCOPE and requested != existingScope:
                raise ValueError(
                    f'fact "{factKey}" belongs to scope "{existingScope}"; '
                    f'refusing a write from scope "{requested}"'
                )
            # Part 26 6.2 (tightened): a bot-scoped write must NOT overwrite a
            # GLOBAL fact. The upsert never rewrites ``scope``, so a
            # ``bot:<id>`` write against a global row used to replace the
            # shared value while the row stayed ``global`` — the bot's private
            # note silently became every session's global fact, and the
            # original shared value was lost. A key has exactly one home: to
            # write a global key, write it from a global scope; a bot that
            # wants its own value must use a distinct key. Callers that
            # legitimately move/merge rows across scopes (consolidation,
            # rollback restore) pass ``allow_scope_override=True``.
            if requested != GLOBAL_SCOPE and existingScope == GLOBAL_SCOPE:
                raise ValueError(
                    f'fact "{factKey}" is a global fact; refusing to overwrite it '
                    f'from scope "{requested}" — write the global key from a '
                    'global scope, or use a distinct key for this scope.'
                )
    # The record shape is server-stamped. `tag` names the writer's code path
    # (never the model's claim); `subject_type` is the one-word identity the
    # index/UI groups by; `sensitive` is a durable copy of the write-door
    # detector so a read door can filter without re-running the regex.
    tagValue = (tag or '').strip().lower()
    if tagValue not in _TAG_VALUES:
        tagValue = _deriveFactTag(source)
    subjectValue = (subject_type or '').strip().lower()
    # 2026-10-27: the ZCode memory vocabulary is accepted as input aliases
    # (user -> profile, feedback -> preference, project -> area,
    # reference -> topic) so prompts written in that dialect land canonically.
    subjectValue = record.normalize_subject_type(subjectValue)
    if subjectValue not in _SUBJECT_TYPES:
        subjectValue = _deriveSubjectType(kind, category)
    _valueTextForType = factValue if isinstance(factValue, str) else str(factValue or '')
    if subjectValue != 'person' and _thirdPartyDetail(_valueTextForType, title or '', description or ''):
        subjectValue = 'person'
    aliasesJson = _json(_normalizedStringList(aliases))
    derivedJson = _json(_normalizedStringList(derived_from, cap=8, item_cap=80))
    _valueText = factValue if isinstance(factValue, str) else str(factValue or '')
    sensitiveFlag = 1 if (
        sensitive
        if sensitive is not None
        else sensitiveMemoryReason(_valueText, title or '', description or '')
    ) else 0

    # A durable fact is re-injected into later turns — the <memory> tail and the
    # always-in profile lane carry whatever is stored here out to every provider
    # request. A credential saved as a fact is therefore published onward
    # indefinitely, and read back into any session that unions the scope, so the
    # shape is refused at this one door (the same door that refuses
    # project-scoped rows and cross-scope overwrites). Labels only, never the
    # matched value, matching hooks/secret_guard.scan_secrets' own contract.
    _secret_hits = scan_secrets(' '.join([
        factValue if isinstance(factValue, str) else str(factValue or ''),
        title or '',
        description or '',
    ]))
    if _secret_hits:
        raise ValueError(
            f'refusing to store secret-shaped content in durable memory '
            f'({", ".join(_secret_hits)}); keep credentials in a provider '
            'config or a secret store, not in a fact'
        )

    kindParam = kind if kind in _FACT_KINDS else ''
    # '' = unspecified: fresh inserts default to 'fact', updates keep the
    # existing kind (a remember-without-kind must not downgrade a lesson).
    value_text = _json(factValue)
    now = utc_now_text()

    with _record_lock():
        stored_row = _get_row_scoped(factKey, scope)
        if if_updated_at:
            if stored_row is None:
                raise ValueError(
                    f'no stored fact "{factKey}" matches version "{if_updated_at}": '
                    'there is nothing to update. Drop if_updated_at to create, or '
                    're-read the key list first.'
                )
            # The guard belongs INSIDE the write. Reading the stamp and then
            # writing when it still matches leaves a window in which another
            # writer moves the row and this write clobbers it — the exact race
            # being closed. The lock above closes it; the token still has to
            # MATCH what the caller read. `if_version` accepts the microsecond
            # `updated_at` stamp (the original token) or the integer `version`.
            _versionToken = if_updated_at.isdigit()
            currentStamp = str(stored_row.get('updated_at') or '')
            currentVersion = str(stored_row.get('version') or 1)
            if _versionToken:
                matched = currentVersion == if_updated_at
            else:
                matched = currentStamp == if_updated_at
            if not matched:
                conflictCurrent = get_fact(factKey) or cast('FactDict', {})
                raise FactVersionConflict(factKey, conflictCurrent)

        row: dict[str, object]
        if stored_row is None:
            with tables.store_lock('record.ids'):
                rows_all = record.load_facts()
                row_id = tables.next_id([dict(r) for r in rows_all])
            row = {
                'id': row_id,
                'fact_key': factKey,
                'fact_value': value_text,
                'title': (title or '').strip(),
                'kind': kindParam or 'fact',
                'description': ' '.join((description or '').split())[:200],
                'category': category,
                'source': source,
                'confidence': confidence,
                'expires_at': expires_at or '',
                'status': 'active',
                'scope': scope,
                'verified_at': utc_now_text() if verified else '',
                'updated_at': now,
                'created_at': now,
                'aliases': aliasesJson,
                'subject_type': subjectValue,
                'tag': tagValue,
                'source_session': (source_session or '').strip(),
                'sensitive': sensitiveFlag,
                'version': 1,
                'derived_from': derivedJson,
                'previous_value': '',
                'use_count': 0,
                'last_used_at': '',
                'injected_count': 0,
                'last_injected_at': '',
                'superseded_by': '',
                'supersedes': '[]',
                'contradicts': '',
            }
        else:
            row = dict(stored_row)
            old_value = str(row.get('fact_value') or '')
            row['fact_value'] = value_text
            if (title or '').strip():
                row['title'] = title.strip()
            if kindParam:
                row['kind'] = kindParam
            if ' '.join((description or '').split())[:200]:
                row['description'] = ' '.join((description or '').split())[:200]
            row['category'] = category
            row['source'] = source
            row['confidence'] = confidence
            row['expires_at'] = expires_at or ''
            row['status'] = 'active'
            if aliasesJson != '[]':
                row['aliases'] = aliasesJson
            if subjectValue:
                row['subject_type'] = subjectValue
            if tagValue:
                row['tag'] = tagValue
            if (source_session or '').strip():
                row['source_session'] = source_session.strip()
            row['sensitive'] = 1 if (sensitiveFlag == 1 or _as_row_int(row.get('sensitive')) == 1) else 0
            row['version'] = _as_row_int(row.get('version'), 1) + 1
            # 059: in-place edit history. The value this update REPLACES is
            # kept on the row (one deep — the immediately prior state), so an
            # edit reads as "now X, previously Y" rather than a silent
            # overwrite. Unchanged content keeps whatever history it had.
            if old_value != value_text:
                row['previous_value'] = old_value
            # M-8: a RE-STATEMENT is the only verification this store can
            # observe. `verified` is the caller's claim that a HUMAN wrote
            # or restated the fact; a model's `remember`, a consolidation
            # merge and a rollback restore leave the existing stamp alone.
            if verified:
                row['verified_at'] = utc_now_text()
            row['updated_at'] = now

    record.write_fact_file(row)
    try:
        from app.services.memory_store.fact_retrieval import invalidate_fact_index

        invalidate_fact_index(scope)
    except Exception:
        pass
    # The MEMORY.md index is maintained as a write side effect; a failure
    # there is logged and never blocks the store write.
    record.note_fact_changed(factKey)


def touch_fact_usage(factKeys: list[str]) -> int:
    """Increment ``use_count`` / ``last_used_at`` for referenced facts.

    M3 usage feedback: an injected fact the model quotes (or updates via
    ``remember``) is marked as used; BM25 retrieval gives a small rank boost
    to high-use entries. Returns the number of rows touched. Since M-1's
    usage decoupling this does NOT touch the cached BM25 corpus — ranking
    reads fresh usage per query.
    """
    if not factKeys:
        return 0
    wanted = [k for k in ((key or '').strip() for key in factKeys) if k]
    if not wanted:
        return 0
    touched = 0
    with _record_lock():
        for row in record.load_facts():
            if str(row.get('fact_key') or '') not in wanted:
                continue
            row['use_count'] = _as_row_int(row.get('use_count')) + 1
            row['last_used_at'] = utc_now_text()
            record.write_fact_file(row)
            touched += 1
    # M-1 usage decoupling: NO invalidate_fact_index here.
    # The cached corpus no longer carries use_count/last_used_at —
    # ranking fetches fresh usage per query for the candidate set — so
    # a touch no longer triggers the full-corpus rebuild cliff.
    return touched


def _set_injected(row: dict[str, object]) -> None:
    row['injected_count'] = _as_row_int(row.get('injected_count')) + 1
    row['last_injected_at'] = utc_now_text()
    record.write_fact_file(row)


def search_facts(query: str, category: str = '', scope: str = 'global') -> list[FactDict]:
    """Search facts by key or value (visible-to-``scope`` only — 2.2).

    The SQL-era LIKE was case-insensitive for ASCII; the term scan keeps that
    so a model searching "GPU" still finds "gpu setup".
    """
    needle = (query or '').lower()
    out: list[FactDict] = []
    for row in record.load_facts():
        if category and str(row.get('category') or '') != category:
            continue
        if not _visible(row, scope):
            continue
        hay = ' '.join([
            str(row.get('fact_key') or ''),
            str(row.get('fact_value') or ''),
        ]).lower()
        if needle in hay:
            out.append(_factRow(row))
    out.sort(key=lambda r: str(r.get('updatedAt') or r.get('updated_at') or ''), reverse=True)
    return out[:20]


def list_facts(
    category: str = '',
    scope: str = 'global',
    limit: int = 500,
    offset: int = 0,
    include_retired: bool = False,
) -> list[FactDict]:
    """List facts visible to ``scope`` (active + unexpired — 2.2), optionally
    filtered by category.

    ``offset`` is what makes the store ENUMERABLE: a model that can only ever
    read the newest N rows cannot audit memory it wrote earlier, so a capped
    first page used to be the whole story. ``include_retired`` adds the rows
    consolidation parked (see :func:`_visible`).
    """
    lim = max(1, min(int(limit or 500), 2000))
    off = max(0, int(offset or 0))
    rows = [
        row
        for row in record.load_facts()
        if (not category or str(row.get('category') or '') == category)
        and _visible(row, scope, include_retired=include_retired)
    ]
    # `id DESC` is the tie-break the SQL ORDER BY carried: legacy rows share
    # second-resolution stamps, so equals need a deterministic order or paging
    # duplicates one row and drops another.
    rows.sort(
        key=lambda r: (str(r.get('updated_at') or ''), _as_row_int(r.get('id'))),
        reverse=True,
    )
    return [_factRow(r) for r in rows[off : off + lim]]


def count_facts(
    category: str = '',
    scope: str = 'global',
    include_retired: bool = False,
) -> int:
    """Total rows :func:`list_facts` would page through — so a caller can say
    "3 of 57" instead of implying its page is the store."""
    return sum(
        1
        for row in record.load_facts()
        if (not category or str(row.get('category') or '') == category)
        and _visible(row, scope, include_retired=include_retired)
    )


def update_fact_value(
    factKey: str,
    factValue: JsonValue,
    category: str = '',
    title: str = '',
    verified: bool = False,
) -> bool:
    """Change an EXISTING fact's value in place — the partial write door.

    ``save_fact`` is an upsert, so it also rewrites what a value change has no
    business touching: ``expires_at`` clears a TTL, and ``status`` revives a
    row consolidation had parked. A door whose UI shows the user the value it
    is about to replace may change that value and nothing else. Returns
    ``False`` when the key is not there, so the caller can fall back to a
    create.

    The refusals ``save_fact`` makes apply here too, because this is a second
    way into the same store: a non-global row is not writable from the settings
    door, and secret-shaped content is refused (facts are re-injected into
    later turns, so a credential saved here is published onward).
    """
    from app.lib.paths import assertPytestDataDirIsolated
    from app.services.session_scope import GLOBAL_SCOPE

    assertPytestDataDirIsolated('memory_store.update_fact_value')

    existing = get_fact(factKey)
    if existing is None:
        return False
    existingScope = str(existing.get('scope') or GLOBAL_SCOPE)
    if existingScope != GLOBAL_SCOPE:
        raise ValueError(
            f'fact "{factKey}" belongs to scope "{existingScope}"; a partial '
            'update from this door is refused'
        )

    hits = scan_secrets(' '.join([
        factValue if isinstance(factValue, str) else str(factValue or ''),
        title or '',
    ]))
    if hits:
        raise ValueError(
            f'refusing to store secret-shaped content in durable memory '
            f'({", ".join(hits)}); keep credentials in a provider config or a '
            'secret store, not in a fact'
        )

    stripped = (title or '').strip()
    with _record_lock():
        row = _get_row_scoped(factKey, existingScope)
        if row is None:
            return False
        row['fact_value'] = _json(factValue)
        if stripped:
            row['title'] = stripped
        if category:
            row['category'] = category
        if verified:
            row['verified_at'] = utc_now_text()
        row['updated_at'] = utc_now_text()
        record.write_fact_file(row)
    try:
        from app.services.memory_store.fact_retrieval import invalidate_fact_index

        invalidate_fact_index()
    except Exception:
        pass
    return True


def _superseded_keys(raw: object) -> list[str]:
    """The keys a fact replaced, read back from the ``supersedes`` field.

    Tolerant on purpose: the field is a JSON array in a text column, and a
    row written by something else (or hand-edited) must not make the delete
    door throw.
    """
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x)]
    if isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        if isinstance(loaded, list):
            return [str(x) for x in loaded if str(x)]
    return []


def restore_superseded_losers(factKey: str) -> list[str]:
    """Bring back the entries ``factKey`` replaced (M-3).

    A superseded row is KEPT, not deleted — so the content that lost to a
    newer statement is still stored. But if the WINNER is deleted, those rows
    would stay parked forever with a link to a fact that no longer exists:
    invisible to recall, invisible to the UI's active list, and never
    proposed for anything. The content would be gone in every sense except
    that the bytes are on disk.

    So deleting a winner restores what it replaced: the status flips back to
    ``active`` and the lineage is cleared. The user asked for one memory to
    go; the entries that memory had replaced are a different memory, and
    bringing them back is the conservative reading.

    Returns the keys that were restored (empty when the fact replaced
    nothing, or when the field is not there).
    """
    try:
        row = record.load_fact(factKey)
    except Exception:
        return []
    if row is None:
        return []
    losers = [k for k in _superseded_keys(row.get('supersedes')) if k and k != factKey]
    if not losers:
        return []
    restored: list[str] = []
    try:
        now = utc_now_text()
        with _record_lock():
            for candidate in record.load_facts():
                key = str(candidate.get('fact_key') or '')
                if key not in losers:
                    continue
                # The same predicate the SQL UPDATE carried: parked rows
                # (and legacy NULL-status rows) come back; an already-active
                # row is not touched.
                status = candidate.get('status')
                if status is not None and str(status) not in ('superseded', 'contested', 'archived'):
                    continue
                candidate['status'] = 'active'
                candidate['superseded_by'] = ''
                candidate['contradicts'] = ''
                candidate['updated_at'] = now
                record.write_fact_file(candidate)
                restored.append(key)
        if restored:
            try:
                from app.services.memory_store.fact_retrieval import invalidate_fact_index

                invalidate_fact_index()
            except Exception:
                pass
    except Exception:
        logger.debug('superseded restore failed', exc_info=True)
    return restored


def dependent_facts(factKey: str) -> list[FactDict]:
    """Every fact derived SOLELY from ``factKey`` (transitively).

    A row is a dependent only when every key in its ``derived_from`` list is
    already in the forgotten set — a row that also derives from a surviving
    parent is a different memory and stays. The walk repeats because deleting
    a dependent can make ITS children solely derived. Returns the rows to
    delete, children last (callers delete in order).
    """
    try:
        rows = [
            row
            for row in record.load_facts()
            if str(row.get('derived_from') or '') not in ('', '[]')
        ]
    except Exception:
        return []
    deleted = {factKey}
    out: list[FactDict] = []
    changed = True
    while changed:
        changed = False
        for row in rows:
            try:
                parents = json.loads(str(row.get('derived_from') or '[]'))
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(parents, list) or not parents:
                continue
            key = str(row.get('fact_key') or '')
            if not key or key in deleted:
                continue
            if {str(p) for p in parents} <= deleted:
                deleted.add(key)
                out.append(_factRow(row))
                changed = True
    return out


def delete_fact(factKey: str) -> bool:
    """Delete a fact by key.

    M-3: the entries this fact REPLACED are restored first (see
    :func:`restore_superseded_losers`) — a winner that is deleted must not
    leave its losers parked against a fact that no longer exists.
    """
    restore_superseded_losers(factKey)
    removed = False
    with _record_lock():
        for row in record.load_facts():
            if str(row.get('fact_key') or '') != factKey:
                continue
            record.delete_fact_file(
                str(row.get('fact_key') or ''), str(row.get('scope') or 'global')
            )
            removed = True
    if removed:
        try:
            from app.services.memory_store.fact_retrieval import invalidate_fact_index

            invalidate_fact_index()
        except Exception:
            pass
        record.note_fact_changed(factKey)
    return removed


def save_proposal(sessionId: str, proposalType: str, content: JsonValue) -> int:
    """Save a proposal (plan, mutation, etc.)."""
    path = tables.proposals_file()
    with tables.store_lock(str(path)):
        rows = tables.load_rows(path)
        row_id = tables.next_id(rows)
        rows.append({
            'id': row_id,
            'session_id': sessionId,
            'proposal_type': proposalType,
            'content': _json(content),
            'status': 'pending',
            'created_at': utc_now_text(),
            'decided_at': None,
            'decided_by': None,
        })
        tables.save_rows(path, rows)
    return row_id


def get_proposal(proposalId: int) -> ProposalDict | None:
    for row in tables.load_rows(tables.proposals_file()):
        if as_int(row.get('id'), 0) == int(proposalId):
            return cast(ProposalDict, _row_as_wire(row))
    return None


def list_proposals(sessionId: str, status: str = '') -> list[ProposalDict]:
    """List proposals for a session, optionally filtered by status."""
    rows = [
        row
        for row in tables.load_rows(tables.proposals_file())
        if str(row.get('session_id') or '') == sessionId
        and (not status or str(row.get('status') or '') == status)
    ]
    rows.sort(key=lambda r: str(r.get('created_at') or ''), reverse=True)
    return [cast(ProposalDict, _row_as_wire(row)) for row in rows]


def claim_proposal_decision(
    conn: object | None,
    proposalId: int,
    status: str,
    decidedBy: str = '',
    from_status: str | None = 'pending',
) -> bool:
    """Move a proposal out of ``from_status``, WITHOUT committing.

    This is the guard, not a convenience: the update matches only rows still
    in ``from_status``, so a proposal is decided once. A second approver, a
    retry after a crash, or an approval of something already rejected, gets
    False and changes nothing. ``decide_proposal`` below is the one-call
    version; a flow that also writes facts calls this one so the claim and
    the write share one decision. Pass ``from_status=None`` for an
    unconditional rewrite.

    The SQL era took the caller's connection so claim + write shared a
    transaction; the file store serializes on the proposals store lock
    instead, and ``conn`` is accepted-and-ignored so every existing call
    site keeps its shape.
    """
    del conn  # the SQL-era transaction handle — the store lock replaces it
    path = tables.proposals_file()
    with tables.store_lock(str(path)):
        rows = tables.load_rows(path)
        for row in rows:
            if as_int(row.get('id'), 0) != int(proposalId):
                continue
            if from_status is not None and str(row.get('status') or '') != from_status:
                return False
            row['status'] = status
            row['decided_at'] = utc_now_text()
            row['decided_by'] = decidedBy
            tables.save_rows(path, rows)
            return True
    return False


def decide_proposal(proposalId: int, status: str, decidedBy: str = '') -> bool:
    """Decide (approve/reject) a proposal. False means it was already decided."""
    return claim_proposal_decision(None, proposalId, status, decidedBy)


def record_lifecycle(sessionId: str, eventType: str, detail: JsonValue = None) -> int:
    """Record a lifecycle event."""
    path = tables.lifecycle_file()
    with tables.store_lock(str(path)):
        rows = tables.ledger_rows(path)
        row_id = tables.next_id(rows)
        tables.append_ledger(
            path,
            {
                'id': row_id,
                'session_id': sessionId,
                'event_type': eventType,
                'detail': _json(detail) if detail else None,
                'created_at': utc_now_text(),
            },
            max_bytes=tables._LIFECYCLE_MAX_BYTES,
        )
    # P4.2: lifecycle rows are diagnostics, not read back
    # cross-thread within the turn — the SQL era debounced the commit; the
    # file write is already durable (append + fsync).
    return row_id


def list_lifecycle(sessionId: str, eventType: str = '', limit: int = 100) -> list[dict[str, object]]:
    """List lifecycle events for a session."""
    rows = [
        row
        for row in tables.ledger_rows(tables.lifecycle_file())
        if str(row.get('session_id') or '') == sessionId
        and (not eventType or str(row.get('event_type') or '') == eventType)
    ]
    rows.sort(key=lambda r: (str(r.get('created_at') or ''), as_int(r.get('id'), 0)), reverse=True)
    return [_row_as_wire(row) for row in rows[: max(1, int(limit))]]


def record_config_audit(
    category: str, action: str, actor: str = '', before: JsonValue = None, after: JsonValue = None
) -> int:
    """Record a structured config-change audit entry.

    Used by alias, fallback, and agent mutation paths so that every
    self-configuration change is traceable.
    """
    path = tables.config_audit_file()
    with tables.store_lock(str(path)):
        rows = tables.load_rows(path)
        row_id = tables.next_id(rows)
        rows.append({
            'id': row_id,
            'category': category,
            'action': action,
            'actor': actor,
            'before_json': _json(before) if before is not None else None,
            'after_json': _json(after) if after is not None else None,
            'created_at': utc_now_text(),
        })
        tables.save_rows(path, rows)
    return row_id


def list_config_audit(category: str = '', limit: int = 200) -> list[dict[str, object]]:
    """List config-change audit entries, newest first."""
    rows = [
        row
        for row in tables.load_rows(tables.config_audit_file())
        if not category or str(row.get('category') or '') == category
    ]
    rows.sort(key=lambda r: (str(r.get('created_at') or ''), as_int(r.get('id'), 0)), reverse=True)
    results = []
    for r in rows[: max(1, int(limit))]:
        entry: dict[str, object] = {
            'id': r.get('id'),
            'category': r.get('category'),
            'action': r.get('action'),
            'actor': r.get('actor') or '',
            'createdAt': r.get('created_at'),
        }
        for rawKey, outKey in (('before_json', 'before'), ('after_json', 'after')):
            raw = r.get(rawKey)
            if isinstance(raw, str):
                try:
                    entry[outKey] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    entry[outKey] = raw
            else:
                entry[outKey] = raw
        results.append(entry)
    return results


def index_session_topic(
    sessionId: str, topic: str, parentTopic: str | None = None, confidence: float = 0.75
) -> bool:
    """Record or update the topic for a session."""
    path = tables.session_topics_file()
    try:
        with tables.store_lock(str(path)):
            rows = tables.load_rows(path)
            for row in rows:
                if str(row.get('session_id') or '') == sessionId:
                    row['topic'] = topic
                    row['parent_topic'] = parentTopic
                    row['confidence'] = confidence
                    row['classified_at'] = utc_now_text()
                    break
            else:
                rows.append({
                    'session_id': sessionId,
                    'topic': topic,
                    'parent_topic': parentTopic,
                    'confidence': confidence,
                    'classified_at': utc_now_text(),
                })
            tables.save_rows(path, rows)
        return True
    except Exception:
        return False


def get_session_topic(sessionId: str) -> dict[str, object] | None:
    """Get the classified topic for a session."""
    for row in tables.load_rows(tables.session_topics_file()):
        if str(row.get('session_id') or '') == sessionId:
            return _row_as_wire(row)
    return None


def list_topics(limit: int = 50) -> list[dict[str, object]]:
    """List all classified session topics, most recent first."""
    rows = tables.load_rows(tables.session_topics_file())
    rows.sort(key=lambda r: str(r.get('classified_at') or ''), reverse=True)
    return [_row_as_wire(row) for row in rows[: max(1, int(limit))]]


def search_sessions_by_topic(topic: str) -> list[dict[str, object]]:
    """Find sessions with a given topic classification."""
    rows = [
        row
        for row in tables.load_rows(tables.session_topics_file())
        if str(row.get('topic') or '') == topic
    ]
    rows.sort(key=lambda r: str(r.get('classified_at') or ''), reverse=True)
    return [_row_as_wire(row) for row in rows]


def resolve_sot_session_id(sessionId: str) -> str:
    """Prefer a workbench / sessions-table SoT id when recording or looking up usage.

    Both branches of the SQL-era lookup returned the same stripped id (keep it
    when a session row exists, return it unchanged otherwise), so the
    resolution is identity over the trimmed input — kept as a named function
    because the usage writers read their intent through it.
    """
    return (sessionId or '').strip()


def record_usage(
    sessionId: str,
    model: str,
    inputTokens: int = 0,
    outputTokens: int = 0,
    contextTokens: int = 0,
    cacheHitTokens: int = 0,
    cacheMissTokens: int = 0,
    turn_id: int | None = None,
) -> int:
    """Record a usage event against the session SoT id when known.

    ``contextTokens`` captures the provider-reported ``inputTokens`` of the
    FINAL sub-call in the agentic turn — i.e. the true current context fill
    (system prompt + tools + messages, counted once). The cumulative
    ``inputTokens``/``outputTokens`` are still recorded for Usage-page totals.

    ``cacheHitTokens``/``cacheMissTokens`` carry the universal prompt-cache
    split (Anthropic cache_read/cache_creation vs OpenAI-compatible
    prompt_cache_hit/miss) so the UI can show the cache hit rate.

    ``turn_id`` is the 053 join key: the turn-outcome row this usage belongs
    to. It was the missing link between "what this call cost" and "how the
    turn ended", which is why no query could rank models or tools by cost per
    success. ``None`` means "no turn row" (a direct API call, or a turn whose
    telemetry write failed), never zero.
    """
    sot_id = resolve_sot_session_id(sessionId)
    path = tables.usage_events_file()
    with tables.store_lock(str(path)):
        rows = tables.ledger_rows(path)
        row_id = tables.next_id(rows)
        tables.append_ledger(
            path,
            {
                'id': row_id,
                'session_id': sot_id,
                'model': model,
                'input_tokens': inputTokens,
                'output_tokens': outputTokens,
                'context_tokens': contextTokens,
                'cache_hit_tokens': cacheHitTokens,
                'cache_miss_tokens': cacheMissTokens,
                'turn_id': turn_id,
                'created_at': utc_now_text(),
            },
            max_bytes=tables._USAGE_EVENTS_MAX_BYTES,
        )
    return row_id


def list_usage(*, limit: int = 200) -> list[dict[str, object]]:
    """List recent usage events (newest first), for GET /api/usage."""
    lim = max(1, min(int(limit), 1000))
    rows = tables.ledger_rows(tables.usage_events_file())
    rows.sort(key=lambda r: (str(r.get('created_at') or ''), as_int(r.get('id'), 0)), reverse=True)
    return [
        _row_as_wire({
            'id': r.get('id'),
            'session_id': r.get('session_id'),
            'model': r.get('model'),
            'input_tokens': r.get('input_tokens'),
            'output_tokens': r.get('output_tokens'),
            'context_tokens': r.get('context_tokens'),
            'created_at': r.get('created_at'),
        })
        for r in rows[:lim]
    ]


#: Most session ids one batched sidebar read will ask about. The sidebar caps
#: what it renders before calling (GROUP_RENDER_CAP per group), so this is a
#: backstop against an unbounded batch rather than the normal size.
MAX_USAGE_BATCH_IDS = 250


def get_usage_batch(session_ids: Sequence[str]) -> dict[str, dict[str, object]]:
    """Input/output token totals for MANY sessions in ONE pass.

    The sidebar renders a ``+input / -output`` delta on every visible chat row.
    Asking :func:`get_usage` per row cost one HTTP request per row AND, behind
    each one, a per-event pricing sweep — the list's own render was driving
    all of it. This is the single grouped pass that replaces the N.

    Keyed by the id the caller ASKED FOR, not a resolved one, so the caller can
    index the result directly. Ids with no usage rows are simply absent (an
    absent row and a zero row mean the same thing to a delta readout, and
    emitting zeros for every unseen id would grow the payload for no fact).
    """
    ids: list[str] = []
    seen: set[str] = set()
    for raw in session_ids:
        sid = (raw or '').strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        ids.append(sid)
        if len(ids) >= MAX_USAGE_BATCH_IDS:
            break
    if not ids:
        return {}
    wanted = set(ids)
    totals: dict[str, dict[str, int]] = {}
    for r in tables.ledger_rows(tables.usage_events_file()):
        key = as_str(r.get('session_id'), '')
        if key not in wanted:
            continue
        bucket = totals.setdefault(key, {'events': 0, 'in': 0, 'out': 0})
        bucket['events'] += 1
        bucket['in'] += as_int(r.get('input_tokens'), 0)
        bucket['out'] += as_int(r.get('output_tokens'), 0)
    out: dict[str, dict[str, object]] = {}
    for key, bucket in totals.items():
        total_in = bucket['in']
        total_out = bucket['out']
        out[key] = {
            'sessionId': key,
            'totalEvents': bucket['events'],
            'totalInputTokens': total_in,
            'totalOutputTokens': total_out,
            'totalTokens': total_in + total_out,
        }
    return out


def get_usage(sessionId: str) -> dict[str, object]:
    """Get aggregated usage for a session.

    Returns cumulative totals (for the Usage page) plus ``latestContextTokens``
    — the ``contextTokens`` of the most recent usage event: the FULL prompt
    of the final sub-call of the latest turn (uncached + cache-read +
    cache-creation), i.e. the true current context fill. Earlier rows
    recorded only the raw ``input_tokens``, which on the Anthropic shape
    excludes cache tokens and under-reports a warm session. Also returns the
    per-event list ordered newest-first so the caller can derive the same
    value independently.
    """
    sessionId = resolve_sot_session_id(sessionId)
    rows = [
        r
        for r in tables.ledger_rows(tables.usage_events_file())
        if str(r.get('session_id') or '') == sessionId
    ]
    rows.sort(key=lambda r: (str(r.get('created_at') or ''), as_int(r.get('id'), 0)), reverse=True)
    total_in = sum(as_int(r.get('input_tokens'), 0) for r in rows)
    total_out = sum(as_int(r.get('output_tokens'), 0) for r in rows)
    cache_hit = sum(as_int(r.get('cache_hit_tokens'), 0) for r in rows)
    cache_miss = sum(as_int(r.get('cache_miss_tokens'), 0) for r in rows)
    latestCtx = 0
    if rows:
        latestCtx = as_int(rows[0].get('context_tokens'), 0) or as_int(rows[0].get('input_tokens'), 0)
    events = [
        {
            'id': r.get('id'),
            'model': r.get('model'),
            'inputTokens': as_int(r.get('input_tokens'), 0),
            'outputTokens': as_int(r.get('output_tokens'), 0),
            'contextTokens': as_int(r.get('context_tokens'), 0) or as_int(r.get('input_tokens'), 0),
            'totalTokens': as_int(r.get('input_tokens'), 0) + as_int(r.get('output_tokens'), 0),
            'createdAt': r.get('created_at'),
        }
        for r in rows[:500]
    ]
    cache_total = cache_hit + cache_miss
    # Cost: per-event sum using the shared pricing table (the composer chip,
    # the spend ceiling, and this endpoint must agree). Never raises.
    total_cost = 0.0
    cost_estimated = False
    try:
        from app.services.cost_estimator import session_cost_estimate

        for r in rows:
            cost, estimated = session_cost_estimate(
                model_id=as_str(r.get('model'), ''),
                total_in=as_int(r.get('input_tokens'), 0),
                total_out=as_int(r.get('output_tokens'), 0),
                cache_hit=as_int(r.get('cache_hit_tokens'), 0),
                cache_miss=as_int(r.get('cache_miss_tokens'), 0),
            )
            total_cost += cost
            # One guessed price in a mixed-model session makes the whole sum a
            # guess; one exact price cannot make the others exact.
            cost_estimated = cost_estimated or estimated
        total_cost = round(total_cost, 4)
    except Exception:
        total_cost = 0.0
    return {
        'sessionId': sessionId,
        'totalEvents': len(rows),
        'totalInputTokens': total_in,
        'totalOutputTokens': total_out,
        'totalTokens': total_in + total_out,
        'cacheHitTokens': cache_hit,
        'cacheMissTokens': cache_miss,
        'cacheHitRate': round(cache_hit / cache_total, 3) if cache_total else 0.0,
        'totalCost': total_cost,
        'costEstimated': cost_estimated,
        'model': events[0]['model'] if events else None,
        'provider': None,
        'contextTokens': latestCtx,
        'latestContextTokens': latestCtx,
        'events': events,
    }


def get_stats() -> dict[str, object]:
    """Get store statistics.

    Keys are camelCase table aliases for wire compatibility (e.g. memoryStore).
    """
    from app.lib.paths import dataPath

    def _count_jsonl(path) -> int:
        try:
            return sum(1 for line in path.read_text(encoding='utf-8').splitlines() if line.strip())
        except OSError:
            return 0

    def _count_json(path) -> int:
        data = tables.load_rows(path)
        return len(data)

    sessions_rows: list[dict[str, object]] = []
    sessions_file = dataPath('transcripts') / 'sessions.json'
    data = filestore_read_json(sessions_file)
    if isinstance(data, list):
        sessions_rows = [r for r in data if isinstance(r, dict)]
    messages = 0
    transcripts = dataPath('transcripts')
    if transcripts.is_dir():
        for child in transcripts.glob('*.jsonl'):
            messages += _count_jsonl(child)
    stats: dict[str, object] = {
        'memoryStore': _count_json(tables.kv_file()),
        'facts': record.record_stats()['rows'],
        'proposals': _count_json(tables.proposals_file()),
        'sessions': len(sessions_rows),
        'messages': messages,
        'usageEvents': _count_jsonl(tables.usage_events_file()),
        'sessionTopics': _count_json(tables.session_topics_file()),
    }
    total_bytes = 0
    for home in ('memory', 'state', 'telemetry', 'transcripts'):
        directory = dataPath(home)
        if directory.is_dir():
            for child in directory.rglob('*'):
                try:
                    if child.is_file():
                        total_bytes += child.stat().st_size
                except OSError:
                    continue
    stats['db_size_bytes'] = total_bytes
    return stats


def filestore_read_json(path) -> object:
    from app.services import filestore

    return filestore.read_json(path, default=[])


def _usage_by_model_export() -> list[dict[str, object]]:
    """Per-model call/token totals for the privacy export (GROUP BY model)."""
    rows = tables.ledger_rows(tables.usage_events_file())
    groups: dict[str, dict[str, int]] = {}
    for r in rows:
        model = str(r.get('model') or '')
        bucket = groups.setdefault(model, {'calls': 0, 'in': 0, 'out': 0})
        bucket['calls'] += 1
        bucket['in'] += as_int(r.get('input_tokens'), 0)
        bucket['out'] += as_int(r.get('output_tokens'), 0)
    out = [
        {
            'model': model,
            'calls': bucket['calls'],
            'inputTokens': bucket['in'],
            'outputTokens': bucket['out'],
        }
        for model, bucket in groups.items()
    ]
    out.sort(key=lambda item: -as_int(item['calls'], 0))
    return out[:100]


def write_timeline_event(
    sessionId: str | None, eventSummary: str, category: str = 'general', scope: str = 'global'
) -> int:
    """v2: Append an entry to episodic_timeline. Returns the new row's id.

    ``sessionId`` is optional — system events (e.g. memory lifecycle) have
    no owning session. ``scope`` (Part 27 T1) fences the entry so a Bot's
    private timeline never rides into a global session's boot index.
    """
    path = tables.timeline_file()
    with tables.store_lock(str(path)):
        rows = tables.load_rows(path)
        row_id = tables.next_id(rows)
        rows.append({
            'id': row_id,
            'timestamp': utc_now_text(),
            'session_id': sessionId,
            'event_summary': eventSummary,
            'category': category,
            'scope': scope or 'global',
        })
        tables.save_rows(path, rows)
    return row_id
