"""P5 — the outcome ledger: close the self-improvement loop with a number.

Every side-effecting learning write (an approved harness proposal, a KEPT
refine entry) records one ``harness_outcome`` row: what it expected, and the
episode statistics *before* it landed. A scheduled job later measures the same
shape *after* it landed and stores a verdict (improved / flat / regressed /
insufficient). This is SkillsBench's paired-evaluation protocol at proposal
scale — the harness's own evals — and it replaces the status quo where
``expectedOutcome`` was written and never checked.

The ledger is ``<dataDir>/telemetry/harness-outcomes.json`` — a JSON state
file keyed by the row's unique ``key`` (a re-approved proposal re-books
nothing, which is what INSERT OR IGNORE used to guarantee).

Episode-derived stats are the measurement surface because that is what the
harness actually changes for the user: how often sessions recover from
failures, how often the targeted failure fingerprint recurs. Rates use
resolved=1.0, rescued=0.5 weighting, matching the distiller's rubric.
Nothing here touches a live turn; all writes are best-effort and never raise
into the caller that records them (a lost measurement row must not fail an
otherwise-successful approval).

A ``regressed`` verdict also FILES a ``revert`` proposal carrying the
regressing change's own rollback text (audit P1#9) — the loop used to record
that a change hurt and then leave the undo to be rediscovered by hand. Filing
is where it stops: the proposal waits in the same human queue as every other.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.json_narrowing import as_dict, as_int, as_str
from app.services.best_effort import best_effort
from app.services.filestore import (  # noqa: F401 (TELEMETRY_DIR/home: outcomes file)
    TELEMETRY_DIR,
    home,
    read_json,
    write_json,
)

logger = logging.getLogger(__name__)

# Measurement window: how many days of episodes before/after the write.
_DEFAULT_WINDOW_DAYS = 14
# Below this many episodes on either side, the comparison says
# 'insufficient' rather than pretending to measure.
# Floor for BOTH sides of the comparison, and for the targeted rule too. Three
# was low enough to classify noise: a resolved rate over 3 episodes moves in
# 1/3 steps, so any single event crossed the old 0.05 hysteresis and could
# trigger an automated revert.
_MIN_EPISODES = 8
# Verdict hysteresis: a resolved-rate or recurrence-proportion move smaller than
# this is `flat`. 0.15 is roughly two events on the 8-episode floor, which is
# about as fine a distinction as this sample can honestly support.
_IMPROVE_EPS = 0.15

# SQLite-compatible format (space form), kept for the stored timestamps.
_TS = '%Y-%m-%d %H:%M:%S'

_OUTCOMES_FILE = 'harness-outcomes.json'
_LOCK = threading.RLock()


def _outcomes_path() -> Path:
    return home(TELEMETRY_DIR) / _OUTCOMES_FILE


def _load_outcomes() -> dict[str, dict[str, Any]]:
    raw = read_json(_outcomes_path(), default={})
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}


def _save_outcomes(rows: dict[str, dict[str, Any]]) -> None:
    write_json(_outcomes_path(), rows)


def _window_days() -> int:
    try:
        from app.services.brain_config_service import getRuntimeConfig

        raw = as_int(getRuntimeConfig().get('outcomeWindowDays', _DEFAULT_WINDOW_DAYS), _DEFAULT_WINDOW_DAYS)
    except Exception:
        raw = _DEFAULT_WINDOW_DAYS
    return max(3, min(90, raw))


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _episode_rows(since_s: str, until_s: str) -> list[dict[str, Any]]:
    """Episode rows inside a window, read from the episode domain's own file.

    The ``episodes`` table left SQLite with the rest of the store; its home is
    ``<state>/episodes.json`` (``memory_store.tables.episodes_file``). This
    module is a READER of that domain, not its owner. Rows keep their SQL-era
    column names (``outcome`` / ``created_at`` / ``fingerprint_id``). An
    unreadable store degrades to the 'insufficient' verdict — never a
    fabricated improvement or regression.
    """
    try:
        from app.services.memory_store import tables

        rows = tables.load_rows(tables.episodes_file())
    except Exception:
        return []
    return [
        row
        for row in rows
        if since_s <= str(row.get('created_at') or '') < until_s
    ]


def episode_stats(since: datetime, until: datetime, fingerprint: str = '') -> dict[str, Any]:
    """Episode outcome stats for a window; optionally the recurrence count of
    one failure fingerprint inside it.

    Recurrence is counted from the episode rows carrying the fingerprint — an
    episode-based count is the honest one (a fingerprint table row aggregates
    lifetime), so we count episodes, not fingerprint lifetimes.
    """
    since_s, until_s = since.strftime(_TS), until.strftime(_TS)
    rows = _episode_rows(since_s, until_s)
    counts: dict[str, int] = {}
    recurrence = 0
    for row in rows:
        outcome = str(row.get('outcome') or '')
        counts[outcome] = counts.get(outcome, 0) + 1
        if fingerprint and str(row.get('fingerprint_id') or row.get('fingerprint') or '') == fingerprint:
            recurrence += 1
    total = sum(counts.values())
    resolved = counts.get('resolved', 0) + 0.5 * counts.get('rescued', 0)
    out: dict[str, Any] = {
        'episodes': total,
        'resolvedRate': round(resolved / total, 4) if total else None,
        'window': [since_s, until_s],
    }
    if fingerprint:
        out['fingerprintRecurrence'] = recurrence
    return out


def record(
    source: str,
    key: str,
    kind: str,
    target: str,
    expected: str,
    fingerprint: str = '',
) -> None:
    """Book one side-effecting learning write with its BEFORE stats.

    Idempotent on ``key``: re-approving/replaying never double-books. Never
    raises — a failed outcome row must not fail the approval it is annotating.
    """
    try:
        days = _window_days()
        now = _utc(datetime.now(timezone.utc))
        before = episode_stats(now - timedelta(days=days), now, fingerprint)
        with _LOCK:
            rows = _load_outcomes()
            if key in rows:
                return
            rows[key] = {
                'id': len(rows) + 1,
                'key': key,
                'source': source,
                'kind': kind,
                'target': target,
                'fingerprint': (fingerprint or '')[:300],
                'applied_at': now.strftime(_TS),
                'expected': (expected or '')[:1000],
                'before_json': json.dumps(before, ensure_ascii=False),
                'measured_at': None,
                'window_d': None,
                'after_json': None,
                'verdict': None,
            }
            _save_outcomes(rows)
    except Exception:
        logger.debug('harness_outcome record failed (%s %s)', source, key, exc_info=True)


def record_proposal_outcome(row: dict[str, Any]) -> None:
    """Called from decide_proposal when a proposal actually applied."""
    payload = as_dict(row.get('payload'))
    kind = as_str(row.get('kind'), '')
    target = as_str(payload.get('name') or payload.get('target') or '')
    if kind == 'brain_config':
        target = ','.join(sorted(as_dict(payload.get('patch')).keys())) or 'config'
    fingerprint = as_str(payload.get('fingerprint'), '')
    record(
        source='proposal',
        key=f'proposal:{row.get("id")}',
        kind=kind,
        target=target[:200],
        expected=as_str(row.get('expectedMetric'), '') or as_str(row.get('proposal'), ''),
        fingerprint=fingerprint,
    )


def record_refine_outcome(applied_item: dict[str, Any], refine_id: str) -> None:
    """Called for each KEPT applied edit (the batch that survived review).

    Takes an entry from auto_refine's applied list — already flattened
    (op/id/kind/version/expectedOutcome). Deletes are still outcome-worthy:
    removing a wrong note is a change too.
    """
    record(
        source='refine',
        key=f"refine:{refine_id}:{applied_item.get('id')}:{applied_item.get('version')}",
        kind=as_str(applied_item.get('kind'), ''),
        target=f"{as_str(applied_item.get('op'), '')} {as_str(applied_item.get('id'), '')}",
        expected=as_str(applied_item.get('expectedOutcome'), ''),
    )


def measure_pending() -> dict[str, Any]:
    """Job body: classify due outcome rows whose after-window has elapsed.

    Only rows older than the full window are measured (the after-stats need a
    complete window), and only once (measured_at stamps the row). Returns a
    compact summary for the learning-job ledger.
    """
    days = _window_days()
    now = _utc(datetime.now(timezone.utc))
    cutoff = (now - timedelta(days=days)).strftime(_TS)
    verdicts = {'improved': 0, 'flat': 0, 'regressed': 0, 'insufficient': 0}
    measured = 0
    try:
        with _LOCK:
            rows = _load_outcomes()
            due = [
                row
                for row in rows.values()
                if row.get('measured_at') is None
                and str(row.get('applied_at') or '') <= cutoff
            ]
            due.sort(key=lambda row: str(row.get('applied_at') or ''))
            for row in due[:50]:
                applied = as_str(row.get('applied_at'), '')
                try:
                    applied_dt = _utc(datetime.strptime(applied, _TS))
                except ValueError:
                    verdicts['insufficient'] += 1
                    measured += 1
                    continue
                fingerprint = as_str(row.get('fingerprint'), '')
                try:
                    before = json.loads(as_str(row.get('before_json'), '{}'))
                except Exception:
                    before = {}
                after = episode_stats(applied_dt, applied_dt + timedelta(days=days), fingerprint)
                verdict = _classify(before, after, targeted=bool(fingerprint))
                row['measured_at'] = now.strftime(_TS)
                row['window_d'] = days
                row['after_json'] = json.dumps(after, ensure_ascii=False)
                row['verdict'] = verdict
                verdicts[verdict] += 1
                measured += 1
                if verdict == 'regressed':
                    with best_effort('learning.revert-proposal'):
                        _file_revert_proposal(row, before, after, days)
            _save_outcomes(rows)
    except Exception:
        logger.debug('harness_outcome measure_pending failed', exc_info=True)
        return {'measured': measured, **{f'v_{k}': v for k, v in verdicts.items()}}
    return {'measured': measured, **{f'v_{k}': v for k, v in verdicts.items()}}


def _source_change(key: str) -> tuple[str, str]:
    """(what the change was, how to undo it) for the row an outcome booked.

    Prefers the source's OWN rollback text over anything invented here — a
    revert proposal is only as good as the undo the author wrote for it.
    Returns empty strings when the source is no longer on file.
    """
    from app.services import harness_self_improve as hsi

    parts = key.split(':')
    if parts[0] == 'proposal' and len(parts) > 1:
        src = hsi.get_proposal(parts[1]) or {}
        rollback = as_str(src.get('rollback'), '').strip()
        if rollback:
            return as_str(src.get('proposal'), '').strip() or f'proposal {parts[1]}', rollback
    elif parts[0] == 'refine' and len(parts) > 2:
        # A refine entry is append-only and its own undo is by entry id, which
        # restores the previous version. There is no separate rollback string.
        return f'refine entry {parts[2]}', f'refine: undo entry {parts[2]} (restores its previous version)'
    return '', ''


def _file_revert_proposal(r: dict[str, Any], before: dict[str, Any], after: dict[str, Any], days: int) -> None:
    """Answer a measured regression: undo it if it is ours, else ask a human.

    A 'regressed' verdict is the ledger's one unambiguous negative signal: the
    change it booked measurably hurt. Undoing a change a HUMAN approved is not a
    call the machine may make on its own, so this only *files* — carrying the
    source's own rollback text and a payload link back to this outcome row. The
    one exception is probation: a change the rails applied themselves, still
    addressable to the snapshot they took, is put back through item 13's restore
    path and nothing is filed.

    Never raises (the caller's best_effort site is the backstop).
    """
    from app.services import harness_self_improve as hsi

    key = as_str(r.get('key'), '')
    source = as_str(r.get('source'), '')
    kind = as_str(r.get('kind'), '')
    target = as_str(r.get('target'), '') or key
    change, rollback = _source_change(key)
    if not rollback:
        # The source row is gone (pruned proposal file). Say so rather than
        # inventing an undo we cannot stand behind.
        rollback = (
            f'undo the {kind} change on {target} by hand — the original rollback text is '
            'no longer on file'
        )
    subject = change or f'the {kind} change on {target}'
    # Probation first. If this regression is a change the rails applied by
    # themselves and the version is still addressable, putting it back is the
    # whole answer — filing a "revert it?" proposal after the fact would ask the
    # human to approve something already done. Anything else (a human's apply,
    # autonomy off, a file someone edited since) returns None and the proposal
    # path below stands exactly as it was.
    from app.services.harness_rails import probation_revert

    if probation_revert(source, key, kind, target) is not None:
        return
    problem = f'Measured regression — revert {subject}?'
    evidence = json.dumps(
        {'verdict': 'regressed', 'windowDays': days, 'before': before, 'after': after},
        ensure_ascii=False,
    )
    proposal = (
        f'Revert the change booked as outcome {key} (source={source}, kind={kind}, '
        f'target={target}). The measurement job classified it regressed over a '
        f'{days}-day window. Undoing it is a human decision: this proposal only '
        'carries the evidence and the rollback the original author wrote.'
    )
    hsi.save_proposal(
        problem=problem,
        evidence=evidence,
        proposal=proposal,
        rollback=rollback,
        kind='revert',
        expected_metric='resolved rate recovers toward the pre-change window',
        payload={
            'outcomeId': r.get('id'),
            'outcomeKey': key,
            'source': source,
            'changeKind': kind,
            'target': target,
            'windowDays': days,
            'before': before,
            'after': after,
        },
    )


def _classify(before: dict[str, Any], after: dict[str, Any], targeted: bool = False) -> str:
    """Verdict for one measured change. Both rules read a RATE over a sample.

    `_MIN_EPISODES` floors both sides and `_IMPROVE_EPS` is the hysteresis: a
    move smaller than it is `flat`. The floor exists because a rate estimated
    from a handful of episodes swings on one event, and the cost of a false
    'regressed' here is not a wrong label — it is an automated revert.
    """
    bEps = as_int(before.get('episodes'), 0)
    aEps = as_int(after.get('episodes'), 0)
    if bEps < _MIN_EPISODES or aEps < _MIN_EPISODES:
        return 'insufficient'

    if targeted and 'fingerprintRecurrence' in before and 'fingerprintRecurrence' in after:
        # Recurrence is counted from the same episode rows as `episodes`, so it
        # is a proportion of the window, never a raw count: a busier window can
        # carry the same number of the targeted failure and be improving.
        bRec = as_int(before.get('fingerprintRecurrence'), 0) / bEps
        aRec = as_int(after.get('fingerprintRecurrence'), 0) / aEps
        if aRec <= bRec - _IMPROVE_EPS:
            return 'improved'
        if aRec >= bRec + _IMPROVE_EPS:
            return 'regressed'
        return 'flat'

    bRate = before.get('resolvedRate')
    aRate = after.get('resolvedRate')
    if bRate is None or aRate is None:
        return 'insufficient'
    if aRate >= bRate + _IMPROVE_EPS:
        return 'improved'
    if aRate <= bRate - _IMPROVE_EPS:
        return 'regressed'
    return 'flat'


def outcome_report(limit: int = 30) -> dict[str, Any]:
    """Rows for the Learning panel / curator endpoint (newest first)."""
    try:
        with _LOCK:
            rows = list(_load_outcomes().values())
        rows.sort(key=lambda row: int(row.get('id') or 0), reverse=True)
        out = []
        for row in rows[: max(1, int(limit))]:
            d = dict(row)
            d.pop('id', None)
            for jf in ('before_json', 'after_json'):
                try:
                    d[jf] = json.loads(str(d.get(jf) or '{}'))
                except Exception:
                    d[jf] = {}
            out.append(d)
        pending = sum(1 for row in rows if row.get('measured_at') is None)
        return {'rows': out, 'pending': pending}
    except Exception:
        return {'rows': [], 'pending': 0}
