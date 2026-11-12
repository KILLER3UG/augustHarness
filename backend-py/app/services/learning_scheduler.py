"""P2 — the unified learning scheduler.

One registry + one loop for every clock-driven learning pass (the
pre-P2 design rode three independent pollers, each with its own
bookkeeping; in-turn ReviewGates stay in-turn by design — they react to
conversation, not the clock):

* One ledger — ``<dataDir>/telemetry/learning-job-run.jsonl`` — one JSON
  line per run with status, duration and a small summary blob, pruned to
  the recent tail per job (the old ``learning_job_run`` table, migration
  041).
* Due-ness is computed from the ledger (last finished run + interval),
  so state survives restarts and there is exactly one overdue path, the
  one ``consolidation_loop`` used to implement by hand.
* Cadences are re-read from brain-config every tick, so a settings change
  takes effect without a restart (same behavior the loops had individually).
* ``run_job(name)`` is the manual entry point (router / debugging) and
  writes the same ledger rows as the scheduler.

The job bodies are thin adapters over the existing passes — introspection
filing, promotion judging, and the whole consolidation chain (memory
lifecycle + skill learning + distiller + refine) — so this is a wiring
change, not a behavior change. The old loop functions remain importable as
shims for one release; nothing starts them anymore.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, cast

from app.json_narrowing import as_int
from app.services.filestore import (
    TELEMETRY_DIR,
    append_jsonl,
    home,
    load_jsonl,
    rewrite_jsonl,
)

logger = logging.getLogger(__name__)

# Ledger retention: keep the recent tail, prune the rest. The scheduler and
# the panel read at most the last few rows per job.
_LEDGER_KEEP_PER_JOB = 50

# Tick granularity. Small vs 6h/24h cadences; cheap vs a ledger read.
_TICK_S = 60

_LEDGER_FILE = 'learning-job-run.jsonl'
_LOCK = threading.RLock()
_LAST_ID = 0


def _ledger_path() -> Path:
    return home(TELEMETRY_DIR) / _LEDGER_FILE


def _rows() -> list[dict[str, Any]]:
    return [r for r in load_jsonl(_ledger_path()) if isinstance(r, dict)]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec='seconds')


# ── Ledger ────────────────────────────────────────────────────────────────


def _start_run(job: str) -> int:
    global _LAST_ID
    with _LOCK:
        rows = _rows()
        best = max((as_int(r.get('id'), 0) for r in rows), default=0)
        _LAST_ID = max(_LAST_ID + 1, best + 1)
        append_jsonl(
            _ledger_path(),
            {
                'id': _LAST_ID,
                'job': job,
                'started_at': _iso(_utcnow()),
                'finished_at': None,
                'status': 'running',
                'duration_s': None,
                'detail': None,
            },
        )
        return _LAST_ID


def _prune_locked(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cap the ledger at the recent tail per job (id is monotonic)."""
    per_job: dict[str, list[int]] = {}
    for row in rows:
        per_job.setdefault(str(row.get('job')), []).append(as_int(row.get('id'), 0))
    drop: set[int] = set()
    for ids in per_job.values():
        drop.update(ids[:-_LEDGER_KEEP_PER_JOB] if len(ids) > _LEDGER_KEEP_PER_JOB else [])
    if not drop:
        return rows
    return [row for row in rows if as_int(row.get('id'), 0) not in drop]


def _finish_run(run_id: int, status: str, detail: dict[str, Any]) -> None:
    with _LOCK:
        rows = _rows()
        target = None
        for row in rows:
            if as_int(row.get('id'), 0) == run_id:
                target = row
                break
        started = str(target.get('started_at')) if target else ''
        dur: float | None = None
        if started:
            try:
                dur = (_utcnow() - datetime.fromisoformat(started)).total_seconds()
            except ValueError:
                dur = None
        if target is not None:
            target['finished_at'] = _iso(_utcnow())
            target['status'] = status
            target['duration_s'] = dur
            target['detail'] = json.dumps(detail, default=str)[:2000]
        else:
            # A torn or pruned start row: the finish is still recorded.
            append_jsonl(
                _ledger_path(),
                {
                    'id': run_id,
                    'job': '',
                    'started_at': '',
                    'finished_at': _iso(_utcnow()),
                    'status': status,
                    'duration_s': dur,
                    'detail': json.dumps(detail, default=str)[:2000],
                },
            )
            return
        kept = _prune_locked(rows)
        rewrite_jsonl(_ledger_path(), cast('list[object]', kept))


def last_run(job: str) -> dict[str, Any] | None:
    """The most recent FINISHED ledger row for a job, or None."""
    with _LOCK:
        rows = _rows()
    for row in reversed(rows):
        if str(row.get('job')) == job and str(row.get('status')) != 'running':
            return row
    return None


def recent_runs(limit: int = 20) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _rows()
    rows.reverse()
    out = []
    for row in rows[: max(1, min(int(limit), 200))]:
        out.append(
            {
                'job': row.get('job'),
                'started_at': row.get('started_at'),
                'finished_at': row.get('finished_at'),
                'status': row.get('status'),
                'duration_s': row.get('duration_s'),
                'detail': row.get('detail'),
            }
        )
    return out


# ── Registry ──────────────────────────────────────────────────────────────


class Job:
    def __init__(
        self,
        name: str,
        fn: Callable[[], dict[str, Any]],
        interval_hours: Callable[[], float],
    ) -> None:
        self.name = name
        self.fn = fn
        self._interval_hours = interval_hours

    def interval(self) -> float:
        try:
            h = float(self._interval_hours())
        except Exception:
            h = 24.0
        return min(max(h, 1.0), 168.0)

    def due(self) -> bool:
        """First boot (no ledger row) is always due; otherwise overdue vs the
        last finished run. Running rows never make the job due twice."""
        row = last_run(self.name)
        if row is None:
            return True
        try:
            finished = datetime.fromisoformat(str(row.get('finished_at')))
        except (TypeError, ValueError):
            return True
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        return (_utcnow() - finished).total_seconds() >= self.interval() * 3600


JOBS: dict[str, Job] = {}


def _journal_job() -> dict[str, object]:
    """Resolve the decision ledger, then summarize the day.

    Deliberately NOT a model call. Resolution reads evidence that already
    exists — the turn's own verdict row — and the digest is arithmetic. A
    journal that needed a model to grade yesterday would be a second
    unmeasured judge, which is the failure mode the quarantined episode
    backlog already documents.

    Every row that cannot be settled by evidence is written ``unscorable``
    with a reason, and that count is reported next to the Brier score: a
    calibration number over three resolved rows out of forty means nothing
    without it.
    """
    from app.services import decision_ledger

    resolved = decision_ledger.resolve_open()
    summary = decision_ledger.digest()
    stats = decision_ledger.stats(days=7)
    by_model = stats.get('byModel')
    calibration = [
        {
            'model': row.get('name'),
            'brier': row.get('brier'),
            'resolved': row.get('resolved'),
            'unscorableRate': row.get('unscorableRate'),
        }
        for row in (by_model if isinstance(by_model, list) else [])
    ]
    return {
        'resolved': resolved.get('resolved', 0),
        'unscorable': resolved.get('unscorable', 0),
        'expired': resolved.get('expired', 0),
        'logged': summary.get('logged', 0),
        'costUsd': summary.get('costUsd'),
        'costPerResolved': summary.get('costPerResolved'),
        'calibration': calibration,
    }


def _register(job: Job) -> None:
    JOBS[job.name] = job


# ── Cadence config ────────────────────────────────────────────────────────


def _config_interval(key: str, default: float) -> Callable[[], float]:
    def _get() -> float:
        try:
            from app.services.brain_config_service import getRuntimeConfig

            raw = getRuntimeConfig().get(key, default)
            return float(raw if isinstance(raw, (int, float, str)) else default)
        except Exception:
            return default

    return _get


# ── Job bodies (adapters over the existing passes) ────────────────────────


def _introspection_job() -> dict[str, Any]:
    from app.services.harness_self_improve import (
        _run_scheduled_pass,
        _run_scheduled_promotion_pass,
    )

    filed = _run_scheduled_pass()
    promoted = _run_scheduled_promotion_pass()
    return {'observationsFiled': filed, 'promotionsFiled': promoted}


def _consolidation_job() -> dict[str, Any]:
    from app.services.memory_store.consolidation import run_consolidation

    summary = run_consolidation()
    # Keep the ledger row compact: the full summary can carry long blobs.
    return {k: v for k, v in summary.items() if not isinstance(v, (list, tuple))}


def _outcome_job() -> dict[str, Any]:
    """P5: measure due harness_outcome rows (pre/post episode stats)."""
    from app.services.harness_outcome import measure_pending

    return measure_pending()


def _refine_job() -> dict[str, Any]:
    """The gated refine pass as its OWN cadence.

    It used to ride inside _skill_learning_pass (consolidation, 24h): a
    refine batch could only land when memory consolidation ran, and its
    ledger row hid inside the consolidation blob. As a first-class job it
    gets its own due-ness, its own ledger row, and its own run-now button —
    and _skill_learning_pass no longer calls it (see consolidation.py).
    """
    from app.services.refine_store import get_refine_config, run_scheduled_refine

    if not get_refine_config()['autoRefine']:
        return {'status': 'disabled'}
    out = run_scheduled_refine()
    # Compact ledger row: keep status + counts, drop the raw model output
    # blob (the applied list lives in the refine journal, not here).
    detail = {k: v for k, v in out.items() if k != 'result'}
    result = out.get('result')
    if isinstance(result, dict) and isinstance(result.get('applied'), list):
        detail['applied'] = len(result['applied'])
    return detail


def _reviewer_job() -> dict[str, Any]:
    """The independent reviewer over open skill proposals.

    Advisory while autonomy is off: it records a verdict beside each proposal
    and decides nothing (see ``harness_self_improve.run_reviewer_pass``). A
    first-class job rather than a tail on introspection for the same reason
    ``_refine_job`` is one — its own due-ness, its own ledger row, its own
    run-now button. The pass is imported at call time, so patching the module
    attribute is the thing that runs.
    """
    from app.services.harness_self_improve import run_reviewer_pass

    return run_reviewer_pass()


def _review_job() -> dict[str, Any]:
    """Read ONE recent conversation and ask what it is worth keeping.

    The other five jobs are all triggered by something going wrong — a flagged
    episode, an observation, a failing automation. Nothing looked at a session
    that went *well* and extracted the preference or the multi-attempt procedure
    it contained, which is why the store stayed empty while hundreds of real
    turns passed. Opt-in and default-off: this spends a model call on a cadence,
    so an absent `memoryReview` means off. (The runaway backstop no longer
    follows that rule — a bound that costs nothing unless it trips is armed by
    default. Imported at call time so patching the module attribute is what runs.)
    """
    from app.services.skill_distiller import run_review_pass

    out = run_review_pass()
    if not isinstance(out, dict):
        return {'result': out}
    # Compact ledger row, exactly as _refine_job does. `results` carries one
    # entry per message read (up to 60), and _finish_run truncates the serialized
    # detail at 2000 chars — a cut JSON blob then fails to parse in
    # scheduler_status and the whole summary line vanishes, so the row would
    # report a successful review as an unexplained one.
    detail = {k: v for k, v in out.items() if k != 'results'}
    results = out.get('results')
    if isinstance(results, list):
        # The two labels that mean "something was kept": apply_verdict's full
        # return set is otherwise refusals, drops and duplicates.
        detail['kept'] = sum(
            1 for r in results if isinstance(r, dict) and r.get('label') in ('memory-saved', 'proposal-filed')
        )
        # 054: `kept` is one number; these are the REASONS. Persisting the door's
        # label is what makes "how often is a memory verdict dropped as a
        # duplicate?" answerable, and without that answer the distiller's
        # precision cannot be checked against anything.
        try:
            from app.services.skill_distiller import verdict_label_stats

            detail['labels'] = verdict_label_stats(days=30)['labels']
        except Exception:
            logger.debug('review job label stats failed', exc_info=True)
    return detail


_register(Job('introspection', _introspection_job, _config_interval('introspectionIntervalHours', 6.0)))
_register(Job('consolidation', _consolidation_job, _config_interval('consolidationIntervalHours', 24.0)))
_register(Job('refine', _refine_job, _config_interval('refineIntervalHours', 24.0)))
_register(Job('reviewer', _reviewer_job, _config_interval('reviewerIntervalHours', 6.0)))
_register(Job('outcome', _outcome_job, _config_interval('outcomeIntervalHours', 72.0)))
_register(Job('review', _review_job, _config_interval('reviewIntervalHours', 24.0)))
# 053: the decision journal. Not a learning pass — it is the only job that
# settles what the harness DECIDED, so it runs after the others and reads
# their verdicts rather than producing new ones.
_register(Job('journal', _journal_job, _config_interval('journalIntervalHours', 24.0)))


# ── Execution ─────────────────────────────────────────────────────────────


def run_job(name: str) -> dict[str, Any]:
    """One synchronous pass of a registered job with ledger bookkeeping.
    Never raises: failures land in the ledger as status='error'."""
    job = JOBS.get(name)
    if job is None:
        return {'ok': False, 'error': f'unknown job {name!r}'}
    run_id = _start_run(name)
    started = time.monotonic()
    try:
        detail = job.fn()
        status = 'ok'
        if isinstance(detail, dict) and detail.get('error'):
            status = 'error'
    except Exception as exc:  # defensive: adapters should not raise
        logger.warning('learning job %s failed', name, exc_info=True)
        detail = {'error': str(exc)[:300]}
        status = 'error'
    _finish_run(run_id, status, detail if isinstance(detail, dict) else {'result': detail})
    return {
        'ok': status == 'ok',
        'job': name,
        'runId': run_id,
        'status': status,
        'durationS': round(time.monotonic() - started, 3),
        'detail': detail,
    }


async def run_job_async(name: str) -> dict[str, Any]:
    """Event-loop-safe wrapper (job bodies are blocking, sync passes)."""
    return await asyncio.to_thread(run_job, name)


async def scheduler_loop() -> None:
    """The one scheduled learning poller. Each tick: every registered job
    that is due runs once, serially (learning passes are cheap individually
    and serialize safely against the shared stores)."""
    while True:
        try:
            for job in list(JOBS.values()):
                if job.due():
                    await run_job_async(job.name)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug('learning scheduler tick failed', exc_info=True)
        await asyncio.sleep(_TICK_S)


# ── Status for the UI ─────────────────────────────────────────────────────


def scheduler_status() -> dict[str, Any]:
    """Per-job cadence + last-run view, plus the shared recent ledger.
    Cheap: no model calls, a couple of ledger reads."""
    now = _utcnow()
    jobs: list[dict[str, Any]] = []
    for job in JOBS.values():
        row = last_run(job.name)
        next_due: str | None = None
        if row and row.get('finished_at'):
            try:
                finished = datetime.fromisoformat(str(row['finished_at']))
                if finished.tzinfo is None:
                    finished = finished.replace(tzinfo=timezone.utc)
                next_due = _iso(finished + timedelta(hours=job.interval()))
            except ValueError:
                pass
        elif row is None:
            next_due = _iso(now)  # never run — due now
        detail: dict[str, Any] = {}
        if row and row.get('detail'):
            try:
                detail = json.loads(str(row['detail']))
            except (json.JSONDecodeError, TypeError):
                detail = {}
        jobs.append(
            {
                'job': job.name,
                'intervalHours': job.interval(),
                'lastRunAt': row.get('finished_at') if row else None,
                'lastStatus': row.get('status') if row else 'never',
                'lastDurationS': row.get('duration_s') if row else None,
                'nextDueAt': next_due,
                'summary': detail,
            }
        )
    # The distiller is not a registered job — it piggybacks the consolidation
    # cadence — so its own readiness would otherwise be invisible here. An
    # unconfigured judge writes no ledger row and no error: the panel would
    # show consolidation running fine while the learning half sat idle.
    try:
        from app.services.skill_distiller import judgeStatus

        distiller = judgeStatus()
    except Exception:
        distiller = {}
    return {'jobs': jobs, 'runs': recent_runs(limit=10), 'distiller': distiller}
