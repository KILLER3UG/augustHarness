"""
Scheduler — manages recurring job execution using asyncio.

Port of backend/services/scheduler/index.js + missing/cron-tools.js.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from app.atomic_write import write_json_atomic
from app.json_narrowing import as_bool, as_str
from app.lib.paths import dataPath

# Cron parsing lives in automations_schedule (single implementation).
# These wrappers keep the legacy private names for existing callers.
from app.services.automations_schedule import _parse_cron_fields as _parseCron  # noqa: F401
from app.services.automations_schedule import matches_cron as _matchesCron  # noqa: F401

_JOBSFile = dataPath('scheduled-jobs.json')

logger = logging.getLogger(__name__)


def _jobsPath() -> Path:
    return _JOBSFile


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


_jobs: dict[str, dict[str, object]] = {}
_tasks: dict[str, asyncio.Task] = {}
_running = False


def _backup_corrupt(path: Path) -> None:
    """Preserve a corrupt jobs file instead of silently losing it."""
    try:
        backup = path.with_name(f'{path.name}.corrupt-{int(time.time())}')
        path.rename(backup)
        logger.warning(
            'scheduler: %s was corrupt — backed up to %s and starting with an empty job index',
            path.name,
            backup.name,
        )
    except OSError:
        logger.exception('scheduler: failed to back up corrupt jobs file %s', path)


def _loadJobs() -> None:
    p = _jobsPath()
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text('utf-8'))
        if isinstance(data, list):
            for j in data:
                if j.get('id'):
                    _jobs[j['id']] = j
    except (json.JSONDecodeError, OSError):
        # Automatic recovery: a corrupt task index must not crash startup NOR
        # silently wipe the jobs — preserve the file and start clean (audit
        # fix). Previously the corrupt file stayed in place and the jobs were
        # invisible (the failed read repeated every boot).
        _backup_corrupt(p)
    _collapseDuplicates()


def _jobFingerprint(job: dict[str, object]) -> tuple[str, str, str]:
    """What makes two legacy cron jobs the SAME job: name, schedule, command.

    Not the id — two records with the same id ARE one job (the id is the
    key). The fingerprint is the three fields that decide what the job
    DOES: a copy of a job that shares all three fires at the same moment,
    runs the same command, and produces the same run row.

    ``enabled`` is deliberately NOT part of the identity. Two jobs that
    differ only in enabled are still one job — one firing, one run row —
    and a fingerprint that separated them would leave both behind and keep
    the duplicate firing. The COLLAPSE resolves the difference instead (see
    :func:`_collapseDuplicates`).
    """
    return (
        str(job.get('name') or '').strip().lower(),
        str(job.get('schedule') or '').strip(),
        str(job.get('command') or '').strip(),
    )


def _collapseDuplicates() -> int:
    """Drop duplicate legacy cron jobs, keeping the OLDEST of each group.

    The live install carried 154 copies of one job ("camel-cron",
    ``*/5 * * * *``, ``true``) — the fingerprint of a test that posted the
    same job 154 times against the live store, the same leak class as the
    pytest rows migration 037 purged. Every copy fired every 5 minutes and
    every firing appended a run row, so one broken job wrote 44,352 rows a
    day: 96.1% of them the SAME error, because "true" is not a command on
    Windows and each failure stored ~296 bytes of stderr.

    The file is the owner's, so the collapse is CONSERVATIVE: a group is
    only collapsed when name, schedule AND command all match — two jobs
    that differ in any of those are different jobs and both survive.

    Within a group the OLDEST job is the survivor, because it is the first
    one the owner created — EXCEPT that an enabled job beats a disabled
    one. A create that asked for ``enabled=True`` must not be answered by
    returning a disabled twin (which is what an age-only rule did: the
    disabled copy won and the job silently never ran). Age breaks a tie
    between jobs that agree on enabled.

    Returns the number of jobs removed.
    """
    groups: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for job in _jobs.values():
        groups.setdefault(_jobFingerprint(job), []).append(job)
    removed = 0
    for fingerprint, jobs in groups.items():
        if len(jobs) < 2:
            continue
        # All three empty = a job with no name, no schedule and no command
        # cannot be judged a duplicate of anything; leave it alone.
        if not any(fingerprint):
            continue
        # Enabled first, then oldest (createdAt, then the id as the
        # tiebreak), so the survivor is deterministic across boots.
        jobs.sort(
            key=lambda j: (
                0 if as_bool(j.get('enabled'), True) else 1,
                str(j.get('createdAt') or ''),
                str(j.get('id') or ''),
            )
        )
        for extra in jobs[1:]:
            _jobs.pop(str(extra.get('id') or ''), None)
            removed += 1
    if removed:
        logger.info(
            'scheduler: collapsed %d duplicate legacy cron job(s) (same name, schedule and command)',
            removed,
        )
        try:
            _saveJobs()
        except Exception:
            logger.debug('scheduler: could not persist the collapsed job index', exc_info=True)
    return removed


def _saveJobs() -> None:
    p = _jobsPath()
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(p, list(_jobs.values()), indent=2, default=str)


def listJobs() -> list[dict[str, object]]:
    return list(_jobs.values())


def createJob(name: str, schedule: str, command: str, enabled: bool = True) -> dict[str, object]:
    """Create a scheduled job.

    An existing job with the SAME name, schedule and command is updated and
    returned instead of a second copy being created. Two such jobs fire at
    the same moment, run the same command, and write the same run row — the
    live install carried 154 copies of one job that way, and each copy
    wrote a run row every 5 minutes.

    The dedupe is keyed on what the job DOES, so the requested ``enabled``
    state is applied to the existing job rather than used to pick a
    different one. Returning a disabled twin unchanged (or creating a second
    copy) would both be wrong: the first makes an ``enabled=True`` request a
    silent no-op, and the second is the duplication this exists to stop.
    """
    import uuid

    _loadJobs()
    fingerprint = (str(name or '').strip().lower(), str(schedule or '').strip(), str(command or '').strip())
    if any(fingerprint):
        for existing in _jobs.values():
            if _jobFingerprint(existing) != fingerprint:
                continue
            if as_bool(existing.get('enabled'), True) != bool(enabled):
                existing['enabled'] = bool(enabled)
                _saveJobs()
            return existing
    jobId = f'sch_{uuid.uuid4().hex[:8]}'
    job = {
        'id': jobId,
        'name': name,
        'schedule': schedule,
        'command': command,
        'enabled': enabled,
        'status': 'idle',
        'lastRun': None,
        'nextRun': None,
        'createdAt': _now(),
    }
    _jobs[jobId] = job
    _saveJobs()
    return job


def deleteJob(jobId: str) -> bool:
    if jobId not in _jobs:
        return False
    if jobId in _tasks:
        _tasks[jobId].cancel()
        del _tasks[jobId]
    del _jobs[jobId]
    _saveJobs()
    return True


def updateJob(jobId: str, updates: dict[str, object]) -> dict[str, object] | None:
    if jobId not in _jobs:
        return None
    _jobs[jobId].update(updates)
    _saveJobs()
    return _jobs[jobId]


async def runJobNow(jobId: str) -> dict[str, object]:
    """Execute a job immediately.

    Runs through the standard sandbox (same policy as workbench run_command
    and automations_store shell jobs) — legacy cron jobs must not be able to
    bypass the sandbox.
    """
    job = _jobs.get(jobId)
    if not job:
        return {'error': 'Job not found'}
    job['status'] = 'running'
    # M-11: legacy cron jobs write the same run ledger automations use, so
    # the RoutinesPane/incidents view sees one history for both schedulers.
    run_id = 0
    try:
        from app.services.automation_memory import start_run

        run_id = start_run(job_id=jobId, trigger='cron-legacy')
    except Exception:
        run_id = 0
    try:
        from app.services.sandbox.policy import SandboxPolicy
        from app.services.sandbox.runner import run_sandboxed

        command = as_str(job.get('command'), '')
        cwd = as_str(job.get('cwd'), '') or ''
        policy = SandboxPolicy(
            mode='workspace-write',
            workspace_root=cwd,
            network=False,
        )
        result = await run_sandboxed(command, policy, timeout=300)
        job['lastRun'] = _now()
        job['status'] = 'idle' if result.exit_code == 0 else 'error'
        job['lastResult'] = result.as_tool_text()[:1000]
        if result.exit_code != 0:
            job['lastError'] = result.as_tool_text()[:500]
        _saveJobs()
        try:
            from app.services.automation_memory import error_signature, finish_run

            finish_run(
                run_id,
                status='succeeded' if result.exit_code == 0 else 'failed',
                result_excerpt=result.as_tool_text()[:1000],
                error_sig='' if result.exit_code == 0 else error_signature(result.as_tool_text()),
                duration_ms=int(result.elapsed_ms or 0),
            )
        except Exception:
            pass
        return job
    except Exception as exc:
        job['status'] = 'error'
        job['lastError'] = str(exc)
        try:
            from app.services.automation_memory import error_signature, finish_run

            finish_run(run_id, status='failed', error_sig=error_signature(str(exc)))
        except Exception:
            pass
        return job


def _make_done_callback(job_id: str) -> Callable[[asyncio.Task], None]:
    """Return a callback that removes *job_id* from ``_tasks`` when done."""

    def _cleanup(t: asyncio.Task) -> None:
        _tasks.pop(job_id, None)

    return _cleanup


async def startScheduler(intervalS: int = 60) -> None:
    """Start the scheduler loop.

    Fires legacy ``scheduled-jobs.json`` cron jobs and due ``automations.json``
    jobs (enabled, not paused) each tick.
    """
    global _running
    if _running:
        return
    _running = True
    _loadJobs()
    try:
        from app.services import automations_store as automations

        await automations.boot_automations()
    except Exception:
        pass
    while _running:
        now = datetime.now(timezone.utc)
        for jobId, job in list(_jobs.items()):
            if not job.get('enabled'):
                continue
            try:
                due = _matchesCron(as_str(job.get('schedule'), '* * * * *'), now)
            except ValueError:
                due = False  # corrupted legacy schedule must not break the tick
            if due:
                # Skip jobs still running from a previous tick — a slow job
                # (longer than the tick interval) must not fire concurrently.
                prev = _tasks.get(jobId)
                if prev is not None and not prev.done():
                    continue
                task = asyncio.create_task(runJobNow(jobId))
                _tasks[jobId] = task
                task.add_done_callback(_make_done_callback(jobId))
        try:
            from app.services import automations_store as automations

            await automations.tick_automations(now=now)
        except Exception:
            pass
        try:
            from app.services.harness_ops import fire_due_routines

            await fire_due_routines()
        except Exception:
            pass
        await asyncio.sleep(intervalS)


def stopScheduler() -> None:
    global _running
    _running = False
    for t in _tasks.values():
        t.cancel()
    _tasks.clear()


class Scheduler:
    """v2: In-process scheduler for cognitive-layer tasks.

    - register_periodic: run `fn` every `interval_seconds`
    - register_idle: run `fn` when no activity for `idle_threshold_seconds`
    - record_activity: reset the idle timer (call from workbench on each turn)
    """

    def __init__(self):
        self._periodic: list[tuple[str, Callable[[], Awaitable[None]], float]] = []
        self._idle: list[tuple[str, Callable[[], Awaitable[None]], float]] = []
        self._periodicTasks: list[asyncio.Task] = []
        self._idleTask: asyncio.Task | None = None
        self._stopped = False
        self._lastActivity: float = time.monotonic()
        self._last_activity: float = self._lastActivity
        self._idleResets: int = 0
        self._idle_resets: int = 0

    def registerPeriodic(
        self,
        name: str,
        fn: Callable[[], Awaitable[None]],
        intervalSeconds: float | None = None,
        interval_seconds: float | None = None,
    ) -> None:
        """Register a task to run every interval seconds."""
        interval = intervalSeconds if intervalSeconds is not None else interval_seconds
        if interval is None:
            raise TypeError('intervalSeconds is required')
        self._periodic.append((name, fn, float(interval)))

    def registerIdle(
        self,
        name: str,
        fn: Callable[[], Awaitable[None]],
        idleThresholdSeconds: float | None = None,
        idle_threshold_seconds: float | None = None,
    ) -> None:
        """Register a task to run when no activity for the idle threshold."""
        threshold = idleThresholdSeconds if idleThresholdSeconds is not None else idle_threshold_seconds
        if threshold is None:
            threshold = 300.0
        self._idle.append((name, fn, float(threshold)))

    # Snake_case aliases (tests + cognitive_boot)
    register_periodic = registerPeriodic
    register_idle = registerIdle
    registerInterval = registerPeriodic
    register_interval = registerPeriodic

    def recordActivity(self, sessionId: str = '') -> None:
        """Reset the idle timer. Called by workbench on each turn."""
        now = time.monotonic()
        self._lastActivity = now
        self._last_activity = now
        self._idleResets += 1
        self._idle_resets += 1

    record_activity = recordActivity

    async def start(self) -> None:
        """Boot the scheduler. Idempotent."""
        if self._periodicTasks or self._idleTask:
            return
        for name, fn, interval in self._periodic:
            t = asyncio.create_task(self._periodicLoop(name, fn, interval))
            self._periodicTasks.append(t)
        if self._idle:
            self._idleTask = asyncio.create_task(self._idleLoop())

    async def stop(self) -> None:
        """Stop all scheduled tasks."""
        self._stopped = True
        for t in self._periodicTasks:
            t.cancel()
        if self._idleTask:
            self._idleTask.cancel()
        awaitableTasks = [t for t in self._periodicTasks if t is not None]
        if self._idleTask is not None:
            awaitableTasks.append(self._idleTask)
        if awaitableTasks:
            await asyncio.gather(*awaitableTasks, return_exceptions=True)
        self._periodicTasks = []
        self._idleTask = None

    async def _periodicLoop(self, name: str, fn: Callable[[], Awaitable[None]], interval: float) -> None:
        while not self._stopped:
            try:
                await fn()
            except Exception:
                pass
            await asyncio.sleep(interval)

    async def _idleLoop(self) -> None:
        """Wake near the next idle deadline instead of polling every 100ms."""
        while not self._stopped:
            if not self._idle:
                await asyncio.sleep(1.0)
                continue
            now = time.monotonic()
            earliest_due: float | None = None
            fired = False
            for _name, fn, threshold in self._idle:
                due_at = self._lastActivity + threshold
                if now >= due_at:
                    try:
                        await fn()
                    except Exception:
                        pass
                    self._lastActivity = time.monotonic()
                    fired = True
                    break
                if earliest_due is None or due_at < earliest_due:
                    earliest_due = due_at
            if fired:
                continue
            sleep_for = 1.0 if earliest_due is None else max(0.05, earliest_due - time.monotonic())
            await asyncio.sleep(sleep_for)
