"""Data & Privacy routes — inspect, export, and erase local user data.

The desktop app is local-first: providers' keys, memories, transcripts,
usage history, audit events, and observation screenshots all live on this
device. These endpoints give the Data & Privacy center a single surface
to summarize, export, and delete that data. Every destructive action is
opt-in and returns the number of rows it removed.

Stores (the file homes the retired SQL tables became — see
``memory_store/tables.py`` and the sqlite-imported marker):
  facts (memory/ markdown record), proposals, episodic_timeline,
  kv (state/kv.json — memory purge keeps only live agent keys),
  transcripts (sessions + per-session jsonl),
  session-topics, usage-events, lifecycle (audit), config-audit,
  tool-guardrail-log, routing-evidence, subagent-runs, decision-ledger,
  harness-jobs
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter

from app.lib.paths import dataPath
from app.services import filestore
from app.services.memory_store import mirror as record
from app.services.memory_store import tables
from app.services.post_observation import count_observations

router = APIRouter(prefix='/api/privacy')

# Stores that hold *user-visible* data (used for counts + export).
_COUNT_TABLES = [
    ('facts', 'facts'),
    ('proposals', 'proposals'),
    ('episodic_timeline', 'timeline'),
    ('sessions', 'sessions'),
    ('messages', 'messages'),
    ('usage_events', 'usageEvents'),
    ('lifecycle', 'auditEvents'),
    ('config_audit', 'configAudit'),
    ('routing_evidence', 'routingEvidence'),
    ('subagent_runs', 'subagentRuns'),
    ('decision_ledger', 'decisionLedger'),
    ('harness_jobs', 'harnessJobs'),
]

# Stores cleared by the "erase memory" action (the agent's knowledge of you).
# The learning corpus belongs here too — episodes carry raw
# user-message excerpts mined from every session, and leaving them (plus the
# fingerprints/outcomes derived from them) alive means the next 24h
# consolidation pass re-mines the surviving transcripts and re-writes NEW
# facts about the user after the purge.
_MEMORY_TABLES = [
    'facts',
    'proposals',
    'episodic_timeline',
    'episodes',
    'failure_fingerprints',
    'turn_outcomes',
    # Sub-agent run rows carry the user's task goals and result summaries —
    # conversation-derived content, so "erase my memory" must reach them.
    'subagent_runs',
    # Refine-store entries are authored from episode digests (which carry
    # user-message excerpts), so they are user-derived knowledge: an
    # "erase memory" that left them standing would keep injecting the
    # user's old lessons into every prompt.
    'refine_entries',
    'refine_ledger',
]

# memory_store KV keys that survive the memory purge — the Bot roster is the
# only key with a live writer. agent_jobs is gone (Part 27 C4: the registry
# ledger is in-memory now).
_KV_KEEP_KEYS = ('agent_registry',)

# Stores cleared by the "clear activity logs" action (telemetry/audit).
_LOG_TABLES = [
    'lifecycle',
    'config_audit',
    'tool_guardrail_log',
    'refine_ledger',
    # Diagnostics ledgers: routing outcomes, decision counterfactuals and
    # harness job records are evidence about the system, not memory — but
    # they are conversation-derived and were previously unwipeable.
    'routing_evidence',
    'decision_ledger',
    'harness_jobs',
]


def _sessions_path():
    return dataPath('transcripts') / 'sessions.json'


def _session_rows() -> list[dict[str, object]]:
    data = filestore.read_json(_sessions_path(), default={})
    if isinstance(data, dict):
        return [r for r in data.values() if isinstance(r, dict)]
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def _message_count() -> int:
    directory = dataPath('transcripts')
    total = 0
    if directory.is_dir():
        for child in directory.glob('*.jsonl'):
            try:
                total += sum(1 for line in child.read_text(encoding='utf-8').splitlines() if line.strip())
            except OSError:
                continue
    return total


def _row_count(name: str) -> int:
    if name == 'facts':
        return record.record_stats()['rows']
    if name == 'sessions':
        return len(_session_rows())
    if name == 'messages':
        return _message_count()
    path = _store_path(name)
    if path is None:
        return 0
    if path.suffix == '.jsonl':
        return len(tables.ledger_rows(path))
    return len(tables.load_rows(path))


def _store_path(name: str):
    """The file home of a logical table name (None when it has none)."""
    mapping = {
        'proposals': tables.proposals_file(),
        'episodic_timeline': tables.timeline_file(),
        'episodes': tables.episodes_file(),
        'failure_fingerprints': tables.failure_fingerprints_file(),
        'config_audit': tables.config_audit_file(),
        'session_topics': tables.session_topics_file(),
        'decision_ledger': tables.decision_ledger_file(),
        'routing_evidence': tables.routing_evidence_file(),
        'usage_events': tables.usage_events_file(),
        'lifecycle': tables.lifecycle_file(),
    }
    if name in mapping:
        return mapping[name]
    if name == 'turn_outcomes':
        return tables.turn_outcomes_file()
    if name == 'subagent_runs':
        return dataPath('telemetry') / 'subagent-runs.jsonl'
    if name == 'tool_guardrail_log':
        return dataPath('telemetry') / 'tool-guardrail-log.jsonl'
    if name == 'harness_jobs':
        return dataPath('telemetry') / 'harness-jobs.json'
    if name == 'refine_entries':
        return tables.state_file('refine-entries.json')
    if name == 'refine_ledger':
        return tables.state_file('refine-ledger.jsonl')
    return None


def _clear_store(name: str) -> int:
    if name == 'facts':
        return tables.clear_record()
    path = _store_path(name)
    if path is None:
        return 0
    if path.suffix == '.jsonl':
        return tables.clear_ledger(path)
    return tables.clear_rows(path)


@router.get('/summary')
async def privacySummary():
    """Counts of everything the app stores about you, in one place."""
    counts: dict[str, int] = {}
    for name, wire_key in _COUNT_TABLES:
        counts[wire_key] = _row_count(name)
    counts['observations'] = count_observations()
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
    counts['dbSizeBytes'] = total_bytes
    return {'counts': counts}


@router.post('/export')
async def privacyExport():
    """Bundle user data (memories, usage, sessions) into one JSON file.

    The export is a plain, readable JSON document — the user owns this
    data, so it should not need the app to read it back. Returns the
    file path + per-section entry counts.
    """
    from app.services.memory_store.rest import _usage_by_model_export

    out: dict[str, object] = {
        'exportedAt': time.time(),
        'app': 'augustharness',
        'facts': [dict(r) for r in record.load_facts()],
        'timeline': tables.load_rows(tables.timeline_file()),
        'usageByModel': _usage_by_model_export(),
        'sessions': _session_rows(),
        'messages': _export_messages(),
    }
    exports_dir = dataPath('exports')
    exports_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime('%Y%m%d-%H%M%S')
    path = exports_dir / f'august-export-{ts}.json'
    path.write_text(json.dumps(out, indent=2, default=str), encoding='utf-8')
    return {
        'path': str(path),
        'bytes': path.stat().st_size,
        'entries': {k: len(v) if isinstance(v, list) else 0 for k, v in out.items()},
    }


def _export_messages(limit_per_session: int = 2000) -> list[dict[str, object]]:
    """Every transcript message (bounded), oldest file first."""
    directory = dataPath('transcripts')
    out: list[dict[str, object]] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob('*.jsonl'), key=lambda p: p.name):
        sid = path.stem
        for row in tables.ledger_rows(path)[:limit_per_session]:
            item = dict(row)
            item.setdefault('session_id', sid)
            out.append(item)
    return out


@router.post('/purge-memories')
async def purgeMemories():
    """Erase the agent's memory of you: facts, proposals, the
    episodic timeline, the learning corpus (episodes, failure fingerprints,
    turn outcomes), and memory-adjacent KV residue.
    The KV purge keeps only the live agent registry key."""
    deleted: dict[str, int] = {}
    for name in _MEMORY_TABLES:
        deleted[name] = _clear_store(name)
    # The KV purge keeps only the live agent registry key.
    try:
        from app.services.memory_store.kv import purge_kv

        deleted['memoryStoreKv'] = purge_kv(_KV_KEEP_KEYS)
    except Exception:
        deleted['memoryStoreKv'] = 0
    # 2.7: the facts DELETE must drop the cached BM25 corpus, or
    # purged facts keep being injected until an unrelated write clears it —
    # a privacy hole ("erase my memory" that doesn't erase recall).
    try:
        from app.services.memory_store.fact_retrieval import invalidate_fact_index

        invalidate_fact_index()
    except Exception:
        pass
    try:
        from app.services.memory_store.mirror import rebuild_mirror

        rebuild_mirror()
    except Exception:
        pass
    return {'deleted': deleted}


@router.post('/clear-logs')
async def clearLogs():
    """Clear activity/audit/guardrail logs + observation screenshots."""
    deleted: dict[str, int] = {}
    for name in _LOG_TABLES:
        deleted[name] = _clear_store(name)
    removed_screenshots = 0
    try:
        obs_dir = dataPath('observations')
        if obs_dir.is_dir():
            for f in obs_dir.glob('*.png'):
                try:
                    f.unlink()
                    removed_screenshots += 1
                except Exception:
                    pass
    except Exception:
        pass
    deleted['observationScreenshots'] = removed_screenshots
    return {'deleted': deleted}


@router.post('/delete-usage')
async def deleteUsage():
    """Delete token usage history (usage_events)."""
    return {'deleted': {'usage_events': _clear_store('usage_events')}}


@router.post('/delete-sessions')
async def deleteSessions():
    """Delete every chat/workbench session, transcript, and topic row.

    Workbench sessions are removed through their own cascade-aware
    deleter so transcript child rows and the JSON export file stay in sync.
    """
    from app.services.workbench.sessions import delete_workbench_session, list_workbench_sessions

    removed_workbench = 0
    for summary in list_workbench_sessions():
        sid = str(summary.get('id') or '')
        if sid and delete_workbench_session(sid):
            removed_workbench += 1
    deleted: dict[str, int] = {}
    # Transcript files (the per-session jsonl) + the sessions index.
    directory = dataPath('transcripts')
    removed_files = 0
    if directory.is_dir():
        for child in list(directory.glob('*.jsonl')):
            try:
                child.unlink()
                removed_files += 1
            except OSError:
                pass
    try:
        _sessions_path().unlink(missing_ok=True)
    except OSError:
        pass
    deleted['messages'] = removed_files
    deleted['session_topics'] = _clear_store('session_topics')
    deleted['sessions'] = removed_files
    deleted['workbenchSessions'] = removed_workbench
    return {'deleted': deleted}
