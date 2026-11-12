"""M5 — turn outcomes as telemetry, not memory (plan 2026-08-27 §3.6).

The deleted turn-lessons failed because free-text failure rules are not
actionable memory. This module records one structured row per turn into the
turn-outcomes ledger (``<dataDir>/telemetry/turn-outcomes.jsonl`` — one JSON
line per finished turn, 30-day retention) and exposes the stats consumers:
routing evidence and the harness self-improve "error rate by model" stat.
Rows are never injected into prompts and never shown in the Memory UI —
diagnostics only (Observability hub).

The ledger row carries the same field spellings the ``turn_outcomes`` table
used, so every reader's dict access is unchanged; ``id`` stays a monotonic
join key (053: ``usage_events.turn_id``, ``decision_ledger.turn_id``).

The single path from failure to memory is ``maybe_promote_failure_lesson``:
when one failure signature repeats, ONE typed lesson fact may be written —
gated by a cheap-model review (discard-default) and a BM25 dedupe filter
against existing facts (Q2 ruling). Three failure classes feed it, each in its
own signature space: the upstream ``error_class``, the edit-verification gate
(``edit_verify_fails``) and the tool guardrails (``guardrail_classes``) — the
latter two only became countable when 046 gave a turn its model/provider
attribution for them.

046 also made the turn verdict durable: ``end_reason`` / ``rounds`` are the
persisted twin of the ``turn_end {reason, rounds}`` SSE event, and
``malformed_tool_args`` / ``surface_downgraded`` are the persisted twin of the
loop's turn-scoped self-correction counters. None on any of them means NOT
RECORDED (older row, or the value was never measured) — never 0.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from app.json_narrowing import as_int

# Audit D8 (2026-09-26): family_for_class joins this recorder's
# (signature-stable) error classes onto the loop's error-family vocabulary so
# ``error_class`` rows and ``guardrail_classes`` digests are diagnosable
# against each other. The vocabulary itself lives in error_families.
from app.services.error_families import family_for_class  # noqa: F401  (re-export)
from app.services.filestore import TELEMETRY_DIR, append_jsonl, home, load_jsonl, rewrite_jsonl

# One dedupe threshold for both automatic memory doors — this promotion path and
# the distiller's `memory` verdict — so they cannot disagree about what "we
# already know this" means. It lives beside the bar that reads it.
from app.services.memory_quality import DEDUPE_SIMILARITY as _DEDUPE_SIMILARITY

logger = logging.getLogger(__name__)

_LEDGER_FILE = 'turn-outcomes.jsonl'
# Guards read-modify-write cycles on the ledger (append / sweep / read-back).
_LOCK = threading.RLock()
# Cached parsed rows keyed by the ledger file's stat, so the aggregate reads
# (Learning panel, Observability) do not re-parse the file on every poll.
_CACHE: dict[str, tuple[tuple[int, int], list[dict[str, object]]]] = {}
_STAMP_FMT = '%Y-%m-%d %H:%M:%S'

_RETENTION_DAYS = 30
# Repeated-failure threshold within the window before promotion is allowed.
_PROMOTE_MIN_FAILURES = 3
_PROMOTE_WINDOW_DAYS = 7
# One promotion per signature per cooldown — no lesson spam.
_PROMOTE_COOLDOWN_DAYS = 7
_LESSON_CHAR_CAP = 300
# (Q2-b) Candidate lessons too similar to an existing fact are discarded; the
# shared threshold is imported above from `memory_quality`.

# ── failure classes (promotion signature spaces) ─────────────────────────
# Which ledger field carries the evidence for one candidate lesson. The
# classes NEVER share a signature space: an ``edit_file`` guardrail block and
# an ``edit_file``-flavoured error class promote two independent facts.
KIND_ERROR = 'error'
KIND_EDIT_VERIFY = 'edit_verify'
KIND_GUARDRAIL = 'guardrail'
FAILURE_KINDS = (KIND_ERROR, KIND_EDIT_VERIFY, KIND_GUARDRAIL)

# The vocabulary of ``turn_end.reason`` (workbench.py:5235). Kept here as a
# note to readers, NOT as a write-time allow-list: a reason the loop learns
# after this file stopped changing must still reach the row instead of being
# silently dropped.
TURN_END_REASONS = (
    'finished',
    'length',
    'cap',
    'stall-stop',
    'budget',
    'error',
    'interrupted',
    'awaiting-input',
)


def turn_ok(end_reason: str | None, errored: bool) -> bool | None:
    """Did this turn succeed? Judged by WHY IT STOPPED, not by whether it raised.

    ``None`` means "neither a success nor a failure" and MUST be written as
    JSON null — never 0 — so an unclassified stop is not silently counted as an
    error.

    ``ok`` used to be ``turnError is None``. A turn that ends ``stall-stop``,
    ``length``, ``budget``, ``cap`` or ``awaiting-input`` raises no exception, so
    every one of them was recorded as a CLEAN SUCCESS. That made both consumers
    of the field wrong in the direction that matters:

    * ``skill_lift`` computes ok-rate-with minus ok-rate-without as a skill's
      measured effect;
    * ``error_rate_by_model`` ranks models by their counted failures.

    So the harness's own self-improvement and observability surfaces were blind
    to precisely the failure modes it built stall detection, budget ladders and
    length continuations to fix — a stall-stopping turn raised a model's
    measured error rate by zero.

    Mapping:
      * ``finished``                     -> True
      * ``awaiting-input``               -> None  (the human owes an answer)
      * ``length``/``cap``/``stall-stop``/``budget``/``interrupted`` -> False
      * ``error``                        -> False
      * no reason recorded, no exception -> None  (unmeasured, not a success)
    """
    if errored:
        return False
    reason = (end_reason or '').strip().lower()
    if not reason:
        return None
    if reason == 'finished':
        return True
    if reason == 'awaiting-input':
        return None
    return False


@dataclass(frozen=True)
class FailureClass:
    """One candidate lesson signature: which class failed, and what it looked like.

    ``kind`` is one of :data:`FAILURE_KINDS`; ``label`` refines it inside that
    space (the error class for ``error``, the blocked tool for ``guardrail``,
    '' for ``edit_verify``); ``sample`` is the evidence text quoted in the
    lesson, capped by the caller.
    """

    kind: str = KIND_ERROR
    label: str = ''
    sample: str = ''

_ERROR_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ('cancelled', re.compile(r'cancel', re.IGNORECASE)),
    ('rate_limit', re.compile(r'\[?429\]?|rate.?limit|too many requests', re.IGNORECASE)),
    (
        'auth',
        re.compile(r'\[?(?:401|403)\]?|api.?key|unauthori[sz]ed|forbidden|invalid.+key', re.IGNORECASE),
    ),
    (
        'context_overflow',
        re.compile(r'context.?length|maximum context|too long|exceed.+context|token.+limit', re.IGNORECASE),
    ),
    ('timeout', re.compile(r'timed? ?out|deadline', re.IGNORECASE)),
    ('not_found', re.compile(r'\[?404\]?|not found', re.IGNORECASE)),
    (
        'bad_request',
        # `invalid[_ ]` because the provider's own error TYPE is snake-cased
        # (`invalid_request_error`) and gateways that pass the envelope without a
        # numeric status were classifying as `other` — which hid a refused
        # request shape from every reader of this field, including the
        # stuck-request ladder that exists to repair it.
        re.compile(r'\[?400\]?|invalid[_ ](?:input|request|parameter)|bad request', re.IGNORECASE),
    ),
    (
        'upstream_5xx',
        re.compile(
            r'\[?5\d\d\]?|internal server error|bad gateway|service unavailable|overloaded',
            re.IGNORECASE,
        ),
    ),
    ('network', re.compile(r'connection|network|dns|refused|reset', re.IGNORECASE)),
    # Audit D8 (2026-09-26): the loop steered on a process_exit family while
    # this recorder had no such class — the shell telling you the truth never
    # reached the measured record. Last (most conservative) match: a traceback
    # riding a more specific upstream error keeps that error's class.
    (
        'process_exit',
        re.compile(
            r'exit code[: ]+[1-9]|exited with code|nonzero exit|traceback \(most recent call last\)',
            re.IGNORECASE,
        ),
    ),
]


def classify_error(errorText: str) -> str:
    """Map a raw turn error string onto a stable error class ('' when ok)."""
    text = (errorText or '').strip()
    if not text:
        return ''
    for label, pattern in _ERROR_PATTERNS:
        if pattern.search(text):
            return label
    return 'other'


def _nullable_int(value: object) -> int | None:
    """Store a counter, or None when it was never measured.

    Deliberately NOT ``int(value or 0)``: that folds "unmeasured" into
    "measured zero", and the panel's whole honesty claim is that a turn whose
    counters were never recorded is not reported as a clean turn.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


def _nullable_text(value: object) -> str | None:
    """Same rule for the text fields: None (not recorded) stays None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or ''


_NEW_COLUMNS = (
    'end_reason',
    'rounds',
    'malformed_tool_args',
    'surface_downgraded',
    'edit_verify_fails',
    'guardrail_classes',
    'skills_injected',
    'skills_loaded',
    'facts_injected',
    'error_families',
    # E4 expect receipts: how many `_expect` verdicts the turn produced and
    # how many mismatched. Absent from the row = the turn used no
    # receipts — never conflated with "all receipts passed".
    'receipts_total',
    'receipts_failed',
)


def _nullable_json_list(value: list[str] | None) -> str | None:
    """JSON-encode a name list, or None when it was never recorded.

    ``None`` → None (pre-050 callers / unrecorded); ``[]`` → ``'[]'``
    (measured and empty). Every name is stringified and deduplicated so an
    aggregate cannot double-count.
    """
    if value is None:
        return None
    names = sorted({str(v) for v in value if str(v)})
    return json.dumps(names, ensure_ascii=False)


def _ledger_path() -> Path:
    return home(TELEMETRY_DIR) / _LEDGER_FILE


def _stat_stamp(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
        return (stat.st_size, int(stat.st_mtime * 1000))
    except OSError:
        return (0, 0)


def _load_rows() -> list[dict[str, object]]:
    """All ledger rows (oldest first), stat-cached; never raises."""
    path = _ledger_path()
    stamp = _stat_stamp(path)
    with _LOCK:
        hit = _CACHE.get('rows')
        if hit and hit[0] == stamp:
            return hit[1]
    rows: list[dict[str, object]] = []
    for raw in load_jsonl(path):
        if isinstance(raw, dict):
            rows.append(raw)
    with _LOCK:
        _CACHE['rows'] = (stamp, rows)
    return rows


def _invalidate_cache() -> None:
    with _LOCK:
        _CACHE.pop('rows', None)


def _stamp(epoch_s: float | None = None) -> str:
    return time.strftime(_STAMP_FMT, time.gmtime(epoch_s))


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(0, days))).strftime(_STAMP_FMT)


def _next_row_id(rows: list[dict[str, object]]) -> int:
    best = 0
    for row in rows:
        try:
            value = as_int(row.get('id'), 0)
        except (TypeError, ValueError):
            continue
        if value > best:
            best = value
    return best + 1


def _in_window(row: dict[str, object], cutoff: str) -> bool:
    return str(row.get('ts') or '') >= cutoff


def _population(row: dict[str, object], task_type: str | None) -> bool:
    """The population an aggregate describes.

    The subagent fanout writes its own durable row into this same ledger with
    ``task_type='subagent_fanout'`` and ``model=''`` — a 1-3 round fanout
    aggregate, not a user turn. Nothing downstream filtered on ``task_type``, so
    those rows were averaged into the same ``reasons`` histogram and the same
    round average as 20-round main turns, and the Learning panel's "avg rounds"
    was a blend of two populations that mean different things.

    Default is therefore "everything except the fanout", which is what the
    Learning panel is about. An explicit ``taskType`` selects one population
    exactly, including the fanout, so the excluded rows stay reachable rather
    than hidden. One predicate shared by every reader here, so they cannot drift
    apart the way the aggregates did.
    """
    actual = str(row.get('task_type') or '')
    if task_type is None:
        return actual != 'subagent_fanout'
    return actual == str(task_type)


def record_turn_outcome(
    model: str,
    provider: str,
    task_type: str,
    ok: bool | None,
    error_class: str = '',
    duration_ms: int = 0,
    session_id: str = '',
    ttft_ms: int = 0,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
    tool_args_ready_to_stream_end_ms: int = 0,
    end_reason: str | None = None,
    rounds: int | None = None,
    malformed_tool_args: int | None = None,
    surface_downgraded: bool | int | None = None,
    edit_verify_fails: int | None = None,
    guardrail_classes: str | None = None,
    skills_injected: list[str] | None = None,
    skills_loaded: list[str] | None = None,
    facts_injected: list[str] | None = None,
    error_families: list[str] | None = None,
    receipts_total: int | None = None,
    receipts_failed: int | None = None,
) -> int | None:
    """Append one telemetry row. Best-effort: never raises into the turn.

    Returns the new row's id (or None when the write failed), which is the
    join key 053 adds to ``usage_events`` and ``decision_ledger``.

    Phase L (Part 17): ``ttft_ms`` + the prompt-cache token split make
    latency regressions measurable per turn — "chat feels slow" becomes
    "first token 42 s, cache hit 0 / miss 29k" instead of a vibe.
    P3.1 (Part 18): ``tool_args_ready_to_stream_end_ms`` measures the
    trailing stream tail AFTER the last tool call's arguments arrived —
    the time early tool dispatch could save. 0 = no tool call this turn.
    046: the verdict + self-correction counters at the tail. Every one of
    them is ``None`` = NOT RECORDED and lands as JSON null, so an older row
    and a turn that measured nothing stay distinguishable from a turn that
    measured a zero.
    """
    try:
        with _LOCK:
            rows = _load_rows()
            row: dict[str, object] = {
                'id': _next_row_id(rows),
                'ts': _stamp(),
                'model': model or '',
                'provider': provider or '',
                'task_type': task_type or '',
                # None stays null (unmeasured), never 0. `ok` is no longer just
                # "did it raise" — see turn_ok(), the single authority for it.
                'ok': None if ok is None else (1 if ok else 0),
                'error_class': error_class or '',
                'duration_ms': int(duration_ms or 0),
                'session_id': session_id or '',
                'ttft_ms': int(ttft_ms or 0),
                'cache_hit_tokens': int(cache_hit_tokens or 0),
                'cache_miss_tokens': int(cache_miss_tokens or 0),
                'tool_args_ready_to_stream_end_ms': int(tool_args_ready_to_stream_end_ms or 0),
                'end_reason': _nullable_text(end_reason),
                'rounds': _nullable_int(rounds),
                'malformed_tool_args': _nullable_int(malformed_tool_args),
                'surface_downgraded': _nullable_int(surface_downgraded),
                'edit_verify_fails': _nullable_int(edit_verify_fails),
                'guardrail_classes': _nullable_text(guardrail_classes),
                'skills_injected': _nullable_json_list(skills_injected),
                'skills_loaded': _nullable_json_list(skills_loaded),
                'facts_injected': _nullable_json_list(facts_injected),
                'error_families': _nullable_json_list(error_families),
                'receipts_total': _nullable_int(receipts_total),
                'receipts_failed': _nullable_int(receipts_failed),
            }
            # fsync on append: the packaged quit is taskkill /F, so this line
            # must be on disk before the function returns (filestore rule 4).
            append_jsonl(_ledger_path(), row)
            _invalidate_cache()
            # The row id is the join key every other per-turn table now carries
            # (053: ``usage_events.turn_id``, ``decision_ledger.turn_id``).
            # Callers that do not need it ignore the return; the turn close
            # uses it.
            return as_int(row['id'], 0)
    except Exception:
        logger.debug('record_turn_outcome failed', exc_info=True)
        return None


def recent_outcomes(session_id: str, limit: int = 60) -> list[dict[str, object]]:
    """This session's most recent outcome rows, newest first.

    Reader for the loop's stuck-request repair ladder (the old raw
    ``SELECT … WHERE session_id = ? ORDER BY id DESC LIMIT ?``).
    """
    if not session_id:
        return []
    rows = [
        row
        for row in _load_rows()
        if str(row.get('session_id') or '') == session_id
    ]
    return rows[-max(1, int(limit)) :][::-1]


def get_outcome(turn_id: int) -> dict[str, object] | None:
    """One outcome row by id (the 053 join-key lookup), or None."""
    try:
        wanted = int(turn_id)
    except (TypeError, ValueError):
        return None
    for row in _load_rows():
        try:
            if as_int(row.get('id'), 0) == wanted:
                return row
        except (TypeError, ValueError):
            continue
    return None


def guardrail_class_digest(session_id: str, sinceUtc: str) -> tuple[str | None, dict[str, str]]:
    """Roll up THIS turn's guardrail blocks out of ``tool-guardrail-log.jsonl``.

    The log stays the single authority for the per-block detail — this is a
    read, not a second writer, and it exists because the log has no
    model/provider field, so nothing could tie a repeated block back to the
    model that keeps causing it. Returns ``(digest, samplesByTool)``:

    * digest ``None``  → nothing measurable (no session id, unreadable log) and
      the caller must write None, not 0;
    * digest ``''``    → the turn was measured and blocked nothing;
    * digest ``',edit_file:2,'`` → per-tool counts, comma-padded so a match on
      ``',edit_file:'`` cannot hit ``'not_edit_file:'``.

    Tool names are sanitised on the way in (`,`` and ``:`` are structural) so
    the digest always round-trips through :func:`parse_guardrail_digest`.
    """
    if not session_id or not sinceUtc:
        return None, {}
    try:
        path = home(TELEMETRY_DIR) / 'tool-guardrail-log.jsonl'
        entries = [e for e in load_jsonl(path) if isinstance(e, dict)]
    except Exception:
        logger.debug('guardrail_class_digest failed', exc_info=True)
        return None, {}
    counts: dict[str, int] = {}
    samples: dict[str, str] = {}
    for entry in entries:
        if str(entry.get('session_id') or '') != session_id:
            continue
        created = str(entry.get('created_at') or '')
        if created and created < sinceUtc:
            continue
        tool = re.sub(r'[,:]', '_', str(entry.get('tool_name') or 'unknown')).strip() or 'unknown'
        counts[tool] = counts.get(tool, 0) + 1
        if tool not in samples:
            samples[tool] = ' '.join(str(entry.get('reason') or '').split())[:200]
    if not counts:
        # Measured, empty. Only honest when the read above actually ran.
        return '', {}
    return ',' + ','.join(f'{tool}:{n}' for tool, n in sorted(counts.items())) + ',', samples


def parse_guardrail_digest(digest: object) -> dict[str, int]:
    """Inverse of :func:`guardrail_class_digest` ('' / None → {})."""
    text = str(digest or '')
    out: dict[str, int] = {}
    for part in text.split(','):
        part = part.strip()
        if not part or ':' not in part:
            continue
        tool, _, raw = part.rpartition(':')
        try:
            out[tool] = int(raw)
        except ValueError:
            continue
    return out


def turn_verdict_stats(days: int = 7, task_type: str | None = None) -> dict[str, object]:
    """Why turns end, and how often they misbehave on the way there.

    The read side of 046 for the Learning panel: one windowed aggregate over
    the rows the retention keeps. Every counter reports ``total`` (None when the
    window holds no measured row), ``measured`` / ``unrecorded`` (row counts
    that split the null-vs-zero question out honestly) and ``turns`` (turns
    with at least one of that signal). Diagnostics only: nothing here gates an
    answer, and a reason distribution is not a score.

    Excludes the ``subagent_fanout`` population by default — see
    :func:`_population` — and reports it under ``byTaskType`` so the
    split is visible rather than merely asserted.
    """
    windowDays = max(1, min(int(days or 7), _RETENTION_DAYS))
    empty: dict[str, object] = {
        'days': windowDays,
        'turns': 0,
        'reasons': [],
        'reasonUnrecorded': 0,
        'counters': {},
        'byTaskType': [],
    }
    try:
        cutoff = _cutoff(windowDays)
        rows = [
            row
            for row in _load_rows()
            if _in_window(row, cutoff) and _population(row, task_type)
        ]
    except Exception:
        logger.debug('turn_verdict_stats failed', exc_info=True)
        return empty

    turns = len(rows)
    reason_counts: dict[str, int] = {}
    unrecordedReason = 0
    rounds_seen: list[int] = []
    rounds_missing = 0
    mf_totals = 0
    mf_missing = 0
    mf_turns = 0
    sd_totals = 0
    sd_missing = 0
    sd_turns = 0
    ev_totals = 0
    ev_missing = 0
    ev_turns = 0
    gr_turns = 0
    gr_missing = 0
    byTool: dict[str, int] = {}
    for row in rows:
        reason = row.get('end_reason')
        if reason is None:
            # Rows written before 046 (and turns whose verdict was never
            # handed down): counted and labelled, never folded into a reason.
            unrecordedReason += 1
        else:
            key = str(reason)
            reason_counts[key] = reason_counts.get(key, 0) + 1
        rounds = _nullable_int(row.get('rounds'))
        if rounds is None:
            rounds_missing += 1
        else:
            rounds_seen.append(rounds)
        mf = _nullable_int(row.get('malformed_tool_args'))
        if mf is None:
            mf_missing += 1
        else:
            mf_totals += mf
            if mf > 0:
                mf_turns += 1
        sd = _nullable_int(row.get('surface_downgraded'))
        if sd is None:
            sd_missing += 1
        else:
            sd_totals += sd
            if sd > 0:
                sd_turns += 1
        ev = _nullable_int(row.get('edit_verify_fails'))
        if ev is None:
            ev_missing += 1
        else:
            ev_totals += ev
            if ev > 0:
                ev_turns += 1
        digest = row.get('guardrail_classes')
        if digest is None:
            gr_missing += 1
        elif str(digest) != '':
            gr_turns += 1
            for tool, n in parse_guardrail_digest(digest).items():
                byTool[tool] = byTool.get(tool, 0) + n

    reasons: list[dict[str, object]] = [
        {'reason': name, 'turns': n}
        for name, n in sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    def _counter(total: int, missing: int, with_turns: int) -> dict[str, object]:
        return {
            # None = the window holds no measured row (NOT 0).
            'total': total if turns - missing else None,
            'measured': turns - missing,
            'turns': with_turns,
            'unrecorded': missing,
        }

    grMeasured = turns - gr_missing
    roundsMeasured = turns - rounds_missing
    counters: dict[str, object] = {
        'rounds': {
            'avg': round(sum(rounds_seen) / roundsMeasured, 2) if roundsMeasured else None,
            'max': max(rounds_seen) if rounds_seen else None,
            'measured': roundsMeasured,
            'unrecorded': rounds_missing,
        },
        'malformedToolArgs': _counter(mf_totals, mf_missing, mf_turns),
        'surfaceDowngrades': _counter(sd_totals, sd_missing, sd_turns),
        'editVerifyFails': _counter(ev_totals, ev_missing, ev_turns),
        'guardrailBlocks': {
            # 0 = measured and nothing was blocked; None = never measured.
            'total': sum(byTool.values()) if grMeasured else None,
            'measured': grMeasured,
            'turns': gr_turns,
            'unrecorded': gr_missing,
            'byTool': [
                {'tool': t, 'blocks': n}
                for t, n in sorted(byTool.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        },
    }
    return {
        'days': windowDays,
        'turns': turns,
        'reasons': reasons,
        'reasonUnrecorded': unrecordedReason,
        'counters': counters,
        'byTaskType': _taskTypeBreakdown(windowDays),
    }


def _taskTypeBreakdown(window_days: int) -> list[dict[str, object]]:
    """Row counts and round averages per ``task_type``, across ALL populations.

    Computed without the population predicate, because its whole job is to show
    what the predicate excluded. Without it, "we filter the fanout" would be an
    assertion nobody could check from the panel — the excluded rows would simply
    be gone.
    """
    try:
        cutoff = _cutoff(int(window_days))
        buckets: dict[str, dict[str, int]] = {}
        for row in _load_rows():
            if not _in_window(row, cutoff):
                continue
            key = str(row.get('task_type') or '')
            bucket = buckets.setdefault(key, {'turns': 0, 'rounds_total': 0, 'rounds_missing': 0})
            bucket['turns'] += 1
            rounds = _nullable_int(row.get('rounds'))
            if rounds is None:
                bucket['rounds_missing'] += 1
            else:
                bucket['rounds_total'] += rounds
    except Exception:
        logger.debug('task_type breakdown failed', exc_info=True)
        return []
    out: list[dict[str, object]] = []
    for key, bucket in buckets.items():
        measured = bucket['turns'] - bucket['rounds_missing']
        out.append(
            {
                'taskType': key,
                'turns': bucket['turns'],
                'avgRounds': round(bucket['rounds_total'] / measured, 2)
                if measured and bucket['rounds_total'] is not None
                else None,
            }
        )
    out.sort(key=lambda item: -as_int(item['turns'], 0))
    return out


def sweep_old_outcomes(days: int = _RETENTION_DAYS) -> int:
    """Drop rows older than the retention window (called by the M4 job)."""
    try:
        cutoff = _cutoff(int(days))
        with _LOCK:
            rows = _load_rows()
            kept = [row for row in rows if _in_window(row, cutoff)]
            removed = len(rows) - len(kept)
            if removed:
                rewrite_jsonl(_ledger_path(), cast('list[object]', kept))
                _invalidate_cache()
            return removed
    except Exception:
        logger.debug('sweep_old_outcomes failed', exc_info=True)
        return 0


def _decode_name_list(raw: object) -> list[str]:
    """The names inside a stored ``skills_injected``-style JSON list."""
    if not isinstance(raw, str) or not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(v) for v in parsed if str(v)]


def _ok_flag(row: dict[str, object]) -> int | None:
    value = row.get('ok')
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    number = as_int(value, -1)
    if number < 0:
        return None
    return 1 if number else 0


def skill_lift_detail(days: int = 30, limit: int = 0) -> list[dict[str, Any]]:
    """The ONE per-skill effect aggregation, with every number it rests on.

    Two consumers read this and they must not be able to disagree: ``GET
    /api/brain/skills/suggestions`` (the human read — the Skills UI) and the
    per-turn demotion prior in ``capabilities_prompt`` (the ranking read). The
    route used to run its OWN copy of this aggregation, so the UI could show
    ``+0.2`` for a skill the ranker treated as harmful — the exact failure the
    two-consumer rule exists to prevent. The route reads this function now, so
    there is one implementation behind both reads.

    A turn whose ``ok`` is null (stop reason never classified) is excluded from
    every denominator and numerator, exactly as ``COUNT(ok)`` / ``SUM(ok)``
    excluded it in the SQL this replaced.

    A skill with no measurable denominator is ABSENT from the result, not
    0.0: "no evidence" and "measured, no effect" are different facts, and
    only the second one may move a ranking.

    Ordered by turns-with DESC then name ASC, so the UI's "most measured
    first" list is a stable order. ``limit`` caps the list (0 = no cap); the
    demotion prior reads the whole map.
    """
    window_days = max(1, min(int(days or 30), _RETENTION_DAYS))
    try:
        cutoff = _cutoff(window_days)
        windowed = [
            row
            for row in _load_rows()
            if _in_window(row, cutoff) and row.get('skills_injected') is not None
        ]
    except Exception:
        logger.debug('skill_lift aggregate failed', exc_info=True)
        return []

    total_turns = sum(1 for row in windowed if _ok_flag(row) is not None)
    ok_total = sum(1 for row in windowed if _ok_flag(row) == 1)
    per: dict[str, dict[str, int]] = {}
    for row in windowed:
        ok = _ok_flag(row)
        for name in _decode_name_list(row.get('skills_injected')):
            bucket = per.setdefault(name, {'with_turns': 0, 'ok_with': 0})
            if ok is not None:
                bucket['with_turns'] += 1
                if ok:
                    bucket['ok_with'] += 1
    out: list[dict[str, Any]] = []
    for name, bucket in sorted(per.items(), key=lambda kv: (-kv[1]['with_turns'], kv[0])):
        with_turns = bucket['with_turns']
        without_turns = max(0, total_turns - with_turns)
        if not name or not with_turns or not without_turns:
            # No denominator on one side means the difference is undefined;
            # a 0.0 here would read as "measured, no effect".
            continue
        ok_with = bucket['ok_with']
        ok_rate_with = ok_with / with_turns
        ok_rate_without = (ok_total - ok_with) / without_turns
        out.append(
            {
                'skill': name,
                'turnsWith': with_turns,
                'okRateWith': round(ok_rate_with, 3),
                'turnsWithout': without_turns,
                'okRateWithout': round(ok_rate_without, 3),
                'lift': round(ok_rate_with - ok_rate_without, 3),
            }
        )
    cap = int(limit or 0)
    if cap > 0:
        out = out[:cap]
    return out


def skill_lift(days: int = 30) -> dict[str, float]:
    """Per-skill measured lift: ok-rate on turns that carried the skill minus
    ok-rate on turns that did not, over the window.

    ONE aggregation for two consumers: ``GET /api/brain/skills/suggestions``
    (the human read) and the per-turn demotion prior in
    ``capabilities_prompt`` (the ranking read). They must not be able to
    disagree — a skill the UI shows at ``+0.2`` and the ranker treats as
    harmful is a bug, not two opinions. The route reads
    :func:`skill_lift_detail`, the SAME function this map is built from.

    A skill with no measurable denominator is ABSENT from the map, not 0.0:
    "no evidence" and "measured, no effect" are different facts, and only the
    second one may move a ranking.
    """
    return {
        str(row.get('skill') or ''): float(row.get('lift') or 0.0)
        for row in skill_lift_detail(days)
        if str(row.get('skill') or '')
    }


def error_rate_by_model(days: int = 7) -> list[dict[str, object]]:
    """Per-model/provider turn stats for Observability + self-improve.

    Excludes the subagent-fanout population by the SAME predicate
    :func:`_population` gives the verdict stats — one filter, so the two
    readers cannot drift apart again.
    """
    try:
        cutoff = _cutoff(int(days))
        buckets: dict[tuple[str, str], dict[str, int]] = {}
        for row in _load_rows():
            if not _in_window(row, cutoff) or not _population(row, None):
                continue
            ok = _ok_flag(row)
            if ok is None:
                continue
            key = (str(row.get('model') or ''), str(row.get('provider') or ''))
            bucket = buckets.setdefault(key, {'turns': 0, 'errors': 0})
            bucket['turns'] += 1
            if ok == 0:
                bucket['errors'] += 1
    except Exception:
        logger.debug('error_rate_by_model failed', exc_info=True)
        return []
    out: list[dict[str, object]] = []
    for (model, provider), bucket in buckets.items():
        turns = bucket['turns']
        errors = bucket['errors']
        out.append(
            {
                'model': model,
                'provider': provider,
                'turns': turns,
                'errors': errors,
                'errorRate': round(errors / turns, 3) if turns else 0.0,
            }
        )
    out.sort(key=lambda r: (-as_int(r['errors'], 0), -as_int(r['turns'], 0)))
    # Roadmap #8: the plain ``errors DESC`` order put the ``('', '')`` bucket —
    # which only ever holds subagent-fanout rows, written with no model — at
    # the TOP of the per-model error ranking whenever any fanout ended badly.
    # A phantom model with the highest error rate on the Observability page.
    # Unnamed rows still rank by errors among themselves; they just cannot
    # outrank a named model, and the fanout population is excluded outright
    # by the same predicate the verdict stats use.
    out.sort(key=lambda r: (not str(r['model'] or ''), -as_int(r['errors'], 0)))
    return out


def _signature(provider: str, model: str, errorClass: str, kind: str = KIND_ERROR) -> str:
    """One stable slug per (provider, model, kind, label) — the fact key.

    The ``error`` space keeps its pre-046 shape byte-for-byte
    (``p1-m1-auth``): lessons and cooldown state written by older builds are
    keyed that way, and a rename would either orphan them or let the same
    failure be re-learned under a second key. Every other class is suffixed
    with its kind, so a repeated guardrail block on ``edit_file`` can never
    merge with an upstream error class of the same name.
    """
    raw = f'{provider or "?"}/{model or "?"}:{errorClass or "other"}'
    if kind and kind != KIND_ERROR:
        raw = f'{provider or "?"}/{model or "?"}:{kind}:{errorClass or "any"}'
    return re.sub(r'[^a-z0-9]+', '-', raw.lower()).strip('-')


def _failure_signature(failure: FailureClass, provider: str, model: str) -> str:
    return _signature(provider, model, failure.label, failure.kind)


def _recent_failures(failure: FailureClass, provider: str, model: str) -> list[str]:
    """Turns inside the promotion window that carry this failure signature.

    Each class is counted from its own field of the SAME ledger (no second
    store): the error class from the ``ok``/``error_class`` pair the feature
    has always used, the edit-verification gate from ``edit_verify_fails``,
    the guardrails from the ``guardrail_classes`` digest. Older rows without
    the 046 fields simply yield nothing for the two new classes.
    """
    try:
        cutoff = _cutoff(_PROMOTE_WINDOW_DAYS)
        rows = [
            row
            for row in _load_rows()
            if _in_window(row, cutoff)
            and str(row.get('provider') or '') == (provider or '')
            and str(row.get('model') or '') == (model or '')
        ]
        if failure.kind == KIND_EDIT_VERIFY:
            return [
                f'edit-verification gate failed {_nullable_int(row.get("edit_verify_fails")) or 0}x'
                for row in rows
                if (_nullable_int(row.get('edit_verify_fails')) or 0) > 0
            ]
        if failure.kind == KIND_GUARDRAIL:
            needle = failure.label or ''
            hits: list[str] = []
            for row in rows:
                digest = row.get('guardrail_classes')
                if not digest:
                    continue
                counts = parse_guardrail_digest(digest)
                if needle and needle not in counts:
                    continue
                hits.append(f"guardrail blocked {counts.get(needle, 0)}x ({needle or 'any tool'})")
            return hits
        return [
            str(row.get('error_class'))
            for row in rows
            if _ok_flag(row) == 0
            and str(row.get('error_class') or '') == (failure.label or '')
        ]
    except Exception:
        logger.debug('_recent_failures failed', exc_info=True)
        return []


def _lesson_text(provider: str, model: str, failure: FailureClass, count: int, sample: str) -> str:
    """The fact body for one class. Same shape, same cap, one per signature."""
    who = f'{provider or "unknown"}/{model or "unknown"}'
    head = {
        KIND_EDIT_VERIFY: (
            f'{who} failed its edit-verification gate {count} times in '
            f'{_PROMOTE_WINDOW_DAYS} days — edits the project test/lint gate '
            'kept rejecting.'
        ),
        KIND_GUARDRAIL: (
            f'{who} had {count} turns blocked by the tool guardrails'
            + (f' on `{failure.label}`' if failure.label else '')
            + f' in {_PROMOTE_WINDOW_DAYS} days.'
        ),
    }.get(
        failure.kind,
        (
            f'{who} failed {count} times in {_PROMOTE_WINDOW_DAYS} days '
            f'with error class "{failure.label or "other"}".'
        ),
    )
    lesson = f'{head.strip()} Sample: {sample}'
    if len(lesson) > _LESSON_CHAR_CAP:
        lesson = lesson[:_LESSON_CHAR_CAP].rstrip() + '…'
    return lesson


async def _review_lesson(
    lessonText: str, sampleError: str, modelHint: str, providerHint: str = ''
) -> bool:
    """Q2-a gate: one cheap-model review, discard-default.

    Any failure to reach a model (no provider, no key, empty answer) means
    the lesson is discarded — nothing reaches the store unreviewed.
    """
    try:
        from app.services.workbench.providers import make_review_llm_client

        # The provider rides with the model: the lesson was derived from THIS
        # provider's failures, and an unnamed provider would review through
        # whoever happens to be first-enabled.
        reviewLlm = make_review_llm_client(None, model=modelHint, provider=providerHint)
        if reviewLlm is None:
            return False
        prompt = [
            {
                'role': 'system',
                'content': (
                    'You gate candidate lessons before they enter long-term memory. '
                    'A lesson is stored only if it is: actionable (changes future behavior), '
                    'non-obvious, durable (still true beyond today), and not already common knowledge. '
                    'Reply with exactly YES or NO. When unsure, answer NO.'
                ),
            },
            {
                'role': 'user',
                'content': (
                    f'Candidate lesson derived from repeated turn failures:\n{lessonText}\n\n'
                    f'Recent error sample: {sampleError[:300]}\n\n'
                    'Is this a lesson worth storing permanently?'
                ),
            },
        ]
        answer = (await reviewLlm(prompt)).strip().upper()
        return answer.startswith('YES')
    except Exception:
        logger.debug('lesson review call failed (discard-default)', exc_info=True)
        return False


def _coerce_class(entry: FailureClass | Sequence[str]) -> FailureClass:
    """Accept a FailureClass or a plain ``(kind, label[, sample])`` tuple."""
    if isinstance(entry, FailureClass):
        return entry
    parts = [str(p or '') for p in (entry or ())]
    return FailureClass(
        kind=(parts[0] if parts else '') or KIND_ERROR,
        label=parts[1] if len(parts) > 1 else '',
        sample=parts[2] if len(parts) > 2 else '',
    )


# Classes whose signature needs a label to exist: an unnamed error class or an
# unnamed blocked tool is not a signature, it is missing data.
_LABELLED_KINDS = (KIND_ERROR, KIND_GUARDRAIL)


_SAMPLE_ERROR_CAP = 1200


def _redactedSampleError(text: str) -> str:
    """Strip this install's own credentials from an error before it is STORED.

    The outbound scrub (`scrubOutboundBody`, wired at the transport in
    `providers/clients/base.py`) protects what is SENT, and `secret_guard` protects
    tool args and protected-path reads. Neither covers a provider's own failure
    string: an auth rejection routinely echoes part of the credential back, and this
    function's output is persisted into the turn-outcomes ledger and then re-read by
    the lesson promotion, the telemetry surfaces and `scripts/harness_eval.py`. DS Bot
    treats exactly this case (`src/host/diagnostics.js`, `turns.js:9-11`) and drops
    the message on AUTH failures for the stated reason that providers may echo a key.

    Matched by VALUE, never by pattern — the same rule the outbound scrub ships
    with. A pattern pass would rewrite the key-shaped text a user pasted into their
    own question to ask why their key 401s, and that text is legitimate content.
    A credential this install does not hold (another tenant's, echoed back) is out of
    reach here, and saying so is better than pretending otherwise.
    """
    if not text:
        return text
    try:
        from app.services.hooks.secret_guard import scrubOutboundBody

        scrubbed = scrubOutboundBody(text)
        if isinstance(scrubbed, str):
            text = scrubbed
    except Exception:
        logger.debug('sample_error scrub failed', exc_info=True)
    return text[:_SAMPLE_ERROR_CAP]


def _candidate_classes(
    error_class: str, sample_error: str, extra: Sequence[FailureClass | Sequence[str]]
) -> list[FailureClass]:
    """Every failure class this turn can teach from, in its own signature space.

    A cancelled turn still teaches nothing (the user stopped it — not the
    model's failure), and duplicates collapse so one class is never judged
    twice in a call.
    """
    out: list[FailureClass] = []
    seen: set[tuple[str, str]] = set()
    candidates = [
        FailureClass(KIND_ERROR, error_class, _redactedSampleError(sample_error)),
        *(_coerce_class(e) for e in extra or ()),
    ]
    for failure in candidates:
        if failure.kind not in FAILURE_KINDS:
            continue
        if failure.kind in _LABELLED_KINDS and not failure.label:
            continue
        if failure.kind == KIND_ERROR and failure.label == 'cancelled':
            continue
        key = (failure.kind, failure.label)
        if key in seen:
            continue
        seen.add(key)
        out.append(failure)
    return out


async def maybe_promote_failure_lesson(
    model: str,
    provider: str,
    error_class: str,
    sample_error: str,
    review_model_hint: str = '',
    failure_classes: Sequence[FailureClass | Sequence[str]] = (),
) -> str:
    """Promote ONE lesson fact per signature that fails repeatedly (Q2 ruling).

    ``failure_classes`` widens the input beyond the upstream error class:
    edit-verification gate misses and guardrail blocks carry their own
    signature, so a model that keeps breaking the same gate learns the same
    way a model that keeps returning 429s does — same threshold, same review,
    same ``lesson`` fact kind, no new store. Each class is judged on its own
    and a noisy one cannot starve the quiet ones: a class that is below
    threshold still leaves the others to run, and every failure inside the
    loop is swallowed so one bad class cannot cancel the rest.

    Returns a status string for logging/lifecycle: ``promoted``,
    ``below-threshold``, ``cooldown``, ``review-rejected``, ``duplicate``,
    ``no-text`` or ``error``. With several classes the per-class statuses are
    joined (``guardrail:promoted;edit_verify:below-threshold``); a single
    class answers with the bare status, exactly as before. This is the only
    path from failure to memory.
    """
    candidates = _candidate_classes(error_class, sample_error, failure_classes)
    if not candidates:
        return 'below-threshold'
    statuses: list[str] = []
    for failure in candidates:
        statuses.append(await _promote_one(model, provider, failure, review_model_hint))
    if len(statuses) == 1:
        return statuses[0]
    return ';'.join(f'{f.kind}:{s}' for f, s in zip(candidates, statuses, strict=True))


async def _promote_one(
    model: str, provider: str, failure: FailureClass, review_model_hint: str
) -> str:
    """The promotion gate for ONE failure signature (see the caller)."""
    from app.services.memory_store import (
        derive_fact_title,
        get_internal_state,
        record_lifecycle,
        save_fact,
        set_internal_state,
    )

    sig = _failure_signature(failure, provider, model)
    try:
        failures = _recent_failures(failure, provider, model)
        if len(failures) < _PROMOTE_MIN_FAILURES:
            return 'below-threshold'
        cooldownKey = f'turn_outcomes:last_promotion:{sig}'
        lastRaw = str(get_internal_state(cooldownKey) or '')
        if lastRaw:
            try:
                lastAt = datetime.fromisoformat(lastRaw)
                if lastAt.tzinfo is None:
                    lastAt = lastAt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - lastAt < timedelta(days=_PROMOTE_COOLDOWN_DAYS):
                    return 'cooldown'
            except ValueError:
                pass
        sample = ' '.join((failure.sample or '').split())[:300]
        if not sample:
            return 'no-text'
        lesson = _lesson_text(provider, model, failure, len(failures), sample)
        # Q2-b: strict necessity filter — dedupe against existing facts.
        from app.services.memory_store.fact_retrieval import find_similar_facts

        similar = find_similar_facts(lesson, k=1)
        if similar and float(similar[0][0]) > _DEDUPE_SIMILARITY:
            set_internal_state(cooldownKey, datetime.now(timezone.utc).isoformat())
            record_lifecycle(
                '', 'lesson_promotion_skipped', {'signature': sig, 'reason': 'duplicate'}
            )
            return 'duplicate'
        if not await _review_lesson(lesson, sample, review_model_hint, providerHint=provider):
            set_internal_state(cooldownKey, datetime.now(timezone.utc).isoformat())
            record_lifecycle(
                '', 'lesson_promotion_skipped', {'signature': sig, 'reason': 'review-rejected'}
            )
            return 'review-rejected'
        factKey = f'harness-lesson:{sig}'
        save_fact(
            factKey,
            lesson,
            category='harness',
            source='harness',
            confidence=0.6,
            title=derive_fact_title(lesson),
            kind='lesson',
        )
        set_internal_state(cooldownKey, datetime.now(timezone.utc).isoformat())
        record_lifecycle('', 'lesson_promoted', {'signature': sig, 'factKey': factKey})
        logger.info('M5 promoted failure lesson %s', factKey)
        return 'promoted'
    except Exception:
        logger.debug('maybe_promote_failure_lesson failed', exc_info=True)
        return 'error'
