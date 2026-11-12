"""Automation persistent memory + Part 19 Phase B routines.

Three file stores make automation jobs stateful (the migration-031 tables
became files under ``dataDir()``):

* ``<telemetry>/automation-runs.jsonl`` — one appended line per finished run
  attempt (the ledger; the job object's ``lastRun``/``lastOutput`` stay as
  cheap UI summary). Self-capped by rotation at 10 MB on every append.
* ``<state>/automation-notes.json`` — per-job KV notepad ("what I decided
  last time"), capped 4 KiB/key, 16 KiB/job. Machine state, deliberately
  NOT facts.
* ``<telemetry>/automation-incidents.json`` — deduped failures upserted by
  ``(job_id, error_signature)``; a succeeding run auto-closes the open row.

Part 19 Phase B rides on this: a routine (automation job with
``deliver='bot-chat'``) gets a wake-up context block (last run + notepad) and
its result is delivered into the Bot's canonical Bot Chat, optionally as a
responding turn (``respond`` flag, default on) — "cron output lands where
the bot actually responds".
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.json_narrowing import as_bool, as_int, as_str
from app.services.filestore import (
    STATE_DIR,
    TELEMETRY_DIR,
    append_jsonl,
    home,
    load_jsonl,
    read_json,
    rotate_jsonl,
    write_json,
)

logger = logging.getLogger(__name__)

# Notepad caps (service-enforced).
_NOTE_KEY_CAP = 4 * 1024
_NOTE_JOB_CAP = 16 * 1024
# Wake-up context caps (re-injected every run — must stay bounded).
_CONTEXT_LAST_RUN_CAP = 4 * 1024
_CONTEXT_CONTINUITY_CAP = 2 * 1024
# Closed incident rows kept this many days before the sweep deletes them.
_INCIDENT_RETENTION_DAYS = 90
# The runs ledger self-caps by bytes (filestore rule 3); the sweep still
# applies the time window on top.
_RUNS_LEDGER_MAX_BYTES = 10 * 1024 * 1024

TERMINAL_STATUSES = frozenset({'succeeded', 'failed', 'timeout', 'cancelled'})

_LOCK = threading.RLock()

_RUNS_FILE = 'automation-runs.jsonl'
_INCIDENTS_FILE = 'automation-incidents.json'
_NOTES_FILE = 'automation-notes.json'

# Runs started in THIS process but not yet finished, keyed by ledger id —
# the row is only appended when the run finishes, so the start context has
# to live somewhere until then. A crash between start and finish loses the
# attempt, which is the point of the append-per-finished-run rule.
_PENDING_RUNS: dict[int, dict[str, object]] = {}
_LAST_RUN_ID = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runs_path() -> Path:
    return home(TELEMETRY_DIR) / _RUNS_FILE


def _incidents_path() -> Path:
    return home(TELEMETRY_DIR) / _INCIDENTS_FILE


def _notes_path() -> Path:
    return home(STATE_DIR) / _NOTES_FILE


def _load_incidents() -> list[dict[str, object]]:
    raw = read_json(_incidents_path(), default=[])
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    return []


def _save_incidents(rows: list[dict[str, object]]) -> None:
    write_json(_incidents_path(), rows)


def _load_notes() -> dict[str, dict[str, dict[str, object]]]:
    raw = read_json(_notes_path(), default={})
    out: dict[str, dict[str, dict[str, object]]] = {}
    if isinstance(raw, dict):
        for job_id, notes in raw.items():
            if isinstance(job_id, str) and isinstance(notes, dict):
                clean = {k: v for k, v in notes.items() if isinstance(k, str) and isinstance(v, dict)}
                if clean:
                    out[job_id] = clean
    return out


# ── runs ledger ────────────────────────────────────────────────────────────


def _next_run_id() -> int:
    """Wall-clock ids: monotonic, sortable, unique within a process burst."""
    global _LAST_RUN_ID
    _LAST_RUN_ID = max(_LAST_RUN_ID + 1, int(time.time() * 1000))
    return _LAST_RUN_ID


def start_run(
    job_id: str,
    trigger: str = 'cron',
    agent_id: str = '',
    session_id: str = '',
) -> int:
    """Register a run attempt; returns its ledger id (0 on failure).

    The ledger line is appended by :func:`finish_run` — one line per
    FINISHED run — so the id only has to survive in this process long
    enough to be stamped.
    """
    try:
        with _LOCK:
            run_id = _next_run_id()
            _PENDING_RUNS[run_id] = {
                'job_id': job_id or '',
                'started_at': _now(),
                'trigger': trigger or 'cron',
                'agent_id': agent_id or '',
                'session_id': session_id or '',
            }
            return run_id
    except Exception:
        logger.debug('start_run failed', exc_info=True)
        return 0


def error_signature(text: str) -> str:
    """Normalize a failure into a dedupe fingerprint (exception class + head)."""
    raw = ' '.join((text or '').split())
    if not raw:
        return 'unknown'
    return raw[:120]


def finish_run(
    run_id: int,
    status: str,
    result_excerpt: str = '',
    error_sig: str = '',
    duration_ms: int = 0,
) -> None:
    """Append the run's finished ledger line and update the incident row.

    ``status`` must be terminal (``succeeded`` closes the job's open incident;
    ``failed``/``timeout`` bump it). Best-effort — never raises into a run.
    """
    if run_id <= 0 or status not in TERMINAL_STATUSES:
        return
    excerpt = (result_excerpt or '')[:4 * 1024]
    try:
        with _LOCK:
            start_info = _PENDING_RUNS.pop(run_id, None) or {}
            row: dict[str, object] = {
                'id': run_id,
                'job_id': str(start_info.get('job_id') or ''),
                'started_at': str(start_info.get('started_at') or ''),
                'finished_at': _now(),
                'status': status,
                'trigger': str(start_info.get('trigger') or 'cron'),
                'duration_ms': int(duration_ms or 0),
                'result_excerpt': excerpt,
                'error_signature': error_sig or '',
                'agent_id': str(start_info.get('agent_id') or ''),
                'session_id': str(start_info.get('session_id') or ''),
            }
            append_jsonl(_runs_path(), row)
            # Self-capping: rotation drops the OLDEST lines past the ceiling
            # on every append, so retention never depends on a sweep keeping
            # pace with ingest.
            rotate_jsonl(_runs_path(), _RUNS_LEDGER_MAX_BYTES)
        if row['job_id']:
            if status == 'succeeded':
                close_incident(str(row['job_id']))
            elif error_sig:
                record_incident(str(row['job_id']), error_sig)
    except Exception:
        logger.debug('finish_run failed', exc_info=True)


def _runs_rows() -> list[dict[str, object]]:
    return [row for row in load_jsonl(_runs_path()) if isinstance(row, dict)]


# ── notepad ─────────────────────────────────────────────────────────────────


def get_notes(job_id: str) -> dict[str, str]:
    with _LOCK:
        notes = _load_notes().get(job_id, {})
    return {
        str(k): str(v.get('value', ''))
        for k, v in sorted(
            notes.items(), key=lambda kv: str(kv[1].get('updated_at') or '')
        )
    }


def set_note(job_id: str, key: str, value: str) -> str:
    """Upsert one notepad entry. Returns '' on success, else an error reason."""
    if not key.strip():
        return 'empty key'
    value = value or ''
    if len(value.encode('utf-8', errors='replace')) > _NOTE_KEY_CAP:
        return f'value over {_NOTE_KEY_CAP} bytes'
    if not value:
        return delete_note(job_id, key)
    with _LOCK:
        notes = _load_notes()
        job_notes = notes.setdefault(job_id, {})
        others = sum(
            len(str(v.get('value', '')).encode('utf-8', errors='replace'))
            for k, v in job_notes.items()
            if k != key
        )
        if others + len(value.encode('utf-8', errors='replace')) > _NOTE_JOB_CAP:
            return f'job notepad over {_NOTE_JOB_CAP} bytes'
        job_notes[key.strip()] = {'value': value, 'updated_at': _now()}
        try:
            write_json(_notes_path(), notes)
            return ''
        except Exception:
            logger.debug('set_note failed', exc_info=True)
            return 'storage error'


def delete_note(job_id: str, key: str) -> str:
    with _LOCK:
        notes = _load_notes()
        job_notes = notes.get(job_id)
        if job_notes is not None and key in job_notes:
            del job_notes[key]
            if not job_notes:
                notes.pop(job_id, None)
            try:
                write_json(_notes_path(), notes)
            except Exception:
                logger.debug('delete_note failed', exc_info=True)
                return 'storage error'
        return ''


# ── incidents ───────────────────────────────────────────────────────────────


def record_incident(job_id: str, error_sig: str) -> bool:
    """Upsert the open incident for this signature (bump occurrences).

    Returns True when a NEW incident row was created (first detection),
    False on a bump or failure — callers use that to fire one-time notices.
    """
    sig = error_sig or 'unknown'
    try:
        with _LOCK:
            rows = _load_incidents()
            for row in rows:
                if (
                    str(row.get('job_id') or '') == job_id
                    and str(row.get('error_signature') or '') == sig
                    and str(row.get('state') or '') != 'closed'
                ):
                    row['occurrences'] = as_int(row.get('occurrences'), 0) + 1
                    row['last_seen_at'] = _now()
                    _save_incidents(rows)
                    return False
            next_id = 1 + max(
                (as_int(row.get('id'), 0) for row in rows), default=0
            )
            rows.append(
                {
                    'id': next_id,
                    'job_id': job_id,
                    'error_signature': sig,
                    'first_seen_at': _now(),
                    'last_seen_at': _now(),
                    'state': 'detected',
                    'occurrences': 1,
                }
            )
            _save_incidents(rows)
            return True
    except Exception:
        logger.debug('record_incident failed', exc_info=True)
        return False


def close_incident(job_id: str) -> None:
    """A succeeding run auto-closes the job's open incidents."""
    try:
        with _LOCK:
            rows = _load_incidents()
            changed = False
            for row in rows:
                if (
                    str(row.get('job_id') or '') == job_id
                    and str(row.get('state') or '') != 'closed'
                ):
                    row['state'] = 'closed'
                    row['last_seen_at'] = _now()
                    changed = True
            if changed:
                _save_incidents(rows)
    except Exception:
        logger.debug('close_incident failed', exc_info=True)


def record_blocked_step(
    *, job_id: str, tool: str, reason: str, session_id: str = ''
) -> None:
    """S-1 rider: an approval that could never be answered in an unattended run.

    Writes a deduped ``blocked-step`` incident (the M-11 ledger row for the
    denial — visible in the RoutinesPane incident badge like any failure) and,
    on FIRST detection, appends a passive notice to the owning Bot's canonical
    chat so the user sees why a routine stopped short. Never raises into the
    turn; interactive and job-less headless sessions record nothing.
    """
    if not job_id:
        return
    new = record_incident(job_id, f'blocked-step: {tool}')
    if not new:
        return
    try:
        from app.services import automations_store

        job = automations_store.get_job(job_id)
        if not job:
            return
        deliver_to_bot_chat(
            job,
            result_text=(
                f'Step blocked: {tool} — {reason}. No approver is available in an '
                'unattended run, so the routine continued without this step. '
                'Re-run it interactively (or approve the command in chat) if the '
                'step is essential.'
            ),
            trigger='blocked-step',
            respond=False,
        )
    except Exception:
        logger.debug('blocked-step notice failed', exc_info=True)


def open_incidents(job_id: str = '') -> list[dict[str, object]]:
    """Open incidents for one job (all jobs when ``job_id`` is empty)."""
    try:
        with _LOCK:
            rows = _load_incidents()
        open_rows = [
            row
            for row in rows
            if str(row.get('state') or '') != 'closed'
            and (not job_id or str(row.get('job_id') or '') == job_id)
        ]
        open_rows.sort(key=lambda row: str(row.get('last_seen_at') or ''), reverse=True)
        return open_rows
    except Exception:
        logger.debug('open_incidents failed', exc_info=True)
        return []


# ── wake-up context ─────────────────────────────────────────────────────────


def last_run(job_id: str) -> dict[str, object] | None:
    """The most recent ledger row for a job (any status)."""
    try:
        rows = [row for row in _runs_rows() if str(row.get('job_id') or '') == job_id]
        if not rows:
            return None
        return rows[-1]
    except Exception:
        logger.debug('last_run failed', exc_info=True)
        return None


def runs_for_job(job_id: str, limit: int = 10) -> list[dict[str, object]]:
    try:
        rows = [
            row
            for row in _runs_rows()
            if str(row.get('job_id') or '') == job_id
        ]
        rows.reverse()
        return rows[: max(1, int(limit))]
    except Exception:
        logger.debug('runs_for_job failed', exc_info=True)
        return []


def wake_context(job: dict[str, object]) -> str:
    """Volatile wake-up block prepended to a workbench automation prompt.

    Bounded: last-run status + result excerpt (4 KiB), the job's notepad
    (4 KiB total), and — only when the job sets ``continuity: true`` — the
    previous run's result tail (2 KiB) as "what you did last time". Empty
    string when there is no history (first run stays exactly the authored
    prompt).
    """
    job_id = as_str(job.get('id'))
    if not job_id:
        return ''
    last = last_run(job_id)
    notes = get_notes(job_id)
    if last is None and not notes:
        return ''

    parts: list[str] = ['<routine_context>', '(This block is refreshed every run — volatile context, not memory.)']
    if last is not None:
        status = as_str(last.get('status'))
        excerpt = as_str(last.get('result_excerpt'))[:_CONTEXT_LAST_RUN_CAP]
        finished = as_str(last.get('finished_at')) or as_str(last.get('started_at'))
        parts.append(f'Last run ({finished}): {status or "unknown"}.')
        if excerpt:
            parts.append(f'Last run result excerpt:\n{excerpt}')
        if as_bool(job.get('continuity')):
            tail = excerpt[-_CONTEXT_CONTINUITY_CAP:]
            if tail:
                parts.append(f'What you did last time (tail):\n{tail}')
    if notes:
        budget = _CONTEXT_LAST_RUN_CAP
        lines: list[str] = []
        for k, v in notes.items():
            entry = f'{k}: {v}'
            if len(entry) > budget:
                entry = entry[:budget]
            lines.append(entry)
            budget -= len(entry)
            if budget <= 0:
                break
        parts.append('Your notepad from previous runs:\n' + '\n'.join(lines))
    parts.append('</routine_context>')
    return '\n'.join(parts)


def sweep(days: int = 30, *, now: datetime | None = None) -> int:
    """Retention sweep — runs ledger at 30 d, closed incidents at 90 d.

    Rides the same maintenance window as the ``turn_outcomes`` sweep; one
    lifecycle row records what was removed.
    """
    now = now or datetime.now(timezone.utc)
    removed = 0
    try:
        with _LOCK:
            # Runs retention is `days` (30), not "everything before today" —
            # a plain now.date() cutoff would wipe the whole ledger on every
            # sweep and the wake-context would forget its history.
            cutoff = (now - timedelta(days=days)).date().isoformat()
            rows = _runs_rows()
            kept = [row for row in rows if str(row.get('started_at') or '') >= cutoff]
            removed += len(rows) - len(kept)
            if removed:
                from typing import cast

                from app.services.filestore import rewrite_jsonl

                rewrite_jsonl(_runs_path(), cast('list[object]', kept))
            incident_cutoff = now - timedelta(days=_INCIDENT_RETENTION_DAYS)
            incidents = _load_incidents()
            kept_incidents: list[dict[str, object]] = []
            for row in incidents:
                if str(row.get('state') or '') != 'closed':
                    kept_incidents.append(row)
                    continue
                last_seen = str(row.get('last_seen_at') or '')
                try:
                    seen_at = datetime.fromisoformat(last_seen)
                    if seen_at.tzinfo is None:
                        seen_at = seen_at.replace(tzinfo=timezone.utc)
                except ValueError:
                    kept_incidents.append(row)
                    continue
                if seen_at < incident_cutoff:
                    removed += 1
                else:
                    kept_incidents.append(row)
            if len(kept_incidents) != len(incidents):
                _save_incidents(kept_incidents)
    except Exception:
        logger.debug('automation_memory sweep failed', exc_info=True)
        return 0
    if removed:
        try:
            from app.services.memory_store import record_lifecycle

            record_lifecycle(
                '',
                'automation_memory_sweep',
                {'removedRows': removed, 'retentionDays': int(days)},
            )
        except Exception:
            logger.debug('sweep lifecycle row failed', exc_info=True)
    return removed


# ── Part 19 Phase B: routine delivery into the canonical Bot Chat ──────────


def deliver_to_bot_chat(
    job: dict[str, object],
    result_text: str,
    trigger: str = 'cron',
    respond: bool = True,
) -> str:
    """Append a routine's result into the Bot's canonical chat.

    ``respond=False`` (or no deliverable bot) appends the text as a plain
    user-role message — visible history, no turn. ``respond=True`` runs one
    workbench turn so the Bot reacts to its own routine output (the §1c
    delta: "cron output lands in a chat where the bot actually responds").

    Returns a status string for the run record; never raises into the caller.
    """
    from app.services.bot_mode import roster

    agent_id = as_str(job.get('agentId'))
    if not agent_id:
        return 'no-agent'
    try:
        chat = roster.find_canonical_bot_chat(agent_id)
        if chat is None:
            chat = roster.ensure_canonical_bot_chat(agent_id)
        session_id = as_str(getattr(chat, 'id', ''))
        if not session_id:
            return 'no-session'
        job_name = as_str(job.get('name')) or job_id_label(job)
        body = f'[routine:{job_name}] (trigger: {trigger}) result:\n' + (result_text or '')[:8000]
        from app.services.workbench import sessions as sessions_mod

        if not respond:
            # Passive delivery: append as visible user-role history, no turn.
            session = sessions_mod.get_workbench_session(session_id)
            if session is not None:
                session.messages.append(
                    {
                        'role': 'user',
                        'content': body,
                        # Delivered by the harness, not typed by the user — provenance
                        # is what keeps it out of episode mining's human-speech lane.
                        'source': 'memory_delivery',
                    }
                )
                session.messageCount += 1
                session.updatedAt = _now()
                sessions_mod.save_sessions()
            return 'delivered-passive'
        # respond=True: the turn loop appends the message itself
        # (workbench.py:2440) — appending here too would duplicate it in the
        # transcript, so the delivery IS the responding turn's user message.
        import asyncio

        async def _turn() -> None:
            try:
                from app.services.workbench import workbench as wb

                await wb.sendWorkbenchMessageStream(
                    sessionId=session_id,
                    message=body,
                    agentId=agent_id,
                )
            except Exception:
                logger.debug('routine respond turn failed', exc_info=True)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            loop.create_task(_turn())
        else:
            asyncio.run(_turn())
        return 'delivered-respond'
    except Exception:
        logger.debug('deliver_to_bot_chat failed', exc_info=True)
        return 'delivery-error'


def job_id_label(job: dict[str, object]) -> str:
    return as_str(job.get('id')) or 'job'
