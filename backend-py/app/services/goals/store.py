"""Goal environments — the file-backed store over the pure rules.

One door to ``<state>/goals.json`` (the same filestore discipline the memory
store uses, so a goal write lands in the same backup and integrity path as a
fact). The rules themselves live in ``records.py``; nothing here decides what
is allowed, it only stores what the rules accepted — with one exception that
belongs to storage: ``goal_movements`` records a REFUSAL as a row, because a
blocked write that leaves no trace is a blocked write the model repeats.

The store is one JSON document with six sections (``envs`` / ``runs`` /
``artifacts`` / ``movements`` / ``claims`` / ``marks``) whose rows keep the
SQL-era column names; the document is read, mutated and atomically written
under one store lock, which is what replaces SQLite's write lock for the
version compare-and-set in :func:`edit_env`.

Sync by design, like every other ``memory_store`` module. The tool layer is async and calls these
through ``asyncio.to_thread`` so a slow disk never blocks the event loop.
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterable, Mapping

from app.services.goals import records as R
from app.services.memory_store import tables

_SECTIONS = ('envs', 'runs', 'artifacts', 'movements', 'claims', 'marks')


def _now() -> float:
    return time.time()


def _load() -> dict[str, list[dict[str, Any]]]:
    data = tables.load_rows(tables.goals_file())
    doc: dict[str, list[dict[str, Any]]] = {s: [] for s in _SECTIONS}
    for row in data:
        section = str(row.get('section') or '')
        body = row.get('row')
        if section in doc and isinstance(body, dict):
            doc[section].append(dict(body))
    return doc


def _save(doc: dict[str, list[dict[str, Any]]]) -> None:
    rows: list[dict[str, Any]] = []
    for section in _SECTIONS:
        for row in doc.get(section) or []:
            rows.append({'section': section, 'row': row})
    tables.save_rows(tables.goals_file(), rows)


def _env_of(row: dict[str, Any]) -> R.Env:
    return R.Env(
        id=str(row.get('id')), parent_id=(str(row['parent_id']) if row.get('parent_id') else None),
        title=str(row.get('title')), goal=str(row.get('goal')),
        inputs=list(json.loads(str(row.get('inputs') or '[]'))), outputs=list(json.loads(str(row.get('outputs') or '[]'))),
        bound=R.Bound.from_json(row.get('bound')), status=str(row.get('status')), owner=str(row.get('owner')),
        version=int(row.get('version') or 1), depth=int(row.get('depth') or 0),
        created_at=float(row.get('created_at') or 0.0), updated_at=float(row.get('updated_at') or 0.0),
        close_reason=str(row.get('close_reason') or ''),
        workspace=str(row.get('workspace') or ''),
    )


def _run_of(row: dict[str, Any]) -> R.Run:
    return R.Run(
        id=str(row.get('id')), env_id=str(row.get('env_id')), status=str(row.get('status')), round=int(row.get('round') or 0),
        model=str(row.get('model') or ''), session_id=str(row.get('session_id') or ''),
        bot_id=str(row.get('bot_id') or ''), reason=str(row.get('reason') or ''),
        started_at=float(row.get('started_at') or 0.0), updated_at=float(row.get('updated_at') or 0.0),
    )


def _insert_env(env: R.Env) -> None:
    path = tables.goals_file()
    with tables.store_lock(str(path)):
        doc = _load()
        doc['envs'].append(
            {
                'id': env.id, 'parent_id': env.parent_id, 'title': env.title, 'goal': env.goal,
                'inputs': json.dumps(env.inputs), 'outputs': json.dumps(env.outputs),
                'bound': env.bound.to_json(), 'status': env.status, 'owner': env.owner,
                'version': env.version, 'depth': env.depth, 'close_reason': env.close_reason,
                'created_at': env.created_at, 'updated_at': env.updated_at, 'workspace': env.workspace,
            }
        )
        _save(doc)


def load_envs(workspace: str | None = None) -> list[R.Env]:
    """Goals in one workspace (or every goal when ``workspace`` is None). A project's goals
    are its own: the panel and the prompt read the workspace they are in, never the table."""
    envs = [_env_of(r) for r in _load()['envs']]
    envs.sort(key=lambda e: (e.depth, e.created_at))
    if workspace is None:
        return envs
    return [e for e in envs if e.workspace == workspace]


def get_env(env_id: str) -> R.Env | None:
    for row in _load()['envs']:
        if str(row.get('id')) == env_id:
            return _env_of(row)
    return None


def ensure_root(seat_tools: Iterable[str], workspace: str = "") -> R.Env:
    """The proxy's own environment, seeded once and never re-seeded.

    Never overwritten on a later call: a root that came back with fresh bounds would silently re-widen
    every goal under it, which is the one failure this layer exists to make impossible.
    """
    existing = get_env(R.root_id_for(workspace))
    if existing is not None:
        return existing
    root = R.seed_root(seat_tools, _now(), workspace)
    _insert_env(root)
    return root


def create_env(*, parent_id: str, title: str, goal: str, inputs: object = None,
               outputs: object = None, bound: Mapping[str, object] | None = None) -> tuple[R.Env, list[str]]:
    env, removed = R.create_env(load_envs(), parent_id=parent_id, title=title, goal=goal,
                                inputs=inputs, outputs=outputs, bound=bound, now=_now())
    _insert_env(env)
    return env, removed


def edit_env(env_id: str, patch: Mapping[str, object], *, expected_version: int | None = None,
             actor: str = 'agent') -> tuple[R.Env, list[str]]:
    path = tables.goals_file()
    with tables.store_lock(str(path)):
        doc = _load()
        rows = doc['envs']
        idx = next((i for i, r in enumerate(rows) if str(r.get('id')) == env_id), -1)
        if idx < 0:
            raise R.GoalRefusal([f'no goal "{env_id}"'])
        env = _env_of(rows[idx])
        unmet = R.unmet_outputs(env, load_artifacts(env_id), str(patch.get('reason') or '')) if patch.get('status') == 'done' else []
        # `actor='user'` is the human door: it may set a ROOT's bound (the global one and a
        # workspace's), because a project's permissions are the trader/user's to decide. The MODEL
        # still cannot, and the narrowing rules apply to a human edit exactly as they do to a goal.
        next_env, clamped = R.edit_env(env, patch, unmet=unmet, expected_version=expected_version,
                                       now=_now(), allow_root=(actor == 'user'))
        # Compare-and-set on the version the caller read: two turns editing the same goal cannot lose one
        # another's change, and a stale write is refused rather than silently overwriting. Under the
        # store lock the read and the write are one unit, so the CAS is exact.
        if env.version != rows[idx].get('version'):
            raise R.GoalRefusal([f'"{env.title}" changed while you were editing it — read it again and retry'])
        rows[idx] = {
            'id': next_env.id, 'parent_id': next_env.parent_id, 'title': next_env.title, 'goal': next_env.goal,
            'inputs': json.dumps(next_env.inputs), 'outputs': json.dumps(next_env.outputs),
            'bound': next_env.bound.to_json(), 'status': next_env.status, 'owner': next_env.owner,
            'version': next_env.version, 'depth': next_env.depth,
            'close_reason': str(patch.get('reason') or env.close_reason),
            'created_at': next_env.created_at, 'updated_at': next_env.updated_at,
            'workspace': next_env.workspace,
        }
        _save(doc)
    return next_env, clamped


# ── artifacts + movements ───────────────────────────────────────────────────


def load_artifacts(env_id: str | None = None) -> list[dict[str, Any]]:
    doc = _load()
    out: list[dict[str, Any]] = []
    for row in doc['artifacts']:
        if env_id and str(row.get('env_id')) != env_id:
            continue
        item = dict(row)
        item['movements'] = [
            {k: m.get(k) for k in ('at', 'actor', 'from_env', 'to_env', 'ok', 'reason')}
            for m in doc['movements']
            if str(m.get('artifact_id')) == str(row.get('id'))
        ]
        out.append(item)
    out.sort(key=lambda r: float(r.get('created_at') or 0.0))
    return out


def record_artifact(*, env_id: str, kind: str, ref: Mapping[str, object], summary: str, produced_by: str) -> dict[str, Any]:
    body = (summary or '').strip()
    if len(body) < 4:
        raise R.GoalRefusal(['summary is too short to say what the artifact is'])
    store = str(ref.get('store') or '')
    key = str(ref.get('key') or ref.get('file_id') or '')
    if not store or not key:
        raise R.GoalRefusal(['a ref must name where the bytes live: store plus key (or file_id)'])
    if get_env(env_id) is None:
        raise R.GoalRefusal([f'no goal "{env_id}" to file this under'])
    art_id = R.new_id('art', body)
    now = _now()
    path = tables.goals_file()
    with tables.store_lock(str(path)):
        doc = _load()
        doc['artifacts'].append(
            {'id': art_id, 'env_id': env_id, 'kind': kind or 'note',
             'ref': json.dumps({'store': store, 'key': key}), 'summary': body,
             'produced_by': (produced_by or 'model')[:60], 'created_at': now, 'updated_at': now}
        )
        doc['movements'].append(
            {'artifact_id': art_id, 'at': now, 'actor': (produced_by or 'model')[:60],
             'from_env': '', 'to_env': env_id, 'ok': 1, 'reason': 'recorded'}
        )
        _save(doc)
    return {'id': art_id, 'env_id': env_id, 'kind': kind or 'note', 'summary': body,
            'ref': json.dumps({'store': store, 'key': key})}


def move_artifact(artifact_id: str, to_env: str, *, actor: str, reason: str) -> dict[str, Any]:
    """Move, or record the refusal. ``to_env`` must exist; when it does not, the refusal is still a row."""
    path = tables.goals_file()
    with tables.store_lock(str(path)):
        doc = _load()
        art = next((r for r in doc['artifacts'] if str(r.get('id')) == artifact_id), None)
        if art is None:
            raise R.GoalRefusal([f'no artifact "{artifact_id}" — read the goals for its id'])
        now = _now()
        ok = any(str(r.get('id')) == to_env for r in doc['envs'])
        detail = reason if ok else f'refused: {to_env} is not a goal'
        doc['movements'].append(
            {'artifact_id': artifact_id, 'at': now, 'actor': (actor or 'model')[:60],
             'from_env': str(art.get('env_id')), 'to_env': to_env, 'ok': 1 if ok else 0, 'reason': detail[:200]}
        )
        if ok:
            art['env_id'] = to_env
            art['updated_at'] = now
        _save(doc)
    return {'id': artifact_id, 'ok': ok, 'to': to_env, 'reason': detail}


# ── claims + marks ──────────────────────────────────────────────────────────


def load_claims(env_id: str | None = None) -> list[dict[str, Any]]:
    doc = _load()
    marked = {str(r.get('claim_id')) for r in doc['marks']}
    claims = [dict(r) for r in doc['claims'] if not env_id or str(r.get('env_id')) == env_id]
    claims.sort(key=lambda r: float(r.get('made_at') or 0.0))
    for c in claims:
        c['marked'] = c['id'] in marked
    return claims


def load_marks(env_id: str | None = None) -> list[dict[str, Any]]:
    doc = _load()
    claim_envs = {str(r.get('id')): str(r.get('env_id')) for r in doc['claims']}
    marks = [
        dict(r) for r in doc['marks']
        if not env_id or claim_envs.get(str(r.get('claim_id'))) == env_id
    ]
    marks.sort(key=lambda r: float(r.get('at') or 0.0))
    return marks


def runs_for(env_id: str) -> list[dict[str, object]]:
    """Every run in a goal, for the API/UI read. Rounds and status only — the ledger of a run is
    its reason field, and the artifacts carry the movements."""
    rows = [r for r in _load()['runs'] if str(r.get('env_id')) == env_id]
    rows.sort(key=lambda r: float(r.get('started_at') or 0.0), reverse=True)
    return [{
        'id': str(r.get('id')), 'envId': str(r.get('env_id')), 'sessionId': str(r.get('session_id')),
        'status': str(r.get('status')), 'round': int(r.get('round') or 0), 'model': str(r.get('model')),
        'reason': str(r.get('reason')), 'startedAt': float(r.get('started_at') or 0.0),
    } for r in rows]


def claim_env(claim_id: str) -> str | None:
    """The goal a claim belongs to. A mark rules on a CLAIM, so its environment is the claim's —
    resolving it from the session instead aimed the verdict at whatever goal the turn happened to be
    in, and the app-owned root refused it."""
    for row in _load()['claims']:
        if str(row.get('id')) == claim_id:
            return str(row.get('env_id'))
    return None


def add_claim(env_id: str, text: str, by: str, about_artifact_id: str = '') -> dict[str, Any]:
    if get_env(env_id) is None:
        raise R.GoalRefusal([f'no goal "{env_id}"'])
    claim = R.make_claim(env_id, text, by, _now(), about_artifact_id)
    path = tables.goals_file()
    with tables.store_lock(str(path)):
        doc = _load()
        doc['claims'].append(
            {'id': claim.id, 'env_id': claim.env_id, 'text': claim.text,
             'about_artifact_id': claim.about_artifact_id, 'made_by': claim.made_by, 'made_at': claim.made_at}
        )
        _save(doc)
    return {'id': claim.id, 'env_id': claim.env_id, 'text': claim.text}


def add_mark(claim_id: str, verdict: str, evidence: Mapping[str, object] | None, by: str) -> dict[str, Any]:
    doc = _load()
    row = next((r for r in doc['claims'] if str(r.get('id')) == claim_id), None)
    if row is None:
        raise R.GoalRefusal([f'no claim "{claim_id}" — a mark rules on a claim that exists'])
    claim = R.Claim(id=str(row.get('id')), env_id=str(row.get('env_id')), text=str(row.get('text')),
                    made_by=str(row.get('made_by')), made_at=float(row.get('made_at') or 0.0))
    mark = R.to_mark(claim, verdict, evidence, by, _now())
    path = tables.goals_file()
    with tables.store_lock(str(path)):
        doc = _load()
        doc['marks'].append(
            {'id': mark['id'], 'env_id': mark['env_id'], 'claim_id': mark['claim_id'], 'text': mark['text'],
             'verdict': mark['verdict'], 'evidence': mark['evidence'], 'by': mark['by'], 'at': mark['at']}
        )
        _save(doc)
    return mark


# ── runs ────────────────────────────────────────────────────────────────────


def live_run(env_id: str, session_id: str) -> R.Run | None:
    rows = [
        r for r in _load()['runs']
        if str(r.get('env_id')) == env_id and str(r.get('session_id')) == session_id
        and str(r.get('status')) in ('running', 'woken')
    ]
    rows.sort(key=lambda r: float(r.get('started_at') or 0.0), reverse=True)
    return _run_of(rows[0]) if rows else None


def session_run(env_id: str, session_id: str) -> R.Run | None:
    """The run this session has in a goal — live first, then PARKED.

    A parked run is still the session's run: the digest that reported "no run open here" while one sat
    parked at its ceiling told the model to open a new one, which is exactly how a ceiling gets reset."""
    live = live_run(env_id, session_id)
    if live is not None:
        return live
    rows = [
        r for r in _load()['runs']
        if str(r.get('env_id')) == env_id and str(r.get('session_id')) == session_id
        and str(r.get('status')) == 'parked'
    ]
    rows.sort(key=lambda r: float(r.get('started_at') or 0.0), reverse=True)
    return _run_of(rows[0]) if rows else None


def env_of_session(session_id: str) -> str:
    """The goal this session is working in: its live run's, its parked run's, else the root."""
    doc = _load()
    for statuses in (('running', 'woken'), ('parked',)):
        rows = [
            r for r in doc['runs']
            if str(r.get('session_id')) == session_id and str(r.get('status')) in statuses
        ]
        rows.sort(key=lambda r: float(r.get('started_at') or 0.0), reverse=True)
        if rows:
            return str(rows[0].get('env_id'))
    return R.ROOT_ID


def open_or_resume_run(env_id: str, *, session_id: str, model: str = '', bot_id: str = '') -> tuple[R.Run, list[str]]:
    """Open a run, or resume the one this session already has — including a PARKED one.

    The parked arm is the point: a turn starting and a model calling open must share one rule, or the
    ceiling can be reset by asking again. Started by ``ensure_root`` having run, so the environments
    exist before any run names one.
    """
    env = get_env(env_id)
    if env is None:
        raise R.GoalRefusal([f'no goal "{env_id}"'])
    if env.status != 'active':
        raise R.GoalRefusal([f'"{env.title}" is {env.status}; a run cannot work in a closed goal'])
    live = live_run(env_id, session_id)
    if live is not None:
        if model and live.model != model:
            _update_run(live.id, model=model)
            return R.Run(**{**live.__dict__, 'model': model}), [f'resumed the same run on {model} — round {live.round} stays spent']
        return live, []
    parked_rows = [
        r for r in _load()['runs']
        if str(r.get('env_id')) == env_id and str(r.get('session_id')) == session_id
        and str(r.get('status')) == 'parked'
    ]
    parked_rows.sort(key=lambda r: float(r.get('started_at') or 0.0), reverse=True)
    if parked_rows:
        parked = _run_of(parked_rows[0])
        # A wake opens a NEW window: the ceiling is per window, so the count starts again and the
        # previous window's work is named in the reason. Keeping the count would print "round 5 of
        # 4" forever after the first park — the impossible receipt this layer exists to avoid.
        woken = R.Run(**{**parked.__dict__, 'status': 'running', 'round': 0,
                         'reason': f'resumed after {parked.round} round(s) at the ceiling',
                         'updated_at': _now()})
        _write_run(woken, insert=False)
        return woken, [f'resumed the parked run after {parked.round} round(s) at its ceiling — this window starts at round 0']
    run = R.open_run(env_id, _now(), session_id=session_id, model=model, bot_id=bot_id)
    _write_run(run, insert=True)
    return run, []


def _write_run(run: R.Run, *, insert: bool) -> None:
    path = tables.goals_file()
    with tables.store_lock(str(path)):
        doc = _load()
        row = {
            'id': run.id, 'env_id': run.env_id, 'session_id': run.session_id, 'bot_id': run.bot_id,
            'status': run.status, 'round': run.round, 'model': run.model,
            'reason': run.reason, 'started_at': run.started_at, 'updated_at': run.updated_at,
        }
        if insert:
            doc['runs'].append(row)
        else:
            idx = next((i for i, r in enumerate(doc['runs']) if str(r.get('id')) == run.id), -1)
            if idx >= 0:
                doc['runs'][idx] = row
        _save(doc)


def _update_run(run_id: str, *, model: str) -> None:
    path = tables.goals_file()
    with tables.store_lock(str(path)):
        doc = _load()
        for row in doc['runs']:
            if str(row.get('id')) == run_id:
                row['model'] = model
                row['updated_at'] = _now()
                break
        _save(doc)


def get_run(run_id: str) -> R.Run | None:
    for row in _load()['runs']:
        if str(row.get('id')) == run_id:
            return _run_of(row)
    return None


def spend_rounds(run_id: str, count: int) -> tuple[R.Run, list[str]]:
    """Spend rounds against the run's OWN environment ceiling, parking at the round that crosses it."""
    run = get_run(run_id)
    if run is None:
        raise R.GoalRefusal([f'no run "{run_id}"'])
    if run.status not in ('running', 'woken'):
        raise R.GoalRefusal([f'a run that is "{run.status}" spends no rounds'])
    env = get_run_env(run.env_id)
    ceiling = env.bound.max_rounds if env is not None else 0
    notes: list[str] = []
    current = R.Run(**{**run.__dict__, 'status': 'running'})
    for _ in range(max(0, int(count))):
        current, parked = R.spend_round(current, ceiling, _now())
        if parked:
            notes.append(f'round ceiling {ceiling} on {run.env_id} reached — the run parked with its count kept')
            break
    _write_run(current, insert=False)
    return current, notes


def get_run_env(env_id: str) -> R.Env | None:
    return get_env(env_id)


def move_run(run_id: str, to: str, reason: str) -> R.Run:
    run = get_run(run_id)
    if run is None:
        raise R.GoalRefusal([f'no run "{run_id}"'])
    moved = R.move_run(run, to, reason, _now())
    _write_run(moved, insert=False)
    return moved
