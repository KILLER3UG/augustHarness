"""
Brain Orchestrator settings-tab HTTP API.

Mounts four routes under ``/api/brain``:

  GET  /api/brain/config                — { source, config, defaults, sessionId?, session? }
  PUT  /api/brain/config                — { ok, config, defaults }           (400 on bad patch)
  POST /api/brain/config/reset          — { ok, config, defaults }
  GET  /api/brain/config/from-session   — { source, config, defaults, sessionId, session }
  GET  /api/brain/stores                — per-store counts for the Memory settings page
  GET  /api/brain/stores/{name}         — paginated rows of one store (read-only browse)
  DELETE /api/brain/stores/{name}/{id}  — delete one row (per-entry Delete in the UI)
  PATCH  /api/brain/stores/{name}/{id}  — update whitelisted fields of one row
  GET  /api/brain/consolidation/log     — M4 consolidation + M5 lesson-promotion log
  POST /api/brain/consolidation/run     — trigger one consolidation pass now
  GET  /api/brain/turn-outcomes         — M5 telemetry: per-model error rates
                                          + the turn verdict distribution and
                                          self-correction counters (046)
  GET  /api/brain/state-lookup          — §5.5 raw internal_state/memory_store row by key
  GET  /api/brain/memory/preview         — the verbatim memory section + boot index a turn receives
  GET  /api/brain/backups               — offline brain-DB copies, each with its own health
  POST /api/brain/backups               — take a verified copy now
  POST /api/brain/backups/restore       — stage a verified restore (applies next launch)
  DELETE /api/brain/backups/restore     — cancel a staged restore
  GET  /api/brain/integrity             — PRAGMA integrity_check on the live DB

The shared service is :mod:`app.services.brain_config_service`. Mutation
endpoints record an audit row via ``memory_store.record_config_audit``.

Handler functions use camelCase to match the project-wide convention. URL
paths and JSON wire-format keys remain snake_case (FastAPI path params,
HTTP method semantics, and the ``auxiliary.cognitive.orchestrator`` SoT
must stay backward-compatible).

Port of the deleted Node.js ``backend/index.js`` brain-config block
(commit 6d61910, 2026-06-21).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query

from app.json_narrowing import as_int
from app.models.camel_base import CamelModel
from app.services import brain_config_service

router = APIRouter(prefix='/api/brain', tags=['brain-config'])


class BackupCreate(CamelModel):
    """Optional label recorded in the backup's filename."""

    reason: str = 'manual'


class BackupRestore(CamelModel):
    """The exact backup name to stage, as reported by ``GET /api/brain/backups``."""

    name: str


@router.get('/config')
async def getBrainConfig():
    """Return the effective brain config + defaults + source tag + session
    info. The React ``BrainSettings`` page calls this on mount."""
    return brain_config_service.getBrainConfigForSettings()


@router.put('/config')
async def putBrainConfig(body: dict[str, object]):
    """Apply a partial patch to ``auxiliary.cognitive.orchestrator``.

    Body must be a JSON object whose keys are a subset of the 11 known
    fields (9 booleans + 2 numeric limits). Unknown keys, wrong types, or
    out-of-range numbers → HTTP 400 with ``{code, message}``.
    """
    ok, err, merged = brain_config_service.saveBrainConfig(body or {})
    if not ok:
        raise HTTPException(
            status_code=400, detail={'code': 'EBRAIN_UNKNOWN_KEY' if 'unknown' in err else 'validation', 'message': err}
        )
    return {'ok': True, 'config': merged, 'defaults': brain_config_service.getDefaults()}


@router.post('/config/reset')
async def postBrainConfigReset():
    """Clear ``auxiliary.cognitive.orchestrator`` and return the factory defaults."""
    ok, defaults = brain_config_service.resetBrainConfig()
    return {'ok': ok, 'config': defaults, 'defaults': defaults}


@router.get('/config/from-session')
async def getBrainConfigFromSession(sessionId: str = Query(..., min_length=1)):
    """Return the brain config tagged ``source='session'`` for a specific
    workbench session. ``sessionId`` is required (400 if missing)."""
    if not sessionId:
        raise HTTPException(
            status_code=400, detail={'code': 'validation', 'message': 'sessionId query param is required'}
        )
    return brain_config_service.getBrainConfigFromSession(sessionId)


@router.get('/stores')
async def getBrainStores():
    """Per-store row counts — the Memory settings page header chips."""
    from app.services.memory_store.brain import brain_store_summary

    return {'stores': brain_store_summary()}


@router.get('/stores/{name}')
async def getBrainStore(
    name: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    query: str = Query('', max_length=200),
    sort: str = Query('', description='newest|oldest|updated|confidence (Part 17 C-4)'),
    category: str = Query('', max_length=64, description='Server-side category filter (C-3)'),
    source: str = Query('', max_length=64, description='Server-side source filter (C-3)'),
    confidence: str = Query('', max_length=16, description='Server-side confidence filter (C-3)'),
    scope: str = Query('', max_length=96, description="Server-side scope filter: 'global' or 'bot:<id>'"),
    status: str = Query('', max_length=24, description="Server-side status filter: 'active' or 'retired' (facts only)"),
):
    """Read-only paginated browse of one brain store (Memory settings page).

    Part 17 Phase C: ``sort``/``category``/``source``/``confidence`` move
    filtering and ordering server-side so they hold past the fetch cap.
    ``scope`` and ``status`` join them for the same reason — the library's
    scope picker and its retired count must be store facts, not facts about
    the page currently in memory.

    A filter whose column a store does not have is SKIPPED, not an error (the
    documented behaviour of every filter here), so a caller must only ask a
    store about a column it has: ``status`` and ``scope`` are facts columns.
    """
    from app.services.memory_store.brain import brain_browse

    result = brain_browse(
        name,
        limit=limit,
        offset=offset,
        query=query,
        sort=sort,
        category=category,
        source=source,
        confidence=confidence,
        scope=scope,
        status=status,
    )
    if result.get('error') and not result.get('total') and 'not available' in str(result.get('error')):
        raise HTTPException(status_code=404, detail=result['error'])
    return result


@router.get('/stores/facts/{row_id}/history')
async def getFactHistory(row_id: str):
    """Edit history for one fact, projected from the rollback store."""
    from app.services.memory_store.brain import fact_history

    result = fact_history(row_id)
    if not result.get('ok'):
        raise HTTPException(status_code=404, detail=str(result.get('error') or 'not found'))
    return result


@router.delete('/stores/{name}/{row_id:path}')
async def deleteBrainStoreRow(name: str, row_id: str):
    """Delete one row from a brain store (per-entry Delete in the Memory UI).

    ``row_id`` is the store's identifier column (``id`` for most stores, the
    ``key`` for the KV memory store). Read-only/legacy stores return 403.
    """
    from app.services.memory_store.brain import brain_delete_row

    result = brain_delete_row(name, row_id)
    if result.get('status') == 403:
        raise HTTPException(status_code=403, detail=result.get('error'))
    if not result.get('ok'):
        err = str(result.get('error') or 'delete failed')
        raise HTTPException(status_code=404 if 'not found' in err else 400, detail=err)
    return result


@router.patch('/stores/{name}/{row_id:path}')
async def updateBrainStoreRow(name: str, row_id: str, body: dict[str, object]):
    """Update whitelisted fields of one brain-store row (Memory UI inline edit).

    Only the store's whitelisted columns are applied; unknown fields are
    ignored. Read-only/legacy stores return 403.
    """
    from app.services.memory_store.brain import brain_update_row

    result = brain_update_row(name, row_id, body or {})
    if result.get('status') == 403:
        raise HTTPException(status_code=403, detail=result.get('error'))
    if not result.get('ok'):
        err = str(result.get('error') or 'update failed')
        raise HTTPException(status_code=404 if 'not found' in err else 400, detail=err)
    return result


@router.get('/consolidation/log')
async def getConsolidationLog(limit: int = Query(50, ge=1, le=200)):
    """M4 consolidation log: one lifecycle row per pass plus the
    M5 lesson-promotion decisions. This is the analysis surface that replaced
    the deleted diagnostics endpoints."""
    import json as _json

    from app.services.memory_store import tables

    rows = [
        r for r in tables.ledger_rows(tables.lifecycle_file())
        if str(r.get('event_type') or r.get('eventType') or '')
        in ('consolidation', 'lesson_promoted', 'lesson_promotion_skipped')
    ]
    rows.sort(
        key=lambda r: (str(r.get('created_at') or r.get('createdAt') or ''), as_int(r.get('id'), 0)),
        reverse=True,
    )
    rows = rows[:limit]
    entries: list[dict[str, object]] = []
    for r in rows:
        raw_detail = r.get('detail')
        detail: object = {}
        try:
            detail = _json.loads(str(raw_detail)) if raw_detail else {}
        except (ValueError, TypeError):
            detail = {'raw': raw_detail}
        entries.append(
            {
                'createdAt': r.get('created_at') or r.get('createdAt'),
                'eventType': r.get('event_type') or r.get('eventType'),
                'detail': detail,
            }
        )
    return {'entries': entries}


@router.post('/consolidation/run')
async def postConsolidationRun():
    """Trigger one consolidation pass now (Settings → Memory → "Run now")."""
    import asyncio

    from app.services.memory_store.consolidation import run_consolidation

    summary = await asyncio.to_thread(run_consolidation)
    return {'ok': not summary.get('error'), 'summary': summary}


@router.get('/turn-outcomes')
async def getTurnOutcomes(days: int = Query(7, ge=1, le=30)):
    """M5 telemetry: per-model/provider error rates plus the per-turn verdict
    distribution (046) for the Observability hub and the Learning panel.
    Diagnostics only — never injected into prompts and never shown in the
    Memory UI. A reason like ``cap`` or ``stall-stop`` names a turn that
    ALREADY finished and whose answer reached the user; this endpoint reports
    why the loop stopped, it never withholds anything."""
    from app.services.turn_outcomes import error_rate_by_model, turn_verdict_stats

    return {
        'days': days,
        'models': error_rate_by_model(days=days),
        # end_reason distribution + rounds + the self-correction counters,
        # each split into measured / unrecorded so NULL is never read as 0.
        'verdicts': turn_verdict_stats(days=days),
    }


@router.get('/decisions')
async def getDecisions(
    days: int = Query(30, ge=1, le=365),
    status: str = Query('', max_length=20),
    limit: int = Query(50, ge=1, le=500),
):
    """053: the decision ledger — what the harness decided and how it settled.

    Read-only diagnostics, like ``/turn-outcomes``: nothing here is injected
    into a prompt and nothing gates an answer. Three sections:

      * ``digest`` — today's activity (logged / resolved / unscorable /
        expired), cost, and cost per resolved decision.
      * ``stats`` — per-model and per-kind resolution, with a Brier component.
        ``unscorableRate`` sits beside ``brier`` on purpose: a good score over
        three resolved rows out of forty is luck, not calibration.
      * ``rows`` — the newest ledger rows, for the Learning panel.
    """
    from app.services import decision_ledger

    return {
        'days': days,
        'digest': decision_ledger.digest(),
        'stats': decision_ledger.stats(days=days),
        'rows': decision_ledger.list_decisions(status=status, limit=limit),
    }


@router.post('/decisions/resolve')
async def postDecisionsResolve():
    """Run one journal resolution pass now (the Learning page → "Run now")."""
    import asyncio

    from app.services.decision_ledger import resolve_open

    return {'ok': True, 'result': await asyncio.to_thread(resolve_open)}


@router.get('/memory/metrics')
async def getMemoryMetrics(days: int = Query(7, ge=1, le=30)):
    """Phase D item 4: recall + latency instrument.

    Three sections, all read-only diagnostics:
      * ``recall`` — accumulated counts from internal_state (global facts
        recalled, project entries recalled): the before/after instrument for
        this plan and every later retrieval change.
      * ``lastTurn`` — the PER-TURN recall blob (facts recalled, memory block
        size) for the last turn that ran. It was written on every turn and
        read by NOTHING: this route read the accumulated totals instead, so
        the per-turn number the docstring promised was never returned. It is
        the number a user actually wants -- "how much did the last turn
        pull in?" -- so it is read here.
      * ``latency`` — the Phase L section: TTFT / duration / prompt-cache
        hit split aggregated over ``days`` from turn_outcomes.

    ``consolidationLastRun`` is the timestamp of the last memory
    consolidation pass. It was written by the pass and read by nothing.
    """
    import json as _json

    from app.services.memory_store import tables
    from app.services.memory_store.kv import get_internal_state

    def _readTotals() -> dict[str, int]:
        try:
            raw = get_internal_state('memory:recall:totals')
            val = _json.loads(str(raw)) if isinstance(raw, str) else raw
            if isinstance(val, dict):
                return {str(k): int(str(v)) for k, v in val.items()}
            return {}
        except Exception:
            return {}

    totals = _readTotals()
    recall = {
        'turns': int(totals.get('turns') or 0),
        'globalFactsRecalled': int(totals.get('globalFactsRecalled') or 0),
        'projectEntriesRecalled': int(totals.get('projectEntriesRecalled') or 0),
    }

    def _readJsonState(key: str) -> object | None:
        try:
            raw = get_internal_state(key)
            val = _json.loads(str(raw)) if isinstance(raw, str) else raw
            return val if isinstance(val, dict) else None
        except Exception:
            return None

    # Written every turn by the workbench, read by nothing until now.
    last_turn = _readJsonState('memory:recall:last_turn')
    # Written by the consolidation pass, read by nothing until now.
    consolidation_last_run: str | None = None
    try:
        raw = get_internal_state('consolidation:last_run')
        consolidation_last_run = str(raw) if raw else None
    except Exception:
        consolidation_last_run = None
    latency: dict[str, object] = {'turns': 0}
    try:
        # The turn-outcomes ledger (the transcript/telemetry domain's runtime
        # file); the aggregate walks the window's rows in one pass.
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).strftime(
            '%Y-%m-%d %H:%M:%S'
        )

        def _in_window(row: dict[str, object]) -> bool:
            ts = str(row.get('ts') or row.get('createdAt') or row.get('created_at') or '')
            # The ledger mixes the space-separated stamp the SQL writer used
            # and ISO 'T' stamps; the space form sorts below the 'T' cutoff on
            # the same day, so both are normalized to a comparable prefix.
            return ts.replace('T', ' ')[:19] >= cutoff

        turns_n = 0
        ttft_sum = ttft_max = 0
        duration_sum = 0
        hitT = missT = 0
        for row in tables.ledger_rows(tables.turn_outcomes_file()):
            if not _in_window(row):
                continue
            turns_n += 1
            try:
                ttft = int(row.get('ttft_ms') or row.get('ttftMs') or 0)
            except (TypeError, ValueError):
                ttft = 0
            ttft_sum += ttft
            ttft_max = max(ttft_max, ttft)
            try:
                duration_sum += int(row.get('duration_ms') or row.get('durationMs') or 0)
            except (TypeError, ValueError):
                pass
            hitT += int(row.get('cache_hit_tokens') or row.get('cacheHitTokens') or 0)
            missT += int(row.get('cache_miss_tokens') or row.get('cacheMissTokens') or 0)
        if turns_n:
            latency = {
                'turns': turns_n,
                'avgTtftMs': round(ttft_sum / turns_n, 1),
                'maxTtftMs': ttft_max,
                'avgDurationMs': round(duration_sum / turns_n, 1),
                'cacheHitTokens': hitT,
                'cacheMissTokens': missT,
                'cacheHitRate': round(hitT / (hitT + missT), 3) if (hitT + missT) else 0.0,
            }
    except Exception:
        latency = {'turns': 0, 'error': 'turn_outcomes unavailable'}
    return {
        'days': days,
        'recall': recall,
        'lastTurn': last_turn,
        'latency': latency,
        'consolidationLastRun': consolidation_last_run,
    }


@router.get('/memory/preview')
async def getMemoryPreview(
    query: str = Query('', max_length=2000, description='Message to recall against'),
    workspace: str = Query('', max_length=500, description='Project workspace to include'),
):
    """The literal durable-memory text a turn would receive for one query.

    Not a listing of stored rows — the rendered memory section itself, produced
    by the same builders the workbench calls. Two honest limits: the per-session
    `new in memory since this chat opened` lane is NOT here, because a preview
    has no session and therefore no writes since opening; and no Settings panel
    calls this door today (the route exists for the API and for a future
    preview surface), so "what the user reads here is what the model reads"
    holds only for whoever reads it through the API.
    :func:`app.services.memory_store.fact_retrieval.memory_context_preview`.
    """
    from app.services.memory_store.fact_retrieval import memory_context_preview

    return memory_context_preview(query, workspace)


@router.get('/backups')
async def list_brain_backups():
    """Every offline copy of the brain DB, each labelled with its own health."""
    from app.services import brain_backup

    return {
        'backups': brain_backup.list_backups(),
        'keep': brain_backup.KEEP_BACKUPS,
        'pendingRestore': brain_backup.pending_restore(),
        'directory': str(brain_backup.backups_dir()),
    }


@router.post('/backups')
async def create_brain_backup(body: BackupCreate | None = None):
    """Verify-and-copy the brain database without a write lock on the original."""
    from app.services import brain_backup

    result = brain_backup.create_backup(reason=(body.reason if body else None) or 'manual')
    if not result.get('ok'):
        raise HTTPException(500, detail=str(result.get('error') or 'backup failed'))
    return result


@router.post('/backups/restore')
async def restore_brain_backup(body: BackupRestore):
    """Stage a verified restore for the next launch.

    Not immediate on purpose: the running app holds the database open on more
    than one thread, so swapping the file underneath it yields a half-old
    database. The caller restarts August (or lets the updater do it) to apply.
    """
    from app.services import brain_backup

    result = brain_backup.schedule_restore(body.name)
    if not result.get('ok'):
        raise HTTPException(400, detail=str(result.get('error') or 'restore refused'))
    return result


@router.delete('/backups/restore')
async def cancel_brain_restore():
    from app.services import brain_backup

    return brain_backup.cancel_restore()


@router.get('/integrity')
async def brain_integrity():
    """``PRAGMA integrity_check`` on the live DB plus what could be recovered."""
    from app.services import brain_backup

    return brain_backup.quick_check()


@router.get('/state-lookup')
async def getStateLookup(key: str = Query(..., min_length=1)):
    """Raw state lookup: type a key, get the raw
    ``internal_state`` or ``memory_store`` row verbatim. This is the only
    surface where ``cognitive:*``-style machine state is ever visible —
    never quarantined into Memory, never rendered by default. Read-only;
    ``internal_state`` is checked first (machine state wins)."""
    import json as _json

    from app.services.memory_store.kv import lookup_any

    k = key.strip()
    if not k:
        raise HTTPException(
            status_code=400, detail={'code': 'validation', 'message': 'key query param is required'}
        )
    row = lookup_any(k)
    if row is not None:
        value: object
        try:
            value = _json.loads(str(row.get('value')))
        except (ValueError, TypeError):
            value = row.get('value')
        return {
            'key': k,
            'found': True,
            'source': row.get('source'),
            'value': value,
            'updatedAt': row.get('updated_at'),
        }
    return {'key': k, 'found': False, 'source': None, 'value': None, 'updatedAt': None}


# ── Routing evidence: Arena / Debate verdicts ──────────────
# The Arena + Debate UIs are live and POST a winner/losers verdict to
# /api/brain/routing/arena and read history back; the endpoints never existed,
# so every recorded verdict toasted "Could not record verdict". These thin
# routes persist verdicts into the existing routing_evidence table (source
# 'arena') and serve the archive + a win-rate suggestion list.


class _ArenaModel(CamelModel):
    model_id: str = ''
    provider: str = ''


class _ArenaVerdict(CamelModel):
    session_id: str = ''
    prompt: str = ''
    task_type: str = 'arena'
    winner: _ArenaModel | None = None
    losers: list[_ArenaModel] = []


@router.post('/routing/arena')
async def recordArenaVerdict(body: _ArenaVerdict):
    """Record one arena/debate verdict: a winner row (ok=1) + one loser row
    (ok=0) per competing model, all tagged source='arena'."""
    from app.services.memory_store import tables
    from app.services.memory_store.timeutil import utc_now_text

    winner = body.winner
    if winner is None or not winner.model_id:
        raise HTTPException(status_code=400, detail='winner.modelId is required')
    path = tables.routing_evidence_file()
    recorded = 0
    with tables.store_lock(str(path)):
        rows = tables.ledger_rows(path)
        next_id = tables.next_id(rows)

        def _row(model: str, provider: str, ok: int) -> dict[str, object]:
            nonlocal next_id
            next_id += 1
            return {
                'id': next_id - 1,
                'session_id': body.session_id,
                'task_type': body.task_type or 'arena',
                'model': model,
                'provider': provider,
                'ok': ok,
                'source': 'arena',
                'prompt': body.prompt,
                'input_tokens': 0,
                'output_tokens': 0,
                'duration_ms': 0,
                'created_at': utc_now_text(),
            }

        rows.append(_row(winner.model_id, winner.provider, 1))
        recorded += 1
        for loser in body.losers:
            if not loser.model_id:
                continue
            rows.append(_row(loser.model_id, loser.provider, 0))
            recorded += 1
        tables.rewrite_ledger(path, rows)
    return {'ok': True, 'recorded': recorded}


@router.get('/routing/arena')
async def getArenaHistory(limit: int = Query(100, ge=1, le=500)):
    """Recent arena verdicts (the durable archive the UI groups per session)."""
    from app.services.memory_store import tables

    rows = [
        r for r in tables.ledger_rows(tables.routing_evidence_file())
        if str(r.get('source') or '') == 'arena'
    ]
    rows.sort(key=lambda r: as_int(r.get('id'), 0), reverse=True)
    rows = rows[:limit]
    return {
        'results': [
            {
                'sessionId': str(r.get('session_id') or ''),
                'taskType': str(r.get('task_type') or 'arena'),
                'model': str(r.get('model') or ''),
                'provider': str(r.get('provider') or ''),
                'won': bool(r.get('ok')),
                'tokens': int(r.get('input_tokens') or 0) + int(r.get('output_tokens') or 0),
                'durationMs': int(r.get('duration_ms') or 0),
                'at': str(r.get('created_at') or ''),
                'prompt': str(r.get('prompt') or ''),
            }
            for r in rows
        ]
    }


@router.get('/routing/suggestions')
async def getRoutingSuggestions(prompt: str = Query('', max_length=2000), limit: int = Query(5, ge=1, le=25)):
    """Per-model win-rate suggestions from arena evidence (surpass #1 loop):
    models ranked by win rate over ≥1 recorded verdict, for the composer's
    'which model wins this kind of prompt' affordance."""
    from app.services.memory_store import tables

    groups: dict[tuple[str, str], dict[str, int]] = {}
    for r in tables.ledger_rows(tables.routing_evidence_file()):
        if str(r.get('source') or '') != 'arena':
            continue
        model = str(r.get('model') or '')
        if not model:
            continue
        key = (model, str(r.get('provider') or ''))
        bucket = groups.setdefault(key, {'wins': 0, 'total': 0, 'tokens': 0, 'n': 0})
        bucket['total'] += 1
        bucket['wins'] += 1 if r.get('ok') else 0
        bucket['tokens'] += int(r.get('input_tokens') or 0) + int(r.get('output_tokens') or 0)
        bucket['n'] += 1
    ranked = sorted(
        groups.items(),
        key=lambda kv: (-(kv[1]['wins'] / kv[1]['total']), -kv[1]['total']),
    )[:limit]
    suggestions = [
        {
            'modelId': key[0],
            'provider': key[1],
            'wins': bucket['wins'],
            'total': bucket['total'],
            'winRate': round(bucket['wins'] / max(1, bucket['total']), 3),
            'avgTokens': round(bucket['tokens'] / bucket['n']) if bucket['n'] else 0,
        }
        for key, bucket in ranked
    ]
    return {'prompt': prompt, 'suggestions': suggestions}


@router.get('/skills/suggestions')
async def getSkillSuggestions(days: int = Query(30, ge=1, le=30), limit: int = Query(10, ge=1, le=50)):
    """Per-skill measured effect from the turn ledger (audit D1, migration 050).

    The ``routing_evidence`` pattern applied to skills: turn_outcomes rows are
    the event records (``skills_injected`` JSON per turn), and this read
    aggregates them with json_each — no second store, no writer to keep in
    sync. Per skill: resolved rate on turns that carried it vs turns that did
    not, over the retention window. ``lift`` is the difference — the honest
    number a demotion suggestion or a transcript provenance chip would quote.
    Rows predating 050 (NULL columns) never enter the aggregate.

    The aggregation is ``turn_outcomes.skill_lift_detail`` — the SAME
    function the per-turn demotion prior reads (``capabilities_prompt``).
    This route used to carry its own copy of the SQL with ``COUNT(*)`` where
    the ledger used ``COUNT(ok)``, so the Skills UI and the ranker could
    disagree about the same skill's lift; one statement now backs both.
    """
    from app.services.turn_outcomes import skill_lift_detail

    windowDays = max(1, min(int(days or 30), 30))
    # A DB without 050 (or without json_each) yields no suggestions — the
    # aggregate degrades to empty, never errors into the caller.
    rows = skill_lift_detail(windowDays, limit=limit)
    suggestions: list[dict[str, object]] = []
    for row in rows:
        suggestions.append(
            {
                'skill': str(row.get('skill') or ''),
                'turnsWith': int(row.get('turnsWith') or 0),
                'okRateWith': row.get('okRateWith'),
                'turnsWithout': int(row.get('turnsWithout') or 0),
                'okRateWithout': row.get('okRateWithout'),
                'lift': row.get('lift'),
            }
        )
    return {'days': windowDays, 'suggestions': suggestions}

@router.post('/memory/open-folder')
async def openMemoryFolder() -> dict[str, object]:
    """Open the markdown memory mirror in the OS file browser.

    The mirror (`<dataDir>/memory/`) is the human-readable view of the facts
    store; this is the door that makes it a surface rather than a file tree
    a person has to find by hand. Returns the path even when the open fails,
    so the UI can still show where the folder lives.
    """
    import sys
    from pathlib import Path

    from app.services.memory_store.mirror import mirror_root

    folder = Path(mirror_root())
    folder.mkdir(parents=True, exist_ok=True)
    opened = False
    try:
        if sys.platform == 'win32':
            os.startfile(str(folder))  # noqa: S606 — Explorer, user-requested
            opened = True
        elif sys.platform == 'darwin':
            import subprocess

            subprocess.Popen(['open', str(folder)])  # noqa: S603, S607
            opened = True
        else:
            import subprocess

            subprocess.Popen(['xdg-open', str(folder)])  # noqa: S603, S607
            opened = True
    except Exception:
        opened = False
    return {'ok': True, 'path': str(folder), 'opened': opened}
