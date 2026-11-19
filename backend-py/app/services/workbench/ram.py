"""Meta-RAM: token-bounded workspace memory topologies.

LOAD reads a per-workspace Markdown topology into the prompt at task and
subagent start; OFFLOAD writes a bounded work-state summary back at subtask
completion and before compaction. The topology lives under the ONE memory
store (``dataDir/memory/workspaces/<slug>/``) — the fact mirror scans only
top-level ``*.md``, so this subdirectory never enters the fact index.

Two topologies are recognized, after the RAM write-up:

- coding: ``QUICKSTART.md`` (current state), ``REGISTRY.md`` (symptom →
  pointer), ``fixes/*.md`` (one bounded episode each), ``SCHEMA.md``.
- research: ``paper-state.md``, ``traceability.md``, ``hypotheses.md``.
  These are authored, not fabricated: OFFLOAD updates a marked block in
  ``paper-state.md`` only when the file already exists; LOAD reads whichever
  of them are present.

Writes happen only in the parent turn's lifecycle (pre-compaction hook,
fanout completion handler). Subagents LOAD through ``subagent_memory_block``
and never write. Policy revisions are filed as ``ram_policy`` proposals and
apply only on human approval, with a rollback entry for 1-click Undo.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from app.json_narrowing import as_dict, as_int, as_list, as_str

logger = logging.getLogger(__name__)

# LOAD order: state first, then the symptom index, then research state.
_LOAD_ORDER = (
    'QUICKSTART.md',
    'REGISTRY.md',
    'paper-state.md',
    'traceability.md',
    'hypotheses.md',
    'SCHEMA.md',
)
_BEGIN = '<!-- ram:begin -->'
_END = '<!-- ram:end -->'
# Char budgets (~4 chars/token): parent 2400 ≈ 600 tokens, worker 1200 ≈ 300.
_PARENT_BUDGET = 2400
_WORKER_BUDGET = 1200
_FILE_CAP = 4096
_REGISTRY_MAX_LINES = 40
_FIXES_KEEP = 12
_FIXES_MAX_AGE_DAYS = 30

_SCHEMA_TEXT = """# RAM topology schema

- `QUICKSTART.md` — current work state. The `ram:begin/end` block is
  rewritten by OFFLOAD; text outside the block is yours and is preserved.
- `REGISTRY.md` — symptom → pointer lines, one per line, inside the
  `ram:begin/end` block. Pointers name files under `fixes/`.
- `fixes/<stamp>-<reason>.md` — one bounded episode: goal, errors seen,
  tools used, outcome. Oldest entries are pruned by an approved policy
  review, never silently.
- Research topology (author-created): `paper-state.md` (OFFLOAD updates its
  marked block when present), `traceability.md`, `hypotheses.md`.

LOAD is token-bounded and read-only for subagents. OFFLOAD runs in the
parent turn only. Policy revisions arrive as proposals with a diff and
apply only on approval; Undo restores the exact prior files.
"""


def topology_dir(workspace: str) -> Path | None:
    workspace = (workspace or '').strip()
    if not workspace:
        return None
    from app.lib.paths import dataPath

    norm = str(Path(workspace)).lower().replace('\\', '/')
    slug = hashlib.sha1(norm.encode('utf-8')).hexdigest()[:12]
    base = re.sub(r'[^A-Za-z0-9_.-]', '_', Path(workspace).name)[:40] or 'workspace'
    return dataPath('memory', 'workspaces', f'{base}-{slug}')


def _small_window(session: object) -> bool:
    from app.services.best_effort import best_effort

    with best_effort('ram.small-window'):
        from app.services.workbench.loop.surface import _modelCapabilityProfile

        profile = _modelCapabilityProfile(session)  # type: ignore[arg-type]
        window = as_int(profile.get('context_window'), 0)
        return bool(profile.get('loopback')) and 0 < window <= 16384
    return False


def load_block(workspace: str, session: object = None, *, subagent: bool = False) -> str:
    """The bounded topology excerpt for a prompt, or '' when nothing exists."""
    root = topology_dir(workspace)
    if root is None or not root.is_dir():
        return ''
    budget = _WORKER_BUDGET if subagent else _PARENT_BUDGET
    if session is not None and _small_window(session):
        budget = min(budget, 1200)
    parts: list[str] = []
    used = 0
    for name in _LOAD_ORDER:
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding='utf-8').strip()
        except OSError:
            continue
        if not text:
            continue
        room = budget - used
        if room <= 80:
            break
        if len(text) > room:
            text = text[:room].rstrip() + '\n[…topology truncated to budget]'
        parts.append(f'--- {name} ---\n{text}')
        used += len(text)
    if not parts:
        return ''
    header = 'Workspace memory topology (RAM' + (', read-only' if subagent else '') + '):'
    return header + '\n' + '\n'.join(parts)


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get('text'), str):
                out.append(block['text'])
        return '\n'.join(out)
    if isinstance(content, dict):
        inner = content.get('content')
        if isinstance(inner, str):
            return inner
    return ''


def _tool_names(messages: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for msg in messages:
        if msg.get('role') != 'assistant':
            continue
        content = msg.get('content')
        if isinstance(content, str) and content.strip().startswith('{'):
            try:
                envelope = json.loads(content)
            except ValueError:
                continue
            for call in as_list(envelope.get('tool_calls')):
                fn = as_dict(as_dict(call).get('function'))
                name = as_str(fn.get('name'))
                if name and name not in names:
                    names.append(name)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'tool_use':
                    name = as_str(block.get('name'))
                    if name and name not in names:
                        names.append(name)
    return names


def _symptoms(messages: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for msg in messages:
        if msg.get('role') != 'tool':
            continue
        text = _text_of(msg.get('content')).strip()
        first = text.splitlines()[0] if text else ''
        if re.match(r'(?i)^(error|\[blocked\])', first) or 'failed' in first.lower():
            line = first[:160]
            if line not in out:
                out.append(line)
        if len(out) >= 5:
            break
    return out


def _replace_marked_block(text: str, block: str) -> str:
    marked = f'{_BEGIN}\n{block}\n{_END}'
    if _BEGIN in text and _END in text:
        head, rest = text.split(_BEGIN, 1)
        _, tail = rest.split(_END, 1)
        return (head.rstrip() + '\n\n' + marked + '\n' + tail.lstrip()).strip() + '\n'
    return (text.rstrip() + '\n\n' + marked + '\n').strip() + '\n'


def _extract_marked_block(text: str) -> str:
    if _BEGIN in text and _END in text:
        return text.split(_BEGIN, 1)[1].split(_END, 1)[0].strip()
    return ''


def offload(
    workspace: str,
    session: object,
    messages: list[dict[str, Any]],
    *,
    reason: str,
) -> dict[str, Any] | None:
    """Write one bounded work-state episode. Parent-turn lifecycle only."""
    root = topology_dir(workspace)
    if root is None or not messages:
        return None
    from app.services.best_effort import best_effort

    with best_effort('ram.offload-config'):
        from app.services import brain_config_service as cfg

        if not bool(cfg.getRuntimeConfig().get('memoryEnabled', True)):
            return None
    try:
        root.mkdir(parents=True, exist_ok=True)
        fixes = root / 'fixes'
        fixes.mkdir(exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S', time.gmtime())
        safe_reason = re.sub(r'[^A-Za-z0-9_-]', '-', reason)[:32] or 'offload'
        rel = f'fixes/{stamp}-{safe_reason}.md'

        goal = as_str(getattr(session, 'goal', '') or '')
        if not goal:
            for msg in messages:
                if msg.get('role') == 'user':
                    goal = _text_of(msg.get('content')).strip()[:200]
                    break
        outcome = ''
        for msg in reversed(messages):
            if msg.get('role') == 'assistant':
                text = _text_of(msg.get('content')).strip()
                if text and not text.startswith('{'):
                    outcome = text[:600]
                    break
        tools = _tool_names(messages)[:12]
        symptoms = _symptoms(messages)

        episode = [
            f'# Episode {stamp} ({reason})',
            f'Goal: {goal or "(unset)"}',
            f'Tools: {", ".join(tools) or "(none)"}',
        ]
        if symptoms:
            episode.append('Errors seen:')
            episode.extend(f'- {s}' for s in symptoms)
        episode.append(f'Outcome: {outcome or "(no final text)"}')
        (root / rel).write_text('\n'.join(episode)[:2048] + '\n', encoding='utf-8')

        state = [
            f'## Work state (offloaded {stamp}, {reason})',
            f'Goal: {goal or "(unset)"}',
            f'Outcome: {outcome[:300] or "(no final text)"}',
            f'Tools: {", ".join(tools) or "(none)"}',
            f'Latest episode: {rel}',
        ]
        state_block = '\n'.join(state)

        quick = root / 'QUICKSTART.md'
        prior_quick = quick.read_text(encoding='utf-8') if quick.is_file() else ''
        quick.write_text(
            _replace_marked_block(prior_quick, state_block)[:_FILE_CAP], encoding='utf-8'
        )

        registry = root / 'REGISTRY.md'
        prior_reg = registry.read_text(encoding='utf-8') if registry.is_file() else ''
        lines = [
            ln
            for ln in _extract_marked_block(prior_reg).splitlines()
            if ln.strip() and ln not in [f'{s} → {rel}' for s in symptoms]
        ]
        lines.extend(f'{s} → {rel}' for s in symptoms)
        registry.write_text(
            _replace_marked_block(prior_reg, '\n'.join(lines[-_REGISTRY_MAX_LINES:]))[:_FILE_CAP],
            encoding='utf-8',
        )

        paper = root / 'paper-state.md'
        if paper.is_file():
            prior_paper = paper.read_text(encoding='utf-8')
            paper.write_text(
                _replace_marked_block(prior_paper, state_block)[:_FILE_CAP], encoding='utf-8'
            )

        schema = root / 'SCHEMA.md'
        if not schema.is_file():
            schema.write_text(_SCHEMA_TEXT, encoding='utf-8')

        logger.info('ram offload: %s (%s, %d messages)', rel, reason, len(messages))
        return {'file': rel, 'reason': reason, 'symptoms': len(symptoms)}
    except OSError:
        logger.debug('ram offload failed', exc_info=True)
        return None


def review_policy(workspace: str) -> dict[str, Any] | None:
    """Three-step map-reduce over the topology → a policy-revision payload.

    Map: per-file stats and the fixes inventory. Reduce: merge into findings
    (oversize state/index, stale or excess episodes, dangling pointers).
    Propose: revised file contents + removals, with a unified diff and the
    exact prior bytes for Undo. Returns None when nothing needs revising.
    """
    root = topology_dir(workspace)
    if root is None or not root.is_dir():
        return None

    # 1 — map.
    stats: dict[str, dict[str, Any]] = {}
    for name in _LOAD_ORDER:
        path = root / name
        if path.is_file():
            text = path.read_text(encoding='utf-8')
            stats[name] = {'chars': len(text), 'lines': len(text.splitlines())}
    fixes_dir = root / 'fixes'
    episodes = sorted(
        (p for p in fixes_dir.glob('*.md')) if fixes_dir.is_dir() else [],
        key=lambda p: p.name,
    )
    now = time.time()
    ages = {p.name: (now - p.stat().st_mtime) / 86400 for p in episodes}

    # 2 — reduce.
    findings: list[str] = []
    oversized = [n for n, s in stats.items() if s['chars'] > _FILE_CAP]
    if oversized:
        findings.append(f'oversize topology files: {", ".join(oversized)}')
    stale = [n for n, age in ages.items() if age > _FIXES_MAX_AGE_DAYS]
    excess = len(episodes) - _FIXES_KEEP
    if stale:
        findings.append(f'{len(stale)} episode(s) older than {_FIXES_MAX_AGE_DAYS} days')
    if excess > 0:
        findings.append(f'{excess} episode(s) beyond the {_FIXES_KEEP}-episode keep window')
    reg_text = (root / 'REGISTRY.md').read_text(encoding='utf-8') if (root / 'REGISTRY.md').is_file() else ''
    dangling = [
        ln
        for ln in _extract_marked_block(reg_text).splitlines()
        if '→' in ln and not (root / ln.split('→', 1)[1].strip()).is_file()
    ]
    if dangling:
        findings.append(f'{len(dangling)} registry pointer(s) name missing episodes')
    if not findings:
        return None

    # 3 — propose.
    before: dict[str, str | None] = {}
    revised: dict[str, str] = {}
    removed: list[str] = []

    drop = set(stale)
    if excess > 0:
        drop.update(p.name for p in episodes[:excess])
    for name in sorted(drop):
        rel = f'fixes/{name}'
        before[rel] = (fixes_dir / name).read_text(encoding='utf-8')
        removed.append(rel)

    if 'REGISTRY.md' in stats:
        prior = (root / 'REGISTRY.md').read_text(encoding='utf-8')
        kept = [
            ln
            for ln in _extract_marked_block(prior).splitlines()
            if ln.strip()
            and '→' in ln
            and ln.split('→', 1)[1].strip().removeprefix('fixes/') not in drop
        ]
        new = _replace_marked_block(prior, '\n'.join(kept[-_REGISTRY_MAX_LINES:]))[:_FILE_CAP]
        if new != prior:
            before['REGISTRY.md'] = prior
            revised['REGISTRY.md'] = new

    for name in oversized:
        if name in revised or name == 'REGISTRY.md':
            continue
        prior = (root / name).read_text(encoding='utf-8')
        # -1 leaves room for the closing newline: a file capped at exactly
        # _FILE_CAP + 1 would be re-flagged as oversize on every later review.
        new = prior[:_FILE_CAP - 1].rstrip() + '\n'
        if new != prior:
            before[name] = prior
            revised[name] = new

    diffs: list[str] = []
    for name, new in revised.items():
        prior = before.get(name) or ''
        diffs.extend(
            difflib.unified_diff(
                prior.splitlines(), new.splitlines(), fromfile=name, tofile=name, lineterm=''
            )
        )
    for rel in removed:
        diffs.append(f'--- {rel}\n+++ /dev/null (episode pruned; restorable via Undo)')

    return {
        'workspace': str(workspace),
        'findings': findings,
        'revised': revised,
        'removed': removed,
        'before': before,
        'diff': '\n'.join(diffs)[:6000],
    }


def maybe_file_review(workspace: str, session_id: str = '') -> str | None:
    """Opt-in automatic review: file a ram_policy proposal, never apply it."""
    from app.services.best_effort import best_effort

    with best_effort('ram.review-config'):
        from app.services import brain_config_service as cfg

        if not bool(cfg.getRuntimeConfig().get('ramReview', False)):
            return None
    return file_review_proposal(workspace, session_id)


def file_review_proposal(workspace: str, session_id: str = '') -> str | None:
    """Run the map-reduce review and file its proposal. Returns the id or None."""
    payload = review_policy(workspace)
    if payload is None:
        return None
    from app.services.harness_self_improve import save_proposal

    findings = as_list(payload.get('findings'))
    try:
        row = save_proposal(
            problem='Workspace memory topology needs a policy revision: '
            + '; '.join(as_str(f) for f in findings),
            evidence=f'Workspace: {workspace}\n\n{as_str(payload.get("diff"))[:3500]}',
            proposal='Apply the reviewed topology revision (diff in evidence). '
            'Trimmed episodes stay restorable through the recorded Undo entry.',
            rollback='Undo restores the exact prior topology files from the '
            'rollback entry recorded at apply time.',
            kind='ram_policy',
            payload=payload,
            session_id=session_id,
        )
    except ValueError:
        return None  # an open proposal already covers this revision
    return as_str(row.get('id'))


def apply_policy(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply an approved ram_policy payload; record the exact Undo snapshot."""
    from app.services.rollback_store import record_rollback

    workspace = as_str(payload.get('workspace'))
    root = topology_dir(workspace)
    if root is None:
        return {'ok': False, 'error': 'payload has no workspace'}
    before = {as_str(k): v for k, v in as_dict(payload.get('before')).items()}
    revised = {as_str(k): as_str(v) for k, v in as_dict(payload.get('revised')).items()}
    removed = [as_str(r) for r in as_list(payload.get('removed'))]
    try:
        root_resolved = root.resolve()
        for rel, content in revised.items():
            target = (root / rel).resolve()
            if not target.is_relative_to(root_resolved):
                return {'ok': False, 'error': f'path escapes the topology dir: {rel}'}
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8')
        for rel in removed:
            target = (root / rel).resolve()
            if not target.is_relative_to(root_resolved):
                return {'ok': False, 'error': f'path escapes the topology dir: {rel}'}
            target.unlink(missing_ok=True)
    except OSError as exc:
        return {'ok': False, 'error': str(exc)}
    entry = record_rollback(
        'restore_ram_topology',
        workspace,
        before={'workspace': workspace, 'files': before},
        after={'revised': sorted(revised), 'removed': removed},
    )
    return {
        'ok': True,
        'rollbackId': as_str(entry.get('id')),
        'revised': sorted(revised),
        'removed': removed,
    }


def export_turn(
    session_id: str,
    model: str,
    provider: str,
    messages: list[dict[str, Any]],
    *,
    turn_end_reason: str = '',
) -> bool:
    """Opt-in JSONL trajectory row with trainable-token masking.

    Masking is a per-message flag, not a deletion: system/tool rows and
    tool-call envelopes are protocol (trainable=False); natural user and
    assistant text is trainable. Injected memory tails never reach here —
    they are stripped before persistence.
    """
    from app.services.best_effort import best_effort

    with best_effort('ram.export-config'):
        from app.services import brain_config_service as cfg

        if not bool(cfg.getRuntimeConfig().get('ramTrajectoryExport', False)):
            return False
    if not session_id or not messages:
        return False
    from app.lib.paths import dataPath

    safe = re.sub(r'[^A-Za-z0-9_.-]', '_', session_id)[:120]
    path = dataPath('exports', 'trajectories', f'{safe}.jsonl')
    rows = []
    for msg in messages:
        role = as_str(msg.get('role'))
        text = _text_of(msg.get('content'))
        trainable = role in ('user', 'assistant') and bool(text.strip())
        if trainable and role == 'assistant' and text.strip().startswith('{'):
            try:
                envelope = json.loads(text)
                if as_list(envelope.get('tool_calls')):
                    trainable = False
            except ValueError:
                pass
        rows.append({'role': role, 'trainable': trainable, 'content': text})
    record = {
        'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'sessionId': session_id,
        'model': model,
        'provider': provider,
        'turnEndReason': turn_end_reason,
        'messages': rows,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
        return True
    except OSError:
        logger.debug('ram trajectory export failed', exc_info=True)
        return False