"""Fresh-context auto-review for skill and memory writes.

The proposer has a conversation and a goal; the reviewer has neither. Every new
skill or memory is shown to a separate call to the SAME model with only the
proposed write and the existing library — every skill, the stored memory, and
the registered tools — and that call decides whether the write is new, an update
to one existing item, a merge of several, or not worth keeping.

Why this exists: memory had a duplicate refusal but no cross-store check, and
skills had neither. The model could file a `skill_create` that shadowed a
bundled skill, and the distiller's judge saw only a truncated catalogue. The
existing independent reviewer could not judge redundancy because it never saw
the catalogue, and it required a DIFFERENT model — which an install with one
configured model cannot satisfy.

It is a veto only when it answers. `update`, `merge` and `reject` name
something real; an answer it could not produce (prose, a timeout, no reviewer)
is a missing opinion, not a refusal — the write proceeds and the daily review
pass still sees the row. Failing closed there refused every write from a model
that cannot hold the JSON contract, which is how `remember` shipped dead on the
1.7B turns.

No similarity threshold is consulted here. Lexical ranking only orders the
snapshot (which bodies to include); the decision is the reviewer's. Targets it
names are validated against the real library, so a hallucinated skill name
cannot redirect a write.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from app.json_narrowing import as_str

logger = logging.getLogger(__name__)

_DECISIONS = ('new', 'update', 'merge', 'reject')
_REVIEW_TIMEOUT_S = 60
_MAX_BODY_CHARS = 1800
_MAX_BODY_MATCHES = 8
_MAX_TOOL_MATCHES = 12
_SKILL_CATALOGUE_CAP = 14_000
_SKILL_BODY_CAP = 14_000
_MEMORY_CAP = 10_000
_TOOL_CAP = 8_000

AUTO_REVIEW_SYSTEM = (
    'You are a separate, fresh-context reviewer for another agent\'s library write. '
    'You do not see that agent\'s conversation or reasoning. You see the write it '
    'proposes and the existing library: every skill, the stored memory, and the '
    'registered tools.\n'
    '\n'
    'Decide whether the write is genuinely new or should reuse something that already '
    'exists. Reply with STRICT JSON only — no prose, no code fences:\n'
    '{"decision":"new|update|merge|reject","targets":["<exact existing name or key>"],'
    '"reason":"<short>"}\n'
    '\n'
    'Rules:\n'
    '- new: no existing skill, memory entry or tool covers this.\n'
    '- update: ONE existing item covers it. Name it exactly. A skill update is a patch; '
    'a memory update is remember with the existing key.\n'
    '- merge: TWO OR MORE existing items each cover part of it. Name them. A merge writes '
    'the combined content into one survivor and leaves every other skill ENABLED; never '
    'retire, disable or delete anything.\n'
    '- reject: duplicate, task state, completed-work log, an invented procedure, or '
    'otherwise not durable.\n'
    '- A skill is a procedure (ordered steps). A memory is a rule that stands without the '
    'conversation that produced it. A tool is an existing callable capability. The same '
    'content in another store is still a duplicate: a one-line rule belongs in memory, a '
    'procedure belongs in a skill, and behavior a tool already documents belongs in '
    'neither.\n'
    '- Prefer update or merge over new. Do not name a target that is not in the library. '
    'If the write is not worth keeping, reject it.'
)

_FENCE_RE = re.compile(r'```(?:json)?\s*(\{.*\})\s*```', re.DOTALL)


def _tokens(text: str) -> set[str]:
    try:
        from app.services.tools.retrieval import _tokenize

        return set(_tokenize(text or ''))
    except Exception:  # noqa: BLE001
        return set()


def _overlap(query: set[str], text: str) -> int:
    """Lexical ranking only — not a decision threshold."""
    if not query:
        return 0
    return len(query & _tokens(text))


def _oneLine(text: object, limit: int = 600) -> str:
    return ' '.join(str(text or '').split())[:limit]


def _section(header: str, lines: list[str], cap: int) -> str:
    text = ((header + '\n') if header else '') + '\n'.join(lines)
    if len(text) > cap:
        text = text[:cap].rsplit('\n', 1)[0] + f'\n… (truncated at {cap} chars)'
    return text


def _sessionContext() -> tuple[str, str, str, str]:
    """(workspace, agent_id, model, provider) for the current session, best effort.

    The provider rides along because the reviewer factory needs it: a session's
    model id is often namespaced ("gateway/model"), and that is a MODEL on a
    named provider — the factory does not parse the model argument, so the
    provider must be named alongside it. Without it the factory cannot tell a
    namespaced id from a "provider/model" config string and fails closed.
    """
    workspace = ''
    model = ''
    provider = ''
    try:
        from app.services.workbench import workbench as _wb
        from app.services.workbench.context import currentSessionId

        sid = str(currentSessionId.get() or '')
        if sid:
            session = _wb.get_workbench_session(sid)
            if session is not None:
                workspace = as_str(getattr(session, 'workspacePath', '') or '', '')
                model = as_str(getattr(session, 'model', '') or '', '')
                provider = as_str(getattr(session, 'provider', '') or '', '')
    except Exception:  # noqa: BLE001
        pass
    agent_id = ''
    try:
        from app.services import session_scope

        agent_id = session_scope.bot_agent_id(session_scope.resolve_scope())
    except Exception:  # noqa: BLE001
        pass
    return workspace, agent_id, model, provider


def _currentScope() -> str:
    try:
        from app.services import session_scope

        return session_scope.resolve_scope()
    except Exception:  # noqa: BLE001
        return 'global'


def _skillSection(query: set[str], workspace: str, agent_id: str) -> tuple[str, int]:
    from app.services import skill_service

    try:
        skills = skill_service.list_all(workspace or None, agent_id)
    except Exception as exc:  # noqa: BLE001
        return f'EXISTING SKILLS\n(unavailable: {type(exc).__name__})', 0
    names = [
        as_str(skill.get('name'), '')
        for skill in skills
        if as_str(skill.get('name'), '')
    ]
    header = (
        f'EXISTING SKILLS ({len(skills)}; every name is listed — review all of them)'
        if skills
        else 'EXISTING SKILLS (none)'
    )
    ranked = sorted(
        skills,
        key=lambda s: _overlap(
            query,
            f'{as_str(s.get("name"), "")} {as_str(s.get("description"), "")} '
            f'{as_str(s.get("instructions"), "")}',
        ),
        reverse=True,
    )
    lines: list[str] = ['ALL NAMES: ' + ', '.join(names), '']
    for skill in ranked:
        name = as_str(skill.get('name'), '')
        if not name:
            continue
        state = 'enabled' if skill.get('enabled') else 'disabled'
        scope = as_str(skill.get('scope'), '') or 'unknown'
        lines.append(
            f'- {name} [{scope}, {state}] — {_oneLine(skill.get("description"), 520)}'
        )
    # Names ride the first line, so a truncation below costs descriptions, never
    # the ability to say every skill was seen.
    bodyParts: list[str] = []
    for skill in ranked[:_MAX_BODY_MATCHES]:
        name = as_str(skill.get('name'), '')
        body = str(skill.get('instructions') or '').strip()
        if not name or not body:
            continue
        bodyParts.append(f'## {name}\n{body[:_MAX_BODY_CHARS]}')
    section = _section(header, lines, _SKILL_CATALOGUE_CAP)
    if bodyParts:
        section += '\n\nCLOSEST SKILL BODIES:\n' + _section(
            '', bodyParts, _SKILL_BODY_CAP
        )
    return section, len(skills)


def _memorySection(query: set[str], scope: str, workspace: str) -> tuple[str, int]:
    from app.services import memory_store

    facts: list[Any] = []
    total = 0
    try:
        facts = list(memory_store.list_facts(scope=scope, limit=200))
        total = memory_store.count_facts(scope=scope)
    except Exception as exc:  # noqa: BLE001
        facts = []
        total = 0
        logger.debug('auto-review memory snapshot failed: %s', exc)
    lines: list[str] = []
    for fact in facts:
        key = as_str(fact.get('factKey'), '')
        if not key:
            continue
        title = _oneLine(fact.get('title'), 120)
        desc = _oneLine(fact.get('description'), 220)
        lines.append(f'- {key}: {title}' + (f' — {desc}' if desc else ''))
    header = f'EXISTING MEMORY ({total}; showing {len(lines)})'
    if total > len(facts):
        header += ' — the model can page the rest with list_facts'
    projectCount = 0
    if workspace:
        try:
            from app.services import project_memory

            entries = project_memory.read_entries(workspace)
            projectCount = len(entries)
            for entry in entries[:100]:
                desc = _oneLine(getattr(entry, 'description', ''), 220)
                lines.append(
                    f'- project:{entry.title}: {entry.title}'
                    + (f' — {desc}' if desc else '')
                )
        except Exception:  # noqa: BLE001
            pass
    if projectCount:
        header = f'EXISTING MEMORY ({total + projectCount}; {projectCount} project)'
    # Order the details by lexical closeness without changing which keys are
    # listed: every key above is visible, only the descriptions are ranked.
    if len(lines) > 120:
        def rank(line: str) -> int:
            return _overlap(query, line)

        head = lines[:20]
        tail = sorted(lines[20:], key=rank, reverse=True)[:100]
        lines = head + tail
        header += f' — {len(lines)} of {total + projectCount} shown'
    return _section(header, lines, _MEMORY_CAP), total + projectCount


def _toolSection(query: set[str]) -> tuple[str, int]:
    try:
        from app.services.tool_registry import listRaw

        tools = listRaw()
    except Exception as exc:  # noqa: BLE001
        return f'EXISTING TOOLS\n(unavailable: {type(exc).__name__})', 0
    names = [as_str(t.get('name'), '') for t in tools if as_str(t.get('name'), '')]
    ranked = sorted(
        tools,
        key=lambda t: _overlap(
            query, f'{as_str(t.get("name"), "")} {as_str(t.get("description"), "")}'
        ),
        reverse=True,
    )
    lines = [', '.join(names)]
    for tool in ranked[:_MAX_TOOL_MATCHES]:
        name = as_str(tool.get('name'), '')
        desc = _oneLine(tool.get('description'), 220)
        if name:
            lines.append(f'- {name}: {desc}')
    header = f'EXISTING TOOLS ({len(tools)}; all names, closest descriptions)'
    return _section(header, lines, _TOOL_CAP), len(tools)


def buildLibrarySnapshot(
    query_text: str,
    *,
    workspace: str = '',
    agent_id: str = '',
    scope: str = '',
) -> tuple[str, dict[str, int]]:
    """Every skill, the stored memory and the tool names, bounded and honest."""
    query = _tokens(query_text)
    skillText, skillCount = _skillSection(query, workspace, agent_id)
    memoryText, memoryCount = _memorySection(query, scope or _currentScope(), workspace)
    toolText, toolCount = _toolSection(query)
    snapshot = '\n\n'.join([skillText, memoryText, toolText])
    return snapshot, {
        'skills': skillCount,
        'memory': memoryCount,
        'tools': toolCount,
    }


def _resolveTargets(
    targets: list[str], known: dict[str, set[str]]
) -> tuple[list[dict[str, str]], str]:
    resolved: list[dict[str, str]] = []
    lower = {kind: {name.lower() for name in names} for kind, names in known.items()}
    for target in targets:
        name = target.strip()
        if not name:
            continue
        kind = next((k for k in ('skill', 'memory', 'tool') if name in known[k]), '')
        if not kind:
            kind = next(
                (k for k in ('skill', 'memory', 'tool') if name.lower() in lower[k]), ''
            )
        if not kind:
            return [], f'reviewer named {name!r}, which is not in the library'
        # Normalize to the library's own spelling so a case-insensitive match
        # still resolves to the exact key the caller must use.
        if name not in known[kind]:
            name = next(
                (n for n in known[kind] if n.lower() == name.lower()), name
            )
        resolved.append({'kind': kind, 'name': name})
    return resolved, ''


def _parseReview(
    raw: object, known: dict[str, set[str]], model: str
) -> dict[str, object]:
    text = str(raw or '').strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001
        return {
            'decision': 'unavailable',
            'targets': [],
            'reason': f'reviewer did not return JSON: {text[:120]!r}',
            'model': model,
        }
    if not isinstance(data, dict):
        return {
            'decision': 'unavailable',
            'targets': [],
            'reason': 'reviewer returned a non-object JSON value',
            'model': model,
        }
    decision = as_str(data.get('decision'), '').strip().lower()
    reason = _oneLine(data.get('reason'), 300)
    if decision not in _DECISIONS:
        return {
            'decision': 'unavailable',
            'targets': [],
            'reason': f'reviewer returned unknown decision {decision!r}',
            'model': model,
        }
    rawTargets = data.get('targets')
    targets = (
        [str(t) for t in rawTargets if str(t).strip()]
        if isinstance(rawTargets, list)
        else []
    )
    resolved, problem = _resolveTargets(targets, known)
    if problem:
        # A fabricated target is not a verdict. Fail closed so a create cannot
        # be redirected to a skill that does not exist.
        return {
            'decision': 'unavailable',
            'targets': [],
            'reason': problem,
            'model': model,
        }
    if decision == 'update' and len(resolved) != 1:
        return {
            'decision': 'unavailable',
            'targets': [],
            'reason': f'update needs exactly one target (got {len(resolved)})',
            'model': model,
        }
    if decision == 'merge' and len(resolved) < 2:
        return {
            'decision': 'unavailable',
            'targets': [],
            'reason': f'merge needs at least two targets (got {len(resolved)})',
            'model': model,
        }
    return {
        'decision': decision,
        'targets': resolved,
        'reason': reason,
        'model': model,
    }


def _knownTargets(
    workspace: str, agent_id: str, scope: str
) -> dict[str, set[str]]:
    known: dict[str, set[str]] = {'skill': set(), 'memory': set(), 'tool': set()}
    try:
        from app.services import skill_service

        known['skill'] = {
            as_str(s.get('name'), '')
            for s in skill_service.list_all(workspace or None, agent_id)
            if as_str(s.get('name'), '')
        }
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.services import memory_store

        known['memory'] = {
            as_str(f.get('factKey'), '')
            for f in memory_store.list_facts(scope=scope, limit=2000)
            if as_str(f.get('factKey'), '')
        }
    except Exception:  # noqa: BLE001
        pass
    if workspace:
        try:
            from app.services import project_memory

            for entry in project_memory.read_entries(workspace):
                known['memory'].add(entry.title)
                known['memory'].add(f'project:{entry.title}')
        except Exception:  # noqa: BLE001
            pass
    try:
        from app.services.tool_registry import listRaw

        known['tool'] = {
            as_str(t.get('name'), '') for t in listRaw() if as_str(t.get('name'), '')
        }
    except Exception:  # noqa: BLE001
        pass
    return known


def _reviewClient() -> tuple[Any | None, str, str]:
    """The session's own model on a fresh call, or a named refusal.

    This deliberately does NOT go through ``review_gate.resolve_independent_reviewer``:
    that gate refuses a reviewer whose model matches the producer, which an
    install with one configured model can never satisfy. The independence here
    is contextual — a fresh call, no conversation, and the whole library — and
    every result still goes through the proposal rails or the memory write door.
    """
    workspace, agent_id, model, provider = _sessionContext()
    del workspace, agent_id
    try:
        from app.services.workbench.providers import make_review_llm_client

        client = make_review_llm_client(None, model=model, provider=provider)
    except Exception as exc:  # noqa: BLE001 — fail closed on any factory fault
        return None, model, f'reviewer could not be created: {type(exc).__name__}'
    if client is None:
        return None, model, 'no model available for auto-review'
    return client, model, ''


async def reviewLibraryWrite(
    *,
    kind: str,
    name: str = '',
    description: str = '',
    body: str = '',
    fact: str = '',
    workspace: str = '',
    agent_id: str = '',
    scope: str = '',
) -> dict[str, object]:
    """Review one proposed skill/memory write against the existing library.

    Returns ``decision`` = new | update | merge | reject | unavailable | skipped.
    ``update``/``merge`` carry exact, validated ``targets`` as ``{kind, name}``.
    """
    try:
        from app.services import brain_config_service

        if not bool(brain_config_service.getRuntimeConfig().get('autoReview', True)):
            return {
                'decision': 'skipped',
                'targets': [],
                'reason': 'auto-review is off',
                'model': '',
            }
    except Exception:  # noqa: BLE001
        pass
    sessionWorkspace, sessionAgent, sessionModel, _sessionProvider = _sessionContext()
    workspace = workspace or sessionWorkspace
    agent_id = agent_id or sessionAgent
    scopeValue = scope or _currentScope()
    proposal = '\n'.join(
        part
        for part in (
            f'kind: {kind}',
            f'name: {name}' if name else '',
            f'description: {description}' if description else '',
            f'body:\n{body}' if body else '',
            f'fact: {fact}' if fact else '',
        )
        if part
    )
    snapshot, counts = buildLibrarySnapshot(
        ' '.join([name, description, body, fact]),
        workspace=workspace,
        agent_id=agent_id,
        scope=scopeValue,
    )
    client, model, refusal = _reviewClient()
    if client is None:
        return {
            'decision': 'unavailable',
            'targets': [],
            'reason': refusal or 'no auto-review model',
            'model': model or sessionModel,
            'counts': counts,
        }
    prompt = [
        {'role': 'system', 'content': AUTO_REVIEW_SYSTEM},
        {
            'role': 'user',
            'content': (
                f'PROPOSED WRITE\n{proposal}\n\n'
                f'EXISTING LIBRARY\n{snapshot}\n\n'
                'Return the strict JSON decision now.'
            ),
        },
    ]
    try:
        raw = await asyncio.wait_for(client(prompt), timeout=_REVIEW_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {
            'decision': 'unavailable',
            'targets': [],
            'reason': f'reviewer did not answer within {_REVIEW_TIMEOUT_S}s',
            'model': model,
            'counts': counts,
        }
    except Exception as exc:  # noqa: BLE001  # noqa: BLE001 — an outage is not an approval
        return {
            'decision': 'unavailable',
            'targets': [],
            'reason': f'{type(exc).__name__}: {str(exc)[:150]}',
            'model': model,
            'counts': counts,
        }
    result = _parseReview(raw, _knownTargets(workspace, agent_id, scopeValue), model)
    result['counts'] = counts
    return result


def formatTargets(targets: object) -> str:
    """Human-readable target list for a refusal message."""
    if not isinstance(targets, list):
        return ''
    parts: list[str] = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        kind = as_str(target.get('kind'), '')
        name = as_str(target.get('name'), '')
        if name:
            parts.append(f'{kind} {name!r}' if kind else repr(name))
    return ', '.join(parts)
