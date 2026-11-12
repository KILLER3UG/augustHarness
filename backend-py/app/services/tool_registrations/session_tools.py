"""Session-management and unified search tool handlers + registration."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from app.json_narrowing import as_int, as_str
from app.services import tool_registry
from app.services.memory_quality import coercivePreferenceReason as _coercivePreferenceReason
from app.services.sensitive_topics import sensitiveMemoryReason as _sensitiveMemoryReason


def _emitMemoryInvalidate() -> None:
    """Push a realtime invalidate after remember/forget wrote, so an open
    Memory tab refetches its store lists / project view instead of showing a
    stale snapshot (the model saved a memory and the tab never learned)."""
    try:
        from app.services.realtime_bus import emit_invalidate

        emit_invalidate('brain-stores', 'brain-store', 'project-memory', 'memory-workspaces')
    except Exception:
        pass


async def _search(query: str, scope: str = 'files', limit: int = 10) -> str:
    """Unified search across files and web with dedup."""
    from app.json_narrowing import as_int as _as_int

    scopes = [s.strip().lower() for s in (scope or 'files').split(',') if s.strip()]
    lim = max(1, min(_as_int(limit, 10), 30))
    blocks: list[str] = []
    seen: set[str] = set()

    def _dedupe_key(text: str) -> str:
        return ' '.join(text.lower().split())[:200]

    if 'files' in scopes:
        try:
            from app.services.tool_registrations.file_tools import _searchFiles

            fres = await _searchFiles(query)
            if 'No matches' not in fres and 'Error' not in fres[:30]:
                for line in fres.splitlines():
                    k = _dedupe_key(line)
                    if k and k not in seen:
                        seen.add(k)
                        blocks.append(f'[file] {line}')
        except Exception:
            pass
    if 'web' in scopes:
        try:
            import json as _json

            from app.services.tool_registrations.web_tools import _webSearch

            wres = await _webSearch(query)
            # The web search returns a JSON DOCUMENT. Handing the model
            # `str(wres)[:6000]` gave it a truncated blob of it -- escaped
            # quotes, a raw `results` array, and whichever half of the
            # document survived the cut -- so it had to parse escaped JSON
            # to learn a title and a URL. Parse it here and render one
            # readable line per result.
            payload: object = None
            try:
                payload = _json.loads(wres) if isinstance(wres, str) else wres
            except Exception:
                payload = None
            hits = payload.get('results') if isinstance(payload, dict) else None
            if isinstance(hits, list):
                for hit in hits:
                    if not isinstance(hit, dict):
                        continue
                    title = as_str(hit.get('title'), '').strip()
                    url = as_str(hit.get('url'), '').strip()
                    snippet = as_str(hit.get('snippet'), '').strip()
                    if not title and not url:
                        continue
                    k = _dedupe_key(url or title)
                    if k:
                        if k in seen:
                            continue
                        seen.add(k)
                    head = title or url
                    line = f'{head} - {url}' if url and url != head else head
                    if snippet:
                        line = f'{line}: {snippet}'
                    blocks.append(f'[web] {line[:400]}')
            else:
                # Not the expected shape -- a timeout note, an instant
                # answer, an error. Pass the text through, as before.
                text = str(wres)[:6000]
                for line in text.splitlines():
                    k = _dedupe_key(line)
                    if k and k not in seen and len(line.strip()) > 20:
                        seen.add(k)
                        blocks.append(f'[web] {line[:300]}')
        except Exception:
            pass
    if not blocks:
        return f'No results for: {query} (scopes: {",".join(scopes)})'
    header = f'Unified search for: {query} (scopes: {",".join(scopes)})\n'
    return header + '\n'.join(blocks[:lim])


_MEMORY_BRAIN_STORES = frozenset(
    {
        'facts',
        'semantic_facts',
        'memory',
        'kv',
        'memory_store',
        'project-memory',
        'project_memory',
        'projectMemory',
    }
)


async def _brainQuery(store: str, query: str = '', filters: str = '', limit: int = 10) -> str:
    """Read-only query over runtime stores (sessions, messages, daemons, blackboard)."""
    from app.json_narrowing import as_int, as_str
    from app.services.memory_store import brain_query as _bq

    # Coerce before touching: a model passing store=42 or filters as a real
    # dict used to leak `'int' object is not iterable` through the except.
    store = as_str(store)
    query = as_str(query)
    filters = as_str(filters)
    if not store:
        return '{"error": "brain_query: store is required — one of sessions, messages, daemons, blackboard, facts."}'
    if store in _MEMORY_BRAIN_STORES:
        _masterRefusal = _memoryDisabledRefusal()
        if _masterRefusal:
            return _masterRefusal
    try:
        filtersDict = {}
        if filters and filters.strip():
            import json as _json

            try:
                filtersDict = _json.loads(filters)
            except _json.JSONDecodeError:
                pass
        result = _bq(store, query, filtersDict or None, as_int(limit, 10))
        return result
    except Exception as exc:
        return f'{{"error": "brain_query: {exc}"}}'


# Sensitive-topic denylist for the `remember` write door — the scanner now
# lives in services.sensitive_topics (shared with the Part 16 distiller's
# drafted summaries/bodies); this alias keeps the remember door unchanged.


def _deriveFactKey(text: str, scope: str = 'global') -> str:
    """Stable fallback key from the fact text (identical text → same key, so a
    repeated save updates rather than duplicates). Models should pass `key`
    explicitly when updating a known fact.

    No scope prefix. Before 055, ``fact_key`` was unique store-wide, so a
    non-global key had to be namespaced to avoid one Bot silently overwriting
    another's row — and the prefix also changed the key's consolidation slug,
    which made the same content in two scopes two rows that could never be
    merged. The row's key is now unique per scope (UNIQUE(scope, fact_key)), so
    the namespacing is unnecessary and was only manufacturing duplicates.
    The ``scope`` argument is kept for call-site compatibility and is now
    unused; remove it with its three callers.
    """
    slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:48] or 'note'
    return f'model:{slug}'


# Write-time quality gates for the remember door (plan 2026-08-28 Bug 8b):
# length bounds plus a per-turn budget so a runaway loop cannot flood the
# facts store. All refusals are soft ({ok: False, policy}) — the model gets
# the reason and can adjust.
_REMEMBER_MIN_FACT_CHARS = 8
_REMEMBER_MAX_FACT_CHARS = 500
_REMEMBER_MAX_DETAILS_CHARS = 2000
_REMEMBER_PER_TURN_LIMIT = 3
# Per-turn counters keyed by workbench session id (ContextVar set by
# _execute_tool); reset at turn start via reset_remember_turn_budget().
_rememberTurnCounts: dict[str, int] = {}


def reset_remember_turn_budget(sessionId: str = '') -> None:
    """Reset the per-turn remember counter for a session (turn start)."""
    if not sessionId:
        _rememberTurnCounts.clear()
        return
    _rememberTurnCounts.pop(sessionId, None)


def remember_used_this_turn(sessionId: str = '') -> bool:
    """True when `remember` already ran this turn for the session (counter > 0).

    Read by the end-of-turn memory-habit nudge (workbench.py) so a turn that
    already consolidated knowledge does not get nudged again.
    """
    try:
        key = sessionId or _currentRememberSessionKey()
    except Exception:
        return False
    return _rememberTurnCounts.get(key, 0) > 0


def _currentRememberSessionKey() -> str:
    try:
        from app.services.workbench.context import currentSessionId

        return str(currentSessionId.get() or 'default')
    except Exception:
        return 'default'


def _currentWorkspacePath() -> str:
    """The current workbench session's workspacePath ('' when none/home).

    Part 17 Phase A: resolves the session bound to the current tool
    dispatch via the ContextVar so the project-memory write door can route
    remember/forget to the right workspace without changing the tool
    signature everywhere.
    """
    try:
        from app.services.workbench import workbench as _wb
        from app.services.workbench.context import currentSessionId

        sid = str(currentSessionId.get() or '')
        if not sid:
            return ''
        session = _wb.getWorkbenchSession(sid)
        if session is None:
            return ''
        ws = as_str(getattr(session, 'workspacePath', '') or '')
        if ws and Path(ws).resolve() == Path.home().resolve():
            # The home dir is NOT a project root (matches the
            # docstring and every other Part 17 door) — auto-project must
            # not create <home>/.aug/memory/ for home-anchored sessions.
            return ''
        return ws
    except Exception:
        return ''


def _normalizeFactText(text: str) -> str:
    """Comparison form for duplicate detection.

    Case, punctuation and spacing are not meaning: "User prefers pnpm." and
    "user prefers  pnpm" are the same sentence, and storing both is how a
    memory store turns into a list of restatements.
    """
    return re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).strip()


def _unwrapFactValue(raw: object) -> str:
    """The fact sentence out of its stored JSON envelope.

    A fact body is stored JSON-encoded — either ``{"fact","details"}`` or a plain
    string — so any door that shows a body to a human or a model has to unwrap it
    or it renders the quotes.
    """
    import json as _json

    text = str(raw or '')
    try:
        obj = _json.loads(text)
    except Exception:
        return text
    if isinstance(obj, dict) and isinstance(obj.get('fact'), str):
        return str(obj['fact'])
    if isinstance(obj, str):
        return obj
    return text


def _memory_version_conflict(
    key: str,
    *,
    current_text: str,
    current_updated: str,
    current_description: str = '',
    current_source: str = '',
    current_version: object = None,
) -> str:
    """The one version-conflict shape for BOTH memory stores.

    Merge-and-retry cannot depend on which store the key happens to live in, and
    a bare "conflict" is not actionable: the point of refusing the write is that
    the caller now sees what it was about to overwrite, so the live body and its
    stamp come back in the result.
    """
    import json as _json

    return _json.dumps(
        {
            'ok': False,
            'conflict': 'version',
            'key': key,
            'policy': (
                f'nothing was written: "{key}" has changed since the version you passed. '
                'Merge the current text below, then call remember again with key and '
                f'if_version="{current_updated}" — or omit if_version to overwrite deliberately.'
            ),
            'current': {
                'fact': current_text,
                'description': current_description,
                'source': current_source,
                'updated': current_updated,
                'version': current_version,
            },
        }
    )


def _storedFactTexts(scope: str, limit: int = 500) -> list[tuple[str, str]]:
    """``(key, normalized text)`` for the active facts visible to ``scope``.

    Reads the fact body out of the stored JSON envelope (``{"fact","details"}``
    or a plain string), the same unwrap `_list_facts` and the Memory page do.
    """
    import json as _json

    from app.services import memory_store

    out: list[tuple[str, str]] = []
    try:
        rows = memory_store.list_facts(scope=scope, limit=limit)
    except Exception:
        return out
    for row in rows:
        raw = str(row.get('factValue') or '')
        text = raw
        try:
            obj = _json.loads(raw)
            if isinstance(obj, dict) and isinstance(obj.get('fact'), str):
                text = str(obj['fact'])
            elif isinstance(obj, str):
                text = obj
        except Exception:
            pass
        out.append((str(row.get('factKey') or ''), _normalizeFactText(text)))
    return out


def _duplicateFactKey(text: str, scope: str, own_key: str) -> str:
    """Key of an existing ACTIVE fact with the same text under a DIFFERENT key.

    ``own_key`` is excluded because writing the same key is an update (the
    documented way to change a memory), not a duplicate.

    This is the STRICTEST rung of memory_quality's dedupe ladder
    (``EXACT_SIMILARITY``, normalized-text identity): consolidation folds at
    0.85 and the distiller refuses at 0.55, so a fact this door accepts can
    still be merged later. Identity, not a ratio, because a near-miss a human
    re-phrased is the model's own way of sharpening a memory, and destroying it
    on a 0.55 is the wrong trade — see memory_quality's own rationale.
    """
    wanted = _normalizeFactText(text)
    if not wanted:
        return ''
    for key, stored in _storedFactTexts(scope):
        if key and key != own_key and stored == wanted:
            return key
    return ''


async def _autoReviewRefusal(
    text: str, description: str, workspace: str, scope: str
) -> str:
    """The fresh-context reviewer's check on a NEW memory write.

    Returns a JSON refusal string, or '' when the write may proceed. An update
    (the key already exists) never reaches here — this is about additions. The
    reviewer decides against the whole library; no similarity threshold is
    consulted.

    It is a veto only when it actually answered: `update`, `merge` and `reject`
    name something real and refuse. An answer it could not produce — prose
    instead of JSON, a timeout, no reviewer — is not a veto, because failing
    closed there refuses every write from a model that cannot hold the JSON
    contract, which is how `remember` shipped dead on the 1.7B turns.
    """
    import json as _json

    from app.services import auto_review

    try:
        review = await auto_review.reviewLibraryWrite(
            kind='memory',
            fact=text,
            description=description,
            workspace=workspace,
            scope=scope,
        )
    except Exception as exc:  # noqa: BLE001 — an outage is not an approval
        review = {
            'decision': 'unavailable',
            'reason': f'{type(exc).__name__}: {str(exc)[:150]}',
            'targets': [],
        }
    decision = str(review.get('decision') or '')
    if decision in ('new', 'skipped'):
        return ''
    reason = str(review.get('reason') or '')
    targets = review.get('targets')
    named = auto_review.formatTargets(targets)
    if decision != 'update' and decision != 'merge' and decision != 'reject':
        # The reviewer could not answer — prose instead of JSON, a timeout, no
        # reviewer configured. That is a missing opinion, not a veto: a gate
        # that fails closed here refuses every write from a model that cannot
        # hold the JSON contract, which is how `remember` shipped dead on the
        # 1.7B turns. The deterministic doors (junk bar, exact/near dedupe)
        # already ran; the write proceeds and the model review pass still sees
        # the row afterwards.
        logging.getLogger(__name__).debug(
            'auto-review unavailable for a memory write: %s', reason or decision
        )
        return ''
    first = targets[0] if isinstance(targets, list) and targets else {}
    targetKind = str(first.get('kind') or '') if isinstance(first, dict) else ''
    targetName = str(first.get('name') or '') if isinstance(first, dict) else ''
    if decision == 'update':
        if targetKind == 'skill':
            policy = (
                f'refused: this is already covered by skill {targetName!r}. Update that '
                'skill instead of writing a duplicate memory.'
            )
        elif targetKind == 'tool':
            policy = (
                f'refused: the {targetName!r} tool already documents this. Do not '
                'store it again as memory.'
            )
        else:
            policy = (
                f'refused: this is already stored as {targetName!r}. Update that entry '
                f'(call remember again with key="{targetName}") instead of creating a '
                'second copy.'
            )
        payload = {'ok': False, 'policy': policy}
        if targetKind == 'memory' and targetName:
            payload['existingKey'] = targetName
        elif targetKind == 'skill' and targetName:
            payload['existingSkill'] = targetName
        elif targetKind == 'tool' and targetName:
            payload['existingTool'] = targetName
        return _json.dumps(payload)
    if decision == 'merge':
        return _json.dumps(
            {
                'ok': False,
                'policy': f'refused: this overlaps {named or "several existing items"}. '
                'Read them, then merge the content into one of them; nothing was written '
                'and no skill was retired.',
                'existingTargets': targets,
            }
        )
    if decision == 'reject':
        return _json.dumps(
            {
                'ok': False,
                'policy': f'refused: auto-review rejected this memory — '
                f'{reason or "not durable"}. Do not retry the same text.',
            }
        )
    return ''


async def _remember(
    fact: str,
    key: str = '',
    category: str = 'general',
    details: str = '',
    expires_at: str = '',
    title: str = '',
    kind: str = '',
    scope: str = '',
    description: str = '',
    if_version: str = '',
    aliases: object = None,
    **_extra: object,
) -> str:
    """Single model write door into durable memory (facts store).

    Gated by the ``modelMemoryWrites`` toggle and a sensitive-topic denylist
    (unless ``memorySensitiveTopics`` is on). Writes via ``save_fact`` with
    ``source='model'`` (upsert over the key = update-over-duplicate) and
    records a rollback entry so the write is undoable. Every entry gets a
    short human ``title`` (derived from the text when not supplied) and a
    ``kind`` (fact | lesson | preference | skill-note | profile, where profile
    entries are the always-in-context lane rather than keyword-recalled ones).

    Part 17 Phase A: ``scope='project'`` writes to the workspace's md-file
    project memory instead of the global facts store — same gates, same
    rollback snapshot, same per-turn budget. Inside a session with a
    non-home workspace the default scope IS project (the workspace's
    constraints and lessons belong to that project); the global store is
    reached explicitly with ``scope='global'``.

    File-memory mode (2026-09-12, config ``fileMemory``, the ZCode-parity
    shape): a new project fact becomes its own ``.aug/memory/<slug>.md``
    with frontmatter (name/description/type) and joins the generated
    ``MEMORY-INDEX.md`` index; ``description`` is the one-line recall hook on BOTH
    scopes (global: stored on the fact row, shown in the boot index and
    ranked by BM25; fall back to the fact's first sentence). Legacy
    ``memory.md`` sections keep working; updates land wherever the entry
    already lives."""
    import json as _json

    from app.services import brain_config_service, memory_store
    from app.type_aliases import JsonValue

    # One fact per line: the body is a single statement; depth belongs in
    # `details`. Collapsing whitespace here also keeps the duplicate and
    # title derivations stable across a multi-line paste.
    text = ' '.join(str(fact or '').split())
    if not text:
        return _json.dumps({'ok': False, 'error': 'fact is required'})
    _masterRefusal = _memoryDisabledRefusal()
    if _masterRefusal:
        return _masterRefusal
    # The record's tag is server-derived from the writer's code path. A model
    # claiming `inferred`/`observed` (or a source) is refused rather than
    # silently relabelled: only the user's stated memory is this door's job.
    _claimedProvenance = str(
        _extra.get('provenance') or _extra.get('tag') or _extra.get('source') or ''
    ).strip().lower()
    if _claimedProvenance and _claimedProvenance != 'stated':
        return _json.dumps(
            {
                'ok': False,
                'policy': 'refused: this door writes user-stated memory only. The provenance '
                'tag is set by the code path, not by the model; observed/inferred facts come '
                'from the distiller/import paths. Save the user statement as-is, or do not '
                'save it.',
            }
        )
    detailsText = str(details or '').strip()
    if len(text) < _REMEMBER_MIN_FACT_CHARS:
        return _json.dumps(
            {
                'ok': False,
                'policy': f'refused: fact is too short (min {_REMEMBER_MIN_FACT_CHARS} characters). '
                'Save complete, self-contained facts only.',
            }
        )
    if len(text) > _REMEMBER_MAX_FACT_CHARS:
        return _json.dumps(
            {
                'ok': False,
                'policy': f'refused: fact exceeds {_REMEMBER_MAX_FACT_CHARS} characters. Keep the fact '
                'to one concise sentence and move the rest into details.',
            }
        )
    if len(detailsText) > _REMEMBER_MAX_DETAILS_CHARS:
        return _json.dumps(
            {
                'ok': False,
                'policy': f'refused: details exceed {_REMEMBER_MAX_DETAILS_CHARS} characters. '
                'Trim to the essentials.',
            }
        )
    try:
        cfg = brain_config_service.getRuntimeConfig()
    except Exception:
        cfg = {}
    if not bool(cfg.get('modelMemoryWrites', True)):
        return _json.dumps(
            {
                'ok': False,
                'policy': 'model memory writes are disabled by the user. Do not retry; '
                "tell the user you can't save memories while this setting is off.",
            }
        )
    _automationRefusal = _automationMemoryWriteRefusal()
    if _automationRefusal:
        return _automationRefusal
    if not bool(cfg.get('memorySensitiveTopics', False)):
        # Name the trigger: a refusal that only says "sensitive topic" cannot
        # be told apart from a false positive, so the model just retried and
        # was refused again (audit finding 2026-09-15 #9).
        _trip = _sensitiveMemoryReason(text, details)
        if _trip:
            return _json.dumps(
                {
                    'ok': False,
                    'policy': f'refused: sensitive memory is disabled and the text matched '
                    f'the "{_trip}" pattern (health, ID numbers, minors, beliefs). Do not '
                    'retry the same text. If that match is a technical false positive — the '
                    'word used in an engineering sense — rephrase without it and save again; '
                    'otherwise tell the user you did not store it.',
                }
            )
    # Not behind a setting: filing this would make the store instruct every later
    # session to be dishonest, and a rule the model installed cannot be argued
    # away from the inside. A human writing it from Settings is their own choice
    # about their own assistant, so the door stays on the model's path — the same
    # split the sensitive-topic gate uses.
    _coercive = _coercivePreferenceReason(text, detailsText)
    if _coercive:
        return _json.dumps(
            {
                'ok': False,
                'policy': f'refused: "{_coercive}" asks the assistant to suppress disagreement, '
                'require praise, or claim permissions this install has not granted. That is not a '
                'preference to store. Tell the user it was not saved; if they meant something '
                'narrower ("keep critique specific", "write blunt review comments"), save that.',
            }
        )
    sessKey = _currentRememberSessionKey()
    used = _rememberTurnCounts.get(sessKey, 0)
    if used >= _REMEMBER_PER_TURN_LIMIT:
        return _json.dumps(
            {
                'ok': False,
                'policy': f'refused: per-turn memory budget exhausted ({_REMEMBER_PER_TURN_LIMIT} '
                'facts max per turn). Continue the task; you can save more next turn.',
            }
        )
    # ── Part 17 Phase A: project scope (md-file workspace memory) ──────────
    ws = _currentWorkspacePath()
    scopeNorm = (scope or '').strip().lower()
    if scopeNorm == 'project' or (not scopeNorm and ws):
        if not ws:
            return _json.dumps(
                {
                    'ok': False,
                    'error': 'scope=project requires a workspace; no workspacePath is bound to '
                    'this session (use scope=global or open a workspace first)',
                }
            )
        try:
            cfgPj = brain_config_service.getRuntimeConfig()
        except Exception:
            cfgPj = {}
        if not bool(cfgPj.get('projectMemory', True)):
            return _json.dumps(
                {
                    'ok': False,
                    'policy': 'project memory is disabled by the user (projectMemory setting). '
                    'Do not retry; save with scope=global only if the fact is user-level.',
                }
            )
        entryTitle = (title or '').strip() or memory_store.derive_fact_title(text)
        # file-memory recall hook: explicit description > first sentence of
        # the fact, one line (frontmatter stays valid — no embedded newlines)
        fmDesc = ' '.join((description or '').split()) or text.split('.')[0][:120]
        fmKind = {'user': 'user', 'feedback': 'feedback', 'project': 'project',
                  'reference': 'reference'}.get((category or '').strip().lower(),
                                                'project')
        before: dict[str, object] | None = None
        try:
            from app.services import project_memory as _pm

            existing = _pm.read_entries(ws, title=entryTitle)
            if existing:
                before = {
                    'workspace': ws,
                    'file': existing[0].file,
                    'title': existing[0].title,
                    'body': existing[0].body,
                    'updated': existing[0].updated,
                    # Frontmatter must survive an undo. Without these the
                    # restore wrote a bare `## <title>` section, permanently
                    # downgrading a per-fact file to the legacy layout, and its
                    # recall silently fell back to the full body text.
                    'description': getattr(existing[0], 'description', ''),
                    'kind': getattr(existing[0], 'kind', ''),
                    'perFact': True,
                }
            if not before:
                _reviewRefusal = await _autoReviewRefusal(text, fmDesc, ws, '')
                if _reviewRefusal:
                    return _reviewRefusal
            body = f'{text}\n\n{detailsText}' if detailsText else text
            # A version token against the project store is checked against the
            # entry's own `updated` stamp — the same compare the facts door does
            # in SQL, and the same refusal: a token is a claim that the entry
            # exists, so a create under one is not silently accepted.
            _want_version = (if_version or '').strip()
            if _want_version:
                if not before:
                    return _json.dumps(
                        {
                            'ok': False,
                            'error': f'no stored project memory entry "{entryTitle}" to update; '
                            'drop if_version to create it, or re-read list_facts first',
                        }
                    )
                if str(before.get('updated') or '') != _want_version:
                    return _memory_version_conflict(
                        entryTitle,
                        current_text=str(before.get('body') or ''),
                        current_updated=str(before.get('updated') or ''),
                        current_description=str(before.get('description') or ''),
                    )
            entry = _pm.upsert_entry(ws, entryTitle, body,
                                     description=fmDesc, kind=fmKind,
                                     per_fact=bool(cfgPj.get('fileMemory', True)))
        except Exception as exc:
            return _json.dumps({'ok': False, 'error': f'remember(project) failed: {exc}'})
        _rememberTurnCounts[sessKey] = used + 1
        try:
            from app.services.rollback_store import record_rollback

            record_rollback(
                type='restore_memory_item',
                target=f'project:{entryTitle}',
                before=before,
                after={'workspace': ws, 'title': entryTitle, 'body': body},
            )
        except Exception:
            pass
        _emitMemoryInvalidate()
        return _json.dumps(
            {
                'ok': True,
                'scope': 'project',
                'key': entryTitle,
                'file': entry.file,
                'updated': before is not None,
            }
        )
    cat = (category or 'general').strip().lower()
    if cat not in ('user', 'feedback', 'project', 'reference', 'general'):
        cat = 'general'
    # M-2: the facts-store scope is stamped server-side from the
    # current session — a Bot's home chat writes 'bot:<agentId>' rows, every
    # other session writes 'global'. (The tool's own ``scope`` argument is
    # the Part 17 project/global md-file axis — a different concept.)
    from app.services import session_scope as _ss

    factScope = _ss.resolve_scope()
    explicitKey = (key or '').strip()
    factKey = explicitKey or _deriveFactKey(text, factScope)
    factTitle = (title or '').strip() or memory_store.derive_fact_title(text)
    factKind = (kind or '').strip().lower()
    # ZCode-parity recall hook (044): explicit description > first sentence of
    # the fact — same rule as the project path above, now stored on the row
    # and used by the boot index + BM25 recall text.
    factDesc = ' '.join((description or '').split()) or text.split('.')[0][:120]
    value: JsonValue = text if not detailsText else {'fact': text, 'details': detailsText}
    exp = (expires_at or '').strip() or None
    before = memory_store.get_fact(factKey)  # type: ignore[assignment]
    # ONE scope rule for remember/forget (mirrors the forget door below) and
    # it now matches ``save_fact`` exactly: a key has ONE home, so a write is
    # accepted only when it comes from the row's own scope. A bot-scoped write
    # against a GLOBAL key is refused (it would otherwise replace the shared
    # value while the row stayed global), and a row in a DIFFERENT private scope
    # is likewise never silently overwritten. Re-affirming a global fact is
    # done from a global session, where the two scopes match.
    _beforeScope = str(before.get('scope') or _ss.GLOBAL_SCOPE) if before else ''
    if before and _beforeScope != factScope:
        return _json.dumps(
            {
                'ok': False,
                'policy': f'refused: key "{factKey}" already exists in another memory '
                'scope; pick a distinct key (this session cannot edit it).',
            }
        )
    # Same sentence under a second key is not a new memory. The derived key
    # already catches identical text saved twice with no key, but a model that
    # supplies keys per topic ("user:editor", "user:tooling") writes the same
    # fact into both. `ownKey` is excluded — writing the same key is an update.
    _twin = _duplicateFactKey(text, factScope, factKey)
    if _twin:
        return _json.dumps(
            {
                'ok': False,
                'policy': f'refused: this is already stored as "{_twin}". Update that '
                f'entry instead (call remember again with key="{_twin}"), or forget it '
                'first if it should be replaced. Do not create a second copy.',
                'existingKey': _twin,
            }
        )
    if not before:
        _reviewRefusal = await _autoReviewRefusal(text, factDesc, ws, factScope)
        if _reviewRefusal:
            return _reviewRefusal
    try:
        memory_store.save_fact(
            factKey, value, category=cat, source='model', confidence=0.7,
            expires_at=exp, title=factTitle, kind=factKind, scope=factScope,
            description=factDesc, if_updated_at=(if_version or '').strip(),
            aliases=aliases if isinstance(aliases, (list, tuple, str)) else None,
            source_session=_currentRememberSessionKey()
            if _currentRememberSessionKey() != 'default'
            else '',
        )
    except memory_store.FactVersionConflict as exc:
        _cur = exc.current
        return _memory_version_conflict(
            factKey,
            current_text=_unwrapFactValue(_cur.get('factValue')),
            current_updated=str(_cur.get('updatedAt') or ''),
            current_description=str(_cur.get('description') or ''),
            current_source=str(_cur.get('source') or ''),
            current_version=_cur.get('version'),
        )
    except Exception as exc:
        return _json.dumps({'ok': False, 'error': f'remember failed: {exc}'})
    _rememberTurnCounts[sessKey] = used + 1
    try:
        # M3 usage feedback: a fact the model writes/updates counts as used.
        memory_store.touch_fact_usage([factKey])
    except Exception:
        pass
    _rollbackId = ''
    try:
        from app.services.rollback_store import record_rollback

        _entry = record_rollback(
            type='restore_memory_item',
            target=factKey,
            before=before,
            after={'key': factKey, 'value': value, 'category': cat, 'source': 'model',
                   'title': factTitle, 'description': factDesc},
        )
        _rollbackId = str((_entry or {}).get('id') or '')
    except Exception:
        pass
    _emitMemoryInvalidate()
    _after = memory_store.get_fact(factKey) or {}
    _result: dict[str, object] = {
        'ok': True,
        'key': factKey,
        'category': cat,
        'updated': before is not None,
        'version': _after.get('version'),
        'rollbackId': _rollbackId or None,
    }
    # 059: an edit carries its history — "now X, previously Y" — so the model
    # (and the receipt the user sees) can tell a correction from a first save.
    if before is not None:
        _previous = _unwrapFactValue(before.get('factValue'))
        if _previous and _previous != text:
            _result['previously'] = _previous
    return _json.dumps(_result)


# Sources the model may delete via `forget`: its own writes, user-added
# entries, and imports. Anything else (extracted / consolidation daemons) is
# system-owned and survives model cleanup.
#
# 'harness' is on the list because those facts are the model's OWN lessons —
# the distiller's `memory` verdict writes with this source. Refusing them meant
# a wrong lesson could never be corrected by the actor that formed it, which is
# the failure the substance bar now prevents at write time. The delete is still
# undoable: `record_rollback` below snapshots the row first, and
# `rollback_store` restores it byte-for-byte.
_FORGET_ALLOWED_SOURCES = ('model', 'user', 'harness', '')

# A `forget` is destructive and irreversible from the model's seat, so it
# executes only when the user's LATEST message asked for a deletion — the tool
# description alone did not hold: a scripted turn whose user message never
# mentioned forgetting deleted a fact and reported {"ok": true, "deleted":
# true}. Verbs are matched broadly (a deletion can be phrased many ways) and
# must be accompanied by something that can stand for the stored thing.
_FORGET_INTENT_RE = re.compile(
    r'\b(forget|delete|remove|erase|wipe|purge|clear|drop|unlearn)\b'
    r'[\s\S]{0,40}?\b(memory|memories|fact|facts|entry|entries|note|notes|'
    r'remembered|stored|saved|that|this|it|them)\b',
    re.IGNORECASE,
)


def _memoryDisabledRefusal() -> str:
    """The master memory switch: off means no read, write, delete or inject."""
    import json as _json

    from app.services import brain_config_service

    try:
        cfg = brain_config_service.getRuntimeConfig()
    except Exception:
        return ''
    if bool(cfg.get('memoryEnabled', True)):
        return ''
    return _json.dumps(
        {
            'ok': False,
            'policy': 'refused: memory is turned off in Settings. Do not retry; answer from '
            'the conversation and tell the user memory is disabled.',
        }
    )


def _automationMemoryWriteRefusal() -> str:
    """Refuse memory writes from an unattended automation/cron run.

    Sub-agents are stopped by their deny set; an automation job is a
    different session (created with ``headless=True`` and stamped with
    ``automationJobId``) that reaches the same registry, so without this
    gate a scheduled run could write durable memory no human asked for.
    Read paths stay open; only the two mutating doors call this.
    """
    import json as _json
    import logging as _logging

    try:
        from app.services.workbench.context import currentSessionId

        if not str(currentSessionId.get() or '').strip():
            # Outside a turn there is no automation to refuse for; do not use
            # get_session()'s most-recent fallback, which could point at an
            # automation session and refuse an unrelated direct write.
            return ''
        from app.services.workbench.workbench import get_session

        session = get_session()
        if session is None or not getattr(session, 'headless', False):
            return ''
        meta = getattr(session, 'metadata', None) or {}
        if not str(meta.get('automationJobId') or '').strip():
            return ''
    except Exception:
        _logging.getLogger(__name__).debug('automation memory gate failed', exc_info=True)
        return ''
    return _json.dumps(
        {
            'ok': False,
            'policy': 'refused: automation/cron runs are read-only for memory. Tell the '
            'user this run cannot save or delete memories; write it from an interactive '
            'chat instead.',
        }
    )


def _userAskedForDeletion() -> bool:
    """True when the current turn's latest user message asks to delete memory.

    Reads the same list-safe text the alias pre-read uses, so a multipart
    (image+text) message still contributes its words. No message at all —
    a headless caller with no session — fails closed: a destructive delete
    without a verifiable user ask is refused, not waved through.
    """
    try:
        from app.services.workbench.turn_close import lastUserMessageText
        from app.services.workbench.workbench import get_session

        session = get_session()
        if session is None:
            return False
        text = lastUserMessageText(session)
    except Exception:
        import logging as _logging

        _logging.getLogger(__name__).debug('forget-intent check failed; refusing delete', exc_info=True)
        return False
    return bool(_FORGET_INTENT_RE.search(text or ''))


async def _forget(key: str) -> str:
    """Delete one durable memory entry by exact key.

    The delete half of the model's memory CRUD (with ``remember`` = write and
    ``list_facts`` = read). Gated by ``modelMemoryWrites``; only model / user
    / imported facts can be deleted. A rollback snapshot is recorded so the
    delete is undoable, matching the Memory UI's delete path.

    Part 17 Phase A: ``project:<title>`` deletes a project-memory entry
    (the remember scope='project' write door's counterpart — deletes from
    ``<ws>/.aug/memory/``, moves nothing into the global store).
    """
    import json as _json

    from app.services import brain_config_service, memory_store

    factKey = (key or '').strip()
    if not factKey:
        return _json.dumps({'ok': False, 'error': 'key is required — call list_facts to see keys'})
    _masterRefusal = _memoryDisabledRefusal()
    if _masterRefusal:
        return _masterRefusal
    _automationRefusal = _automationMemoryWriteRefusal()
    if _automationRefusal:
        return _automationRefusal
    # Project-scope delete: `project:<title>` targets the workspace's md
    # entries; a bare key inside a non-home workspace tries the project
    # entries first (matching remember's auto-project default) and falls
    # through to the global facts store when no entry matches.
    ws = _currentWorkspacePath()
    if ws:
        isProjectKey = factKey.startswith('project:')
        title = factKey[len('project:') :].strip() if isProjectKey else factKey
        try:
            from app.services import project_memory as _pm

            existing = [e for e in _pm.read_entries(ws) if e.title.lower() == title.lower()]
        except Exception:
            existing = []
        if existing:
            try:
                cfgF = brain_config_service.getRuntimeConfig()
            except Exception:
                cfgF = {}
            if not bool(cfgF.get('modelMemoryWrites', True)):
                return _json.dumps(
                    {
                        'ok': False,
                        'policy': 'model memory writes are disabled by the user. Do not retry; '
                        "tell the user you can't delete memories while this setting is off.",
                    }
                )
            if not _userAskedForDeletion():
                return _json.dumps(
                    {
                        'ok': False,
                        'policy': "refused: the user's latest message does not ask to delete a "
                        'memory, and forget is destructive. Ask the user to confirm, and only '
                        'call forget again once they do.',
                    }
                )
            entry = existing[0]
            try:
                from app.services import project_memory as _pm

                _pm.delete_entry(ws, entry.title)
            except Exception as exc:
                return _json.dumps({'ok': False, 'error': f'forget(project) failed: {exc}'})
            try:
                from app.services.rollback_store import record_rollback

                record_rollback(
                    type='restore_memory_item',
                    target=f'project:{entry.title}',
                    before={
                        'workspace': ws,
                        'file': entry.file,
                        'title': entry.title,
                        'body': entry.body,
                        'updated': entry.updated,
                    },
                    after=None,
                )
            except Exception:
                pass
            # Global facts promoted FROM this project entry are derived solely
            # from it; forget the source and the copy has nothing left to stand
            # on. Snapshot each derived row before deleting it.
            _derivedProject: list[dict[str, object]] = []
            try:
                _derivedProject = [
                    dict(d)
                    for d in memory_store.dependent_facts(f'project:{entry.title}')
                ]
                for _dep in _derivedProject:
                    _depKey = str(_dep.get('factKey') or _dep.get('key') or '')
                    if not _depKey:
                        continue
                    try:
                        from app.services.rollback_store import record_rollback

                        record_rollback(
                            type='restore_memory_item',
                            target=_depKey,
                            before=_dep,
                            after=None,
                        )
                    except Exception:
                        pass
                    memory_store.delete_fact(_depKey)
            except Exception:
                import logging as _logging

                _logging.getLogger(__name__).debug(
                    'project forget cascade failed', exc_info=True
                )
            _emitMemoryInvalidate()
            return _json.dumps(
                {
                    'ok': True,
                    'deleted': True,
                    'scope': 'project',
                    'key': entry.title,
                    'derivedDeleted': [
                        str(d.get('factKey') or d.get('key') or '') for d in _derivedProject
                    ],
                }
            )
        if isProjectKey:
            return _json.dumps(
                {
                    'ok': False,
                    'deleted': False,
                    'error': f'no project-memory entry titled "{title}" in this workspace',
                }
            )
    try:
        cfg = brain_config_service.getRuntimeConfig()
    except Exception:
        cfg = {}
    if not bool(cfg.get('modelMemoryWrites', True)):
        return _json.dumps(
            {
                'ok': False,
                'policy': 'model memory writes are disabled by the user. Do not retry; '
                "tell the user you can't delete memories while this setting is off.",
            }
        )
    if not _userAskedForDeletion():
        return _json.dumps(
            {
                'ok': False,
                'policy': "refused: the user's latest message does not ask to delete a "
                'memory, and forget is destructive. Ask the user to confirm, and only '
                'call forget again once they do.',
            }
        )
    before = memory_store.get_fact(factKey)
    if not before:
        return _json.dumps(
            {
                'ok': False,
                'deleted': False,
                'error': f'no fact with key "{factKey}" — call list_facts to see current keys',
            }
        )
    source = str(before.get('source') or '')
    if source not in _FORGET_ALLOWED_SOURCES and not source.startswith('imported'):
        return _json.dumps(
            {
                'ok': False,
                'policy': f'refused: "{factKey}" is system-owned (source="{source}"); '
                'only model/user/imported facts can be forgotten.',
            }
        )
    # M-2: a session may only forget facts inside its visible union
    # (global ∪ own scope) — a bot cannot delete another bot's private notes,
    # and a regular chat cannot reach into a bot's home memory.
    from app.services import session_scope as _ss

    rowScope = str(before.get('scope') or 'global')
    if rowScope != _ss.GLOBAL_SCOPE and rowScope != _ss.resolve_scope():
        return _json.dumps(
            {
                'ok': False,
                'policy': f'refused: "{factKey}" belongs to another memory scope; '
                'it is not visible to this session and cannot be forgotten from here.',
            }
        )
    _derived: list[dict[str, object]] = []
    try:
        _derived = [dict(d) for d in memory_store.dependent_facts(factKey)]
        for _dep in _derived:
            _depKey = str(_dep.get('factKey') or _dep.get('key') or '')
            if not _depKey:
                continue
            try:
                from app.services.rollback_store import record_rollback

                record_rollback(
                    type='restore_memory_item', target=_depKey, before=_dep, after=None
                )
            except Exception:
                pass
            memory_store.delete_fact(_depKey)
        deleted = memory_store.delete_fact(factKey)
    except Exception as exc:
        return _json.dumps({'ok': False, 'error': f'forget failed: {exc}'})
    if deleted:
        try:
            from app.services.rollback_store import record_rollback

            record_rollback(
                type='restore_memory_item',
                target=factKey,
                before=before,
                after=None,
            )
        except Exception:
            pass
    if deleted:
        _emitMemoryInvalidate()
    return _json.dumps(
        {
            'ok': bool(deleted),
            'deleted': bool(deleted),
            'key': factKey,
            'derivedDeleted': [str(d.get('factKey') or d.get('key') or '') for d in _derived],
        }
    )


async def _list_facts(
    category: str = '',
    query: str = '',
    limit: int = 50,
    offset: int = 0,
    include_retired: bool = False,
) -> str:
    """List durable memory entries (key, title, category, source).

    The read half of the model's memory CRUD — this is what makes
    ``remember``'s "pass a stable key to update" and ``forget``'s key
    argument actually usable. Gated by ``modelMemoryRead``.

    ENUMERABLE, not a sample: ``offset`` pages through the store and every
    response carries ``total`` + ``hasMore``, so a model auditing its own memory
    knows when it has seen all of it. Before this the answer was hard-capped at
    50 newest rows with no way to ask for the rest — a store the model could
    write to but only partly read back. ``include_retired`` adds the rows
    consolidation parked, which are still stored memory even though recall
    skips them.
    """
    import json as _json

    from app.services import brain_config_service, memory_store

    _masterRefusal = _memoryDisabledRefusal()
    if _masterRefusal:
        return _masterRefusal
    try:
        cfg = brain_config_service.getRuntimeConfig()
    except Exception:
        cfg = {}
    if not bool(cfg.get('modelMemoryRead', True)):
        return _json.dumps(
            {
                'ok': False,
                'policy': 'model memory reads are disabled by the user. Do not retry; '
                'answer from the conversation context instead.',
            }
        )
    lim = max(1, min(as_int(limit, 50), 200))
    off = max(0, as_int(offset, 0))
    cat = (category or '').strip().lower()
    q = (query or '').strip()
    includeRetired = bool(include_retired)
    # 2.2: scope-filter the read so a Bot sees global ∪ its own
    # notes, never another Bot's private keys/titles.
    from app.services import session_scope as _ss

    _readScope = _ss.resolve_scope()
    try:
        rows = (
            memory_store.search_facts(q, cat, scope=_readScope)
            if q
            else memory_store.list_facts(
                cat, scope=_readScope, limit=lim, offset=off,
                include_retired=includeRetired,
            )
        )
        # A text search is not paged (search_facts has its own bound and a
        # relevance order); report its size so `hasMore` stays honest.
        total = (
            len(rows)
            if q
            else memory_store.count_facts(
                cat, scope=_readScope, include_retired=includeRetired
            )
        )
    except Exception as exc:
        return _json.dumps({'ok': False, 'error': f'list_facts failed: {exc}'})
    facts: list[dict[str, object]] = []
    # Inside a workspace, project-memory entries list first
    # (they are the remember-default scope there) with key `project:<title>`.
    # Only on the FIRST page: repeating them on every page would make a paged
    # audit read the same entries over and over.
    ws = _currentWorkspacePath()
    projectCount = 0
    if ws:
        try:
            from app.services import project_memory as _pm

            entries = _pm.read_entries(ws)
            projectCount = len(entries)
            if off == 0:
                for e in entries:
                    if q and q.lower() not in e.title.lower() and q.lower() not in e.body.lower():
                        continue
                    facts.append(
                        {
                            'key': f'project:{e.title}',
                            'title': e.title[:120],
                            'category': 'project',
                            'source': 'project-file',
                            'kind': 'entry',
                            'updated': e.updated or '',
                            'file': e.file,
                        }
                    )
        except Exception:
            pass
    for r in rows[:lim]:
        title = str(r.get('title') or '').strip()
        value = str(r.get('factValue') or '')
        if not title:
            # factValue is stored JSON-encoded: unwrap {"fact","details"}
            # dicts and plain strings alike; fall back to the first line.
            try:
                obj = _json.loads(value)
                if isinstance(obj, dict) and isinstance(obj.get('fact'), str):
                    title = str(obj.get('fact'))
                elif isinstance(obj, str):
                    title = obj
            except Exception:
                pass
            if not title:
                title = value.split('\n', 1)[0][:80]
        facts.append(
            {
                'key': r.get('factKey'),
                'title': title[:120],
                'description': str(r.get('description') or '')[:200],
                'category': r.get('category') or '',
                'source': r.get('source') or '',
                'kind': r.get('kind') or '',
                'status': r.get('status') or '',
                'updated': r.get('updatedAt') or '',
            }
        )
    # The page's own arithmetic is what tells the model whether it has now seen
    # the whole store.
    storeRows = len(rows)
    return _json.dumps(
        {
            'ok': True,
            'count': len(facts),
            'total': total + projectCount,
            'storeTotal': total,
            'projectTotal': projectCount,
            'offset': off,
            'hasMore': bool(storeRows and off + storeRows < total),
            'includeRetired': includeRetired,
            'facts': facts,
        }
    )


def _purge_session_everywhere(sessionId: str) -> dict[str, object]:
    """Remove a session from workbench storage + brain SQLite (cascade children)."""
    from app.services import memory_store

    wb_ok = False
    try:
        from app.services.workbench.sessions import delete_workbench_session

        wb_ok = bool(delete_workbench_session(sessionId))
    except Exception:
        pass
    # Free the headless browser for this session — every session that used a
    # browser tool otherwise leaves a chromium process resident until app
    # shutdown.
    try:
        from app.services.browser.session_manager import closeSession

        asyncio.run(closeSession(sessionId))
    except Exception:
        pass
    result = memory_store.delete_session_cascade(sessionId, notify=not wb_ok)
    return {
        'ok': wb_ok or bool(result.get('ok')),
        'messages': as_int(result.get('messages'), 0),
        'children': result.get('children') or {},
        'workbench': wb_ok,
    }


async def _deleteSession(sessionId: str) -> str:
    """Delete a chat session and all dependent rows."""
    try:
        # SQLite cascades are synchronous — keep them off the event loop so
        # another chat can continue streaming while a large session deletes.
        result = await asyncio.to_thread(_purge_session_everywhere, sessionId)
        if result.get('ok'):
            children = result.get('children') or {}
            msgCount = as_int(result.get('messages'), 0)
            extra = (
                sum(int(v) for k, v in children.items() if k != 'messages')
                if isinstance(children, dict)
                else 0
            )
            extra_note = f', {extra} other related row(s)' if extra else ''
            return f'Deleted session {sessionId} (+ {msgCount} message(s){extra_note}).'
        return f'Session {sessionId} not found — it may have already been deleted.'
    except Exception as exc:
        return f'Error deleting session {sessionId}: {exc}'


_SESSION_CONTEXT_MAX_CHARS = 6000

# ── session_search (discovery / scroll / browse over the messages FTS index) ──
# Every bound below is a read bound, not a preference: the messages table is
# the whole transcript history of an install, so an unbounded browse would hand
# a model (and a tool-result pipeline with a 64 KB cap) a multi-megabyte blob.
# The SQL LIMIT is always emitted from an int() of these, never from a model
# string, and the JSON is assembled with json.dumps — never an f-string over
# message text, which is how a brace in a transcript escapes into structure.
_SESSION_SEARCH_MAX_LIMIT = 50
_SESSION_SEARCH_MAX_SNIPPET = 240
_SESSION_SEARCH_DEFAULT_LIMIT = 20
_SESSION_SEARCH_MAX_WINDOW = 200
# Browse pages the MERGED session list (workbench blobs ∪ sessions-table
# fallback). The fallback SQL reads the newest ``offset + limit`` rows rather
# than a same-offset window, because the fallback set is disjoint from the
# workbench list and a page of the merged list can therefore contain fallback
# rows from the TOP of their own order (500 workbench rows means global page 11
# starts at fallback row 1, not row 500).
_SESSION_SEARCH_MAX_BROWSE_LIMIT = 200
_SESSION_SEARCH_DEFAULT_BROWSE_LIMIT = 50


def _sessionSearchCoerce(args: dict[str, object], names: tuple[str, ...]) -> dict[str, object]:
    """Narrow the wire arguments a session_search shape touches.

    ``dispatch`` calls ``handler(**args)``, so a model that passes
    ``sessionId=42`` would otherwise reach ``str.replace`` / ``f-string``
    interpolation as an int and surface ``'int' object has no attribute
    'replace'`` — the same leak ``_brainQuery`` closes by coercing before it
    touches anything. Only the names a shape reads are copied, so an argument
    belonging to another shape never reaches this one.
    """
    from app.json_narrowing import as_int, as_str

    out: dict[str, object] = {}
    for name in names:
        if name not in args:
            continue
        value = args.get(name)
        if name in ('limit', 'offset', 'beforeId', 'afterId'):
            out[name] = as_int(value, 0)
        else:
            out[name] = as_str(value)
    return out


def _jsonl_rows(path: Path) -> list[dict[str, object]]:
    from app.services.memory_store import tables

    return tables.ledger_rows(path)


def _transcript_message_rows(session_cap: int = 400) -> list[dict[str, object]]:
    """Every transcript message row (snake_case), newest-modified file first.

    The transcripts are ``transcripts/<sessionId>.jsonl`` (one line per
    message); reads are bounded the same way ``brain_query(store=messages)``
    bounds them — at most ``session_cap`` transcript files.
    """
    from app.lib.paths import dataPath

    directory = dataPath('transcripts')
    rows: list[dict[str, object]] = []
    if not directory.is_dir():
        return rows
    files = sorted(
        directory.glob('*.jsonl'),
        key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
        reverse=True,
    )[:session_cap]
    for path in files:
        sid = path.stem
        for raw in _jsonl_rows(path):
            row = dict(raw)
            if not str(row.get('session_id') or ''):
                row['session_id'] = sid
            rows.append(row)
    return rows


def _session_title_by_id() -> dict[str, str]:
    """Session id → title, from the sessions store (the LEFT JOIN stand-in —
    a message whose session row is gone still has transcript text worth
    finding, so a missing title just reads '')."""
    titles: dict[str, str] = {}
    try:
        from app.services.memory_store import list_sessions

        for session in list_sessions() or []:
            sid = str(
                getattr(session, 'id', '')
                or (session.get('id') if isinstance(session, dict) else '')
                or ''
            )
            if not sid:
                continue
            title = str(
                getattr(session, 'title', '')
                or (session.get('title') if isinstance(session, dict) else '')
                or ''
            )
            titles[sid] = title
    except Exception:
        pass
    return titles


def _search_terms(query: str) -> list[str]:
    """The searchable terms of a free-text query (the FTS tokenizer's
    vocabulary — whitespace/symbol split). Empty when the query has no
    searchable tokens."""
    return [t for t in re.split(r'[^\w]+', query or '') if t.replace('"', '')]


def _sessionSearchRows(
    session_ids: set[str] | None,
    role: str,
    before_id: int,
    after_id: int,
    terms: list[str],
    limit: int,
    newest_first: bool = True,
) -> list[dict[str, object]]:
    """One bounded scan of the transcript rows.

    The FTS index is gone with the database; the matcher is a case-insensitive
    term match over the content text (the same vocabulary
    ``kv._fts_match_query`` tokenizes), and the session title rides in from
    the sessions store the way the SQL LEFT JOIN carried it. The response
    keeps the same wire shape ``brain_query(store=messages)`` returns.
    """
    from app.services.memory_store.kv import fts_match

    titles = _session_title_by_id()
    rows = _transcript_message_rows()
    out: list[dict[str, object]] = []
    for row in rows:
        sid = str(row.get('session_id') or '')
        if session_ids is not None and sid not in session_ids:
            continue
        if role and str(row.get('role') or '') != role:
            continue
        mid = as_int(row.get('id'), 0)
        if before_id and (not mid or mid >= before_id):
            continue
        if after_id and (not mid or mid <= after_id):
            continue
        content_text = str(row.get('content') or '')
        try:
            decoded = json.loads(content_text)
            if isinstance(decoded, str):
                content_text = decoded
        except (TypeError, ValueError):
            pass
        if terms and not fts_match(content_text, ' '.join(terms)):
            continue
        out.append(
            {
                'id': mid,
                'session_id': sid,
                'role': str(row.get('role') or ''),
                'content': content_text,
                'created_at': str(row.get('created_at') or ''),
                'session_title': titles.get(sid, ''),
            }
        )
    out.sort(key=lambda r: as_int(r.get('id'), 0), reverse=newest_first)
    return out[: max(1, int(limit))]


def _sessionSearchMessage(row: dict[str, object], full: bool = True) -> dict[str, object]:
    """One message as the wire shape, with a bounded content preview.

    The preview is a plain prefix rather than ``snippet()``: the FTS5 snippet
    helper marks matches with caller-supplied delimiters, and a transcript that
    already contains those characters would render as malformed markup. A
    prefix is honest about what it is — the head of the message, not a
    relevance-centred excerpt.

    ``full=False`` (discovery) drops the body down to the preview: a hit list
    that carried every matching message in full would be bounded by nothing but
    the 64 KB tool-result cap, and the model only needs enough of each hit to
    decide which session to scroll into. ``full=True`` (scroll) is the read.
    """
    wire = dict(row)
    try:
        content = json.loads(str(wire.get('content') or ''))
        if isinstance(content, str):
            wire['content'] = content
    except (TypeError, ValueError):
        pass
    text = str(wire.get('content') or '')
    wire['preview'] = ' '.join(text.split())[:_SESSION_SEARCH_MAX_SNIPPET]
    # snake→camel, the same rule every other brain read applies (wire._row_as_wire),
    # so the model sees one naming convention across brain_query, list_facts and
    # this tool instead of a per-tool exception.
    wire['sessionId'] = str(wire.pop('session_id', '') or '')
    wire['sessionTitle'] = str(wire.pop('session_title', '') or '')
    wire['createdAt'] = str(wire.pop('created_at', '') or '')
    if not full:
        wire.pop('content', None)
    return wire


def _sessionSearchPreview(
    rows: list[dict[str, object]], full: bool = True
) -> list[dict[str, object]]:
    return [_sessionSearchMessage(r, full=full) for r in rows]


async def _sessionSearch(mode: str = '', **args: object) -> str:
    """Search past conversation messages; move inside one session; list chats.

    Three shapes on ONE door, so the model never has to guess which tool holds
    the transcript index:

    * discovery — ``mode='search'`` with a ``query``: FTS5 matches over the
      ``messages`` table, each hit carrying its ``sessionId``. Returns matching
      MESSAGES, not one row per session, because a session that matched on one
      line is not a session that matched everywhere and the scroll shape is how
      the model widens the window.
    * scroll — ``mode='scroll'`` with ``sessionId`` and optional ``beforeId`` /
      ``afterId``: moves backwards or forwards through ONE session by message
      id. ``beforeId`` walks back (older), ``afterId`` walks forward (newer);
      both is a contradiction and is refused rather than silently resolved.
    * browse — ``mode='browse'``: one page of the session list itself, newest
      first (``limit``/``offset``, ``hasMore``/``total``).

    Read-only by construction: every path is a SELECT over the one brain
    database, and nothing here writes, spawns or indexes. A session-scoped
    scroll narrows to that session's own rows — the transcript index has no
    per-user ownership axis, so the session id IS the boundary.
    """
    import json as _json

    from app.json_narrowing import as_int, as_str

    mode = as_str(mode).strip().lower() or 'search'
    try:
        if mode == 'browse':
            wanted = _sessionSearchCoerce(args, ('limit', 'offset'))
            return await asyncio.to_thread(
                _sessionSearchBrowse,
                as_int(wanted.get('limit'), _SESSION_SEARCH_DEFAULT_BROWSE_LIMIT),
                as_int(wanted.get('offset'), 0),
            )
        if mode == 'scroll':
            wanted = _sessionSearchCoerce(args, ('sessionId', 'beforeId', 'afterId', 'limit'))
            sid = as_str(wanted.get('sessionId'))
            if not sid:
                return _json.dumps(
                    {
                        'error': (
                            'session_search(scroll) requires sessionId — take it from a '
                            "discovery hit, or call session_search(mode='browse') to list sessions."
                        )
                    }
                )
            before_id = as_int(wanted.get('beforeId'), 0)
            after_id = as_int(wanted.get('afterId'), 0)
            if before_id and after_id:
                return _json.dumps(
                    {
                        'error': (
                            'session_search(scroll): pass beforeId OR afterId, not both — '
                            'one walks back through the session, the other forward.'
                        )
                    }
                )
            return await asyncio.to_thread(_sessionSearchScroll, sid, before_id, after_id)
        if mode == 'search':
            wanted = _sessionSearchCoerce(
                args, ('query', 'sessionId', 'role', 'limit', 'beforeId', 'afterId')
            )
            query = as_str(wanted.get('query'))
            if not query:
                return _json.dumps(
                    {
                        'error': (
                            'session_search requires a query for discovery. '
                            "Use mode='browse' to list sessions, or mode='scroll' to move "
                            'inside one you already have.'
                        )
                    }
                )
            return await asyncio.to_thread(
                _sessionSearchDiscovery,
                query,
                as_str(wanted.get('sessionId')),
                as_str(wanted.get('role')),
                as_int(wanted.get('limit'), _SESSION_SEARCH_DEFAULT_LIMIT),
                as_int(wanted.get('beforeId'), 0),
                as_int(wanted.get('afterId'), 0),
            )
        return _json.dumps(
            {
                'error': (
                    f"unknown session_search mode {mode!r} — expected 'search' (discovery "
                    "with a query), 'scroll' (move inside one sessionId) or 'browse' (list sessions)."
                )
            }
        )
    except Exception as exc:
        return _json.dumps({'error': f'session_search({mode}): {exc}'})


def _sessionSearchDiscovery(
    query: str,
    session_id: str,
    role: str,
    limit: int,
    before_id: int,
    after_id: int,
) -> str:
    """Discovery shape: term matches over transcript messages, narrowed by session/role."""
    import json as _json

    lim = max(1, min(limit, _SESSION_SEARCH_MAX_LIMIT))
    terms = _search_terms(query)
    if not terms:
        return _json.dumps(
            {
                'mode': 'search',
                'query': query,
                'messages': [],
                'note': 'query has no searchable tokens (punctuation/short words only).',
            }
        )
    rows = _sessionSearchRows(
        {session_id} if session_id else None,
        role,
        before_id,
        after_id,
        terms,
        lim,
        newest_first=True,
    )
    messages = _sessionSearchPreview(rows, full=False)
    return _json.dumps(
        {
            'mode': 'search',
            'query': query,
            'sessionId': session_id or '',
            'role': role or '',
            'count': len(messages),
            'messages': messages,
            'note': (
                'matching MESSAGES, newest first — each hit carries its sessionId. '
                'Pass sessionId + beforeId to scroll further back in one session, '
                "or sessionId + afterId to move forward."
            ),
        },
        default=str,
        ensure_ascii=False,
    )


def _sessionSearchScroll(session_id: str, before_id: int, after_id: int) -> str:
    """Scroll shape: a bounded window of ONE session, by message id."""
    import json as _json

    if after_id:
        rows = _sessionSearchRows(
            {session_id}, '', before_id, after_id, [], _SESSION_SEARCH_MAX_WINDOW, newest_first=False
        )
        messages = _sessionSearchPreview(rows)
        endId = messages[-1]['id'] if messages else 0
        return _json.dumps(
            {
                'mode': 'scroll',
                'sessionId': session_id,
                'direction': 'forward',
                'fromId': after_id,
                'count': len(messages),
                'messages': messages,
                'hasMore': len(messages) >= _SESSION_SEARCH_MAX_WINDOW,
                'nextAfterId': endId or None,
                'note': 'older messages are behind you: pass beforeId to walk back.',
            },
            default=str,
            ensure_ascii=False,
        )
    rows = _sessionSearchRows(
        {session_id}, '', before_id or 0, 0, [], _SESSION_SEARCH_MAX_WINDOW, newest_first=True
    )
    messages = _sessionSearchPreview(rows)
    # DESC is the fetch order for "walk back"; the transcript reads forward.
    messages.reverse()
    firstId = messages[0]['id'] if messages else 0
    return _json.dumps(
        {
            'mode': 'scroll',
            'sessionId': session_id,
            'direction': 'back',
            'fromId': before_id or None,
            'count': len(messages),
            'messages': messages,
            'hasMore': len(messages) >= _SESSION_SEARCH_MAX_WINDOW,
            'nextBeforeId': firstId or None,
            'note': (
                'newest-first window reversed for reading. Pass nextBeforeId to keep '
                "walking back, or afterId to walk forward from the newest message."
            ),
        },
        default=str,
        ensure_ascii=False,
    )


def _message_counts() -> dict[str, int]:
    """Per-session transcript line counts (the COUNT-grouped query's stand-in)."""
    from app.lib.paths import dataPath

    counts: dict[str, int] = {}
    directory = dataPath('transcripts')
    if not directory.is_dir():
        return counts
    for path in directory.glob('*.jsonl'):
        counts[path.stem] = len(_jsonl_rows(path))
    return counts


def _sessionSearchBrowse(
    limit: int = _SESSION_SEARCH_DEFAULT_BROWSE_LIMIT,
    offset: int = 0,
) -> str:
    """Browse shape: one bounded page of the session list, newest first.

    Two sources, because the sidebar's own list is only half the truth:
    ``list_workbench_sessions`` reads the workbench BLOB, and a session whose
    blob is absent (a row written by the message path alone, or a blob lost to a
    pre-T18 install) still has messages worth finding — omitting it would make
    this door report a session as nonexistent while discovery returns its
    messages. The sessions-table rows are the fallback set, keyed by id so a
    session present in both is reported once.

    Message counts come from the ``messages`` table (COUNT per session, one
    grouped query) rather than the ``sessions.message_count`` column, which the
    workbench writes from its in-memory copy and which a row written through
    the message path never updates.

    Paged, never a dump: ``limit``/``offset`` bound the page and the response
    carries ``total``/``hasMore`` like ``list_facts``, so the model can walk the
    whole list. The fallback SQL reads the newest ``offset + limit`` rows rather
    than a same-offset window — the fallback set is disjoint from the workbench
    list, so a page of the MERGED list can contain fallback rows from the TOP of
    their own order. A source that fails is reported in ``degraded``/``errors``
    and the list is explicitly NOT claimed complete.
    """
    import json as _json

    from app.services.workbench import workbench as _wb

    lim = max(1, min(as_int(limit, _SESSION_SEARCH_DEFAULT_BROWSE_LIMIT),
                     _SESSION_SEARCH_MAX_BROWSE_LIMIT))
    off = max(0, as_int(offset, 0))
    items: dict[str, dict[str, object]] = {}
    errors: list[str] = []
    try:
        for item in _wb.listWorkbenchSessions():
            if not isinstance(item, dict):
                continue
            sid = str(item.get('id') or '')
            if sid:
                items[sid] = {
                    'sessionId': sid,
                    'title': str(item.get('title') or '')[:120],
                    'messageCount': item.get('messageCount') or 0,
                    'updatedAt': item.get('updatedAt') or item.get('startedAt') or '',
                    'isArchived': bool(item.get('isArchived')),
                }
    except Exception as exc:
        errors.append(f'workbench sessions: {type(exc).__name__}: {exc}')
    try:
        from app.services.memory_store import list_sessions

        # Message counts come from the transcript files themselves (one line
        # count per session) rather than the stored ``message_count``, which
        # the workbench writes from its in-memory copy and which a row written
        # through the message path never updates.
        counts = _message_counts()
        for session in list_sessions() or []:
            sid = str(
                getattr(session, 'id', '')
                or (session.get('id') if isinstance(session, dict) else '')
                or ''
            )
            if not sid or sid in items:
                continue
            title = str(
                getattr(session, 'title', '')
                or (session.get('title') if isinstance(session, dict) else '')
                or ''
            )
            updated = str(
                getattr(session, 'updatedAt', '')
                or (session.get('updatedAt') if isinstance(session, dict) else '')
                or ''
            )
            started = str(
                getattr(session, 'startedAt', '')
                or (session.get('startedAt') if isinstance(session, dict) else '')
                or ''
            )
            archived = bool(
                getattr(session, 'isArchived', None)
                or (session.get('isArchived') if isinstance(session, dict) else False)
            )
            items[sid] = {
                'sessionId': sid,
                'title': title[:120],
                'messageCount': counts.get(sid, 0),
                'updatedAt': updated or started,
                'isArchived': archived,
            }
    except Exception as exc:
        errors.append(f'sessions store: {type(exc).__name__}: {exc}')
    sessions = sorted(
        items.values(),
        key=lambda x: str(x.get('updatedAt') or ''),
        reverse=True,
    )
    total = len(sessions)
    page = sessions[off : off + lim]
    note = (
        'newest first. Pass one of these sessionId values to '
        "session_search(mode='scroll') to read it, or to "
        "session_search(mode='search', sessionId=…) to narrow a discovery query."
    )
    if errors:
        note += (
            ' INCOMPLETE — a source failed (' + '; '.join(errors) + '), so this is '
            'NOT the full session list and `total`/`hasMore` are unknown.'
        )
    return _json.dumps(
        {
            'mode': 'browse',
            'count': len(page),
            'total': None if errors else total,
            'offset': off,
            'limit': lim,
            'hasMore': True if errors else (off + len(page) < total),
            'degraded': bool(errors),
            'errors': errors,
            'sessions': page,
            'note': note,
        },
        default=str,
        ensure_ascii=False,
    )


def _contextCapsule(blob: dict[str, object], maxChars: int, focus: str = '') -> str:
    """Render a past session as a compact handoff capsule (title, metadata,
    plan, and one trimmed line per dialogue turn — never a raw dump).

    ``focus`` (the tool's ``query``) selects relevance: word hits trim the
    digest to matching turns plus the turn before each (context), like this
    environment's focused session-context reads; empty = full digest."""
    import re as _re

    title = str(blob.get('title') or '(untitled)')
    sid = str(blob.get('id') or '')
    updated = str(blob.get('updatedAt') or blob.get('startedAt') or '')
    messages = blob.get('messages')
    if not isinstance(messages, list):
        messages = []
    lines: list[str] = [
        f'SESSION {sid} — "{title}"' + (f' · last updated {updated[:19]}' if updated else ''),
    ]
    plan = blob.get('plan')
    if isinstance(plan, dict):
        steps = plan.get('steps')
        if isinstance(steps, list) and steps:
            lines.append(f'Plan: {len(steps)} step(s) — ' + '; '.join(str(s)[:80] for s in steps[:6]))
    lines.append('')

    turnCount = 0
    turns: list[tuple[str, str]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get('role') or '')
        if role not in ('user', 'assistant'):
            continue
        content = str(msg.get('content') or '').strip()
        if not content:
            continue
        turnCount += 1
        one = ' '.join(content.split())
        if len(one) > 260:
            one = one[:257] + '…'
        prefix = 'USER' if role == 'user' else 'AUG'
        turns.append((prefix, one))

    focusWords = [w.lower() for w in _re.findall(r'\w{3,}', focus or '')]
    if focusWords and turns:
        scored = {
            i for i, (_p, one) in enumerate(turns)
            if any(w in one.lower() for w in focusWords)
        }
        if scored:
            keep: set[int] = set()
            for i in scored:
                keep.add(i)
                if i:
                    keep.add(i - 1)  # one preceding turn for context
            lines.append(f'focused digest: {len(scored)} of {turnCount} turn(s) match {focus.strip()!r}')
            turns = [t for i, t in enumerate(turns) if i in keep]
    for prefix, one in turns:
        lines.append(f'{prefix}: {one}')

    text = '\n'.join(lines)
    if not turnCount:
        text += '\n(no dialogue turns recorded)'
    else:
        text += f'\n({turnCount} turn(s))'
    if len(text) > maxChars:
        text = text[: maxChars - 1].rsplit('\n', 1)[0] + '\n…(truncated)'
    return text


async def _sessionContext(sessionId: str = '', query: str = '') -> str:
    """Handoff capsule for a past session — what was asked, decided, changed."""
    from app.services.memory_store.sessions import get_workbench_blob
    from app.services.workbench import workbench as _wb

    sid = (sessionId or '').strip()
    if not sid:
        return (
            'Error: sessionId is required. List candidates first with '
            "brain_query(store='sessions', query='<topic>') and pick the id."
        )
    blob: dict[str, object] | None = None
    try:
        live = _wb.getWorkbenchSession(sid)
    except Exception:
        live = None
    if live is not None:
        # The in-memory session is the freshest copy (a session being
        # chatted in may not be persisted yet).
        blob = {
            'id': getattr(live, 'id', sid) or sid,
            'title': getattr(live, 'title', '') or '',
            'plan': getattr(live, 'plan', None),
            'messages': list(getattr(live, 'messages', None) or []),
        }
    if not blob:
        # Pruned from the in-memory map — read the SQLite snapshot.
        try:
            blob = await asyncio.to_thread(get_workbench_blob, sid)
        except Exception as exc:
            return f'Error reading session {sid}: {exc}'
    if not blob:
        return f'Session {sid} not found. Check the id via brain_query(store=sessions).'
    return _contextCapsule(blob, _SESSION_CONTEXT_MAX_CHARS, focus=query)


async def _deleteSessions(sessionIds: object = None, sessionId: str = '') -> str:
    """Bulk-delete chat sessions. Prefer over many delete_session calls."""
    from app.services.tool_registrations.bulk_helpers import coerce_str_list, format_bulk_report

    ids = coerce_str_list(sessionIds, single=sessionId)
    if not ids:
        return 'Error: sessionIds is required (array of session IDs to delete).'
    deleted: list[str] = []
    missing: list[str] = []
    errors: list[str] = []
    msg_total = 0
    for sid in ids:
        try:
            result = await asyncio.to_thread(_purge_session_everywhere, sid)
            if result.get('ok'):
                deleted.append(sid)
                msg_total += as_int(result.get('messages'), 0)
            else:
                missing.append(sid)
        except Exception as exc:
            errors.append(f'{sid}: {exc}')
    return format_bulk_report(
        label='delete_sessions',
        total=len(ids),
        ok_ids=deleted,
        missing=missing,
        errors=errors,
        extra=f'(+ {msg_total} message(s))',
    )


async def _renameSession(sessionId: str = '', title: str = '') -> str:
    """Rename a chat session so the sidebar shows a clear human title."""
    from app.services.workbench.context import currentSessionId
    from app.services.workbench.sessions import get_workbench_session, rename_workbench_session

    sid = (sessionId or '').strip()
    new_title = (title or '').strip()
    if not new_title:
        return 'Error: title is required.'
    if not sid:
        # Prefer the session that is currently executing tools.
        ctx = currentSessionId.get()
        if ctx and ctx != 'default':
            sid = ctx
    if not sid:
        try:
            from app.services.workbench import workbench as wb

            sessions = wb.listWorkbenchSessions()
            if sessions:
                sid = str(sessions[0].get('id') or '')
        except Exception:
            pass
    if not sid:
        return 'Error: sessionId is required (no active session found).'
    session = rename_workbench_session(sid, new_title)
    if not session:
        if not get_workbench_session(sid):
            return f'Session {sid} not found.'
        return f'Could not rename session {sid}.'
    return f'Renamed session {sid} → "{session.title}".'


async def _deleteFolder(folderId: str) -> str:
    """Delete all sessions in a folder and their dependent rows."""
    from app.services import memory_store

    try:
        sessions = memory_store.list_sessions()
        folderSessions = [s for s in sessions if s.get('folderId') == folderId]
        if not folderSessions:
            return f"No sessions found in folder '{folderId}'."
        count = 0
        msgCount = 0
        for s in folderSessions:
            sid = s['id']
            result = await asyncio.to_thread(_purge_session_everywhere, sid)
            if result.get('ok'):
                count += 1
                msgCount += as_int(result.get('messages'), 0)
        return f"Deleted {count} session(s) from folder '{folderId}' (+ {msgCount} message(s))."
    except Exception as exc:
        return f"Error deleting folder '{folderId}': {exc}"


def register() -> None:
    """Register session-management and search tools."""
    tool_registry.register(
        'session_context',
        'Read a condensed context capsule for a PAST chat session — its title, plan, '
        'and a one-line digest of every turn. Use to recall what a previous '
        'conversation covered before continuing its work. sessionId is required '
        "(list candidates via brain_query(store='sessions')). Read-only; costs one "
        'call regardless of session length.',
        _sessionContext,
        {
            'type': 'object',
            'properties': {
                'sessionId': {
                    'type': 'string',
                    'description': 'The session to read (e.g. wb_20260715_143052_a1b2c3).',
                },
                'query': {
                    'type': 'string',
                    'description': 'Optional focus hint — trims the digest to turns matching it '
                    '(plus one turn of context each); empty returns the full one-line-per-turn digest.',
                },
            },
            'required': ['sessionId'],
        },
    )
    tool_registry.register(
        'session_search',
        'Search past conversation MESSAGES across every session, then move inside one. '
        "Three shapes on one tool: mode='search' (discovery) runs an FTS5 full-text query over the "
        'messages store and returns matching messages, each carrying its sessionId — use it to find '
        "what was discussed before; mode='scroll' walks one session by message id (sessionId plus "
        'beforeId to go back, afterId to go forward — never both); mode=\'browse\' lists sessions '
        'newest first, paged (limit/offset, hasMore). Prefer this over brain_query(store=\'messages\') '
        'when you are looking for conversational history rather than a row listing. Read-only; costs one '
        'call regardless of how much history it searches.',
        _sessionSearch,
        {
            'type': 'object',
            'properties': {
                'mode': {
                    'type': 'string',
                    'enum': ['search', 'scroll', 'browse'],
                    'description': "Which shape: 'search' (discovery, needs query), 'scroll' (needs "
                    "sessionId and one of beforeId/afterId), 'browse' (list sessions, newest first, "
                    "paged). Default 'search'.",
                },
                'query': {
                    'type': 'string',
                    'description': 'Discovery text to find in past messages (mode=search). Required there; '
                    'ignored by the other shapes.',
                },
                'sessionId': {
                    'type': 'string',
                    'description': 'Session to scroll inside (mode=scroll), or to narrow a discovery '
                    'query to (mode=search). Take it from a discovery hit or mode=browse.',
                },
                'role': {
                    'type': 'string',
                    'enum': ['user', 'assistant', 'system', 'tool'],
                    'description': 'Optional discovery filter: only messages from this role.',
                },
                'beforeId': {
                    'type': 'integer',
                    'description': 'Scroll/back-paging cursor: return messages with id < beforeId '
                    '(older). Discovery accepts it too. Mutually exclusive with afterId.',
                },
                'afterId': {
                    'type': 'integer',
                    'description': 'Forward-paging cursor: return messages with id > afterId (newer). '
                    'Mutually exclusive with beforeId.',
                },
                'limit': {
                    'type': 'integer',
                    'description': 'Discovery hits to return (1-50, default 20) or browse page size '
                    '(1-200, default 50). Scroll windows are bounded server-side and ignore this.',
                },
                'offset': {
                    'type': 'integer',
                    'description': 'Browse paging cursor: sessions to skip (default 0). Page with '
                    '`limit` and stop when `hasMore` is false; `total` is the full session count.',
                },
            },
        },
        keywords=['session', 'search', 'history', 'messages', 'conversation', 'transcript', 'recall', 'scroll', 'browse'],
    )
    tool_registry.register(
        'rename_session',
        'Rename a chat session in the sidebar when the user asks. Titles auto-generate after the first '
        'reply — do NOT call this just to title a new chat. Use a short 3–8 word title. Pass sessionId '
        'when known; for the current chat (see <session> in the system prompt) you may omit it.',
        _renameSession,
        {
            'type': 'object',
            'properties': {
                'sessionId': {
                    'type': 'string',
                    'description': 'Workbench session id (e.g. wb_20260715_143052_a1b2c3). Optional for the active chat.',
                },
                'title': {
                    'type': 'string',
                    'description': 'Short sidebar title (max ~48 chars).',
                },
            },
            'required': ['title'],
        },
    )
    tool_registry.register(
        'delete_session',
        'Delete a single chat session by ID (e.g. wb_20260715_143052_a1b2c3; the current chat id is in '
        'the <session> block). For multiple, use delete_sessions instead of repeating this. Use '
        'brain_query(store=sessions) to list first. IMPORTANT: confirm with the user before deleting.',
        _deleteSession,
        {
            'type': 'object',
            'properties': {'sessionId': {'type': 'string', 'description': 'The session ID to delete.'}},
            'required': ['sessionId'],
        },
    )
    tool_registry.register(
        'delete_sessions',
        'Bulk-delete multiple chat sessions in one call. Pass sessionIds as an array (e.g. from '
        'brain_query(store=sessions)); prefer over many delete_session calls. IMPORTANT: list the exact '
        'sessions to the user and wait for explicit confirmation — never bulk-delete without it.',
        _deleteSessions,
        {
            'type': 'object',
            'properties': {
                'sessionIds': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Session IDs to delete (e.g. ["wb_…", "wb_…"]).',
                },
                'sessionId': {
                    'type': 'string',
                    'description': 'Optional single ID fallback if sessionIds is omitted.',
                },
            },
            'required': ['sessionIds'],
        },
    )
    tool_registry.register(
        'search',
        'Unified search across files and web with dedup — one call, scope decides. '
        'Prefer this over juggling search_files vs web_search.',
        _search,
        {
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': 'Search query.'},
                'scope': {
                    'type': 'string',
                    'description': 'Comma-separated scopes: files,web (e.g. "files,web"). Default files.',
                },
                'limit': {'type': 'integer', 'description': 'Max results (1-30). Default 10.'},
            },
            'required': ['query'],
        },
    )
    tool_registry.register(
        'brain_query',
        "Read-only query over runtime stores (sessions, messages, blackboard, daemons, "
        'facts, memory, project-memory). Returns compact JSON rows. Use store=sessions to list chats '
        "before deleting; store=project-memory reads this workspace's md-file entries "
        '(BM25-ranked by query); store=memory reads the key-value notes table.',
        _brainQuery,
        {
            'type': 'object',
            'properties': {
                'store': {
                    'type': 'string',
                    'description': 'Which store to read: sessions | messages | blackboard | daemons | facts | memory | project-memory',
                    'enum': [
                        'sessions',
                        'messages',
                        'blackboard',
                        'daemons',
                        'facts',
                        'memory',
                        'project-memory',
                    ],
                },
                'query': {'type': 'string', 'description': 'Search text (FTS or LIKE). Optional.'},
                'filters': {
                    'type': 'string',
                    'description': 'JSON object of column filters (e.g. \'{"category": "auth"}\'). Optional.',
                },
                'limit': {'type': 'integer', 'description': 'Max rows to return (1-100). Default 10.'},
            },
            'required': ['store'],
        },
    )
    tool_registry.register(
        'remember',
        'Save one durable memory entry (the only model write door). Use for user-stated '
        'preferences, project constraints, and feedback that must outlive this session. '
        'The record is one markdown file per fact under the app data dir, with MEMORY.md as its '
        'index — a write here is that file write, and the index is in context at session start. '
        'Check a key before adding a second copy: a sentence already stored under another key is '
        'refused with that key, so update it instead. Pass a stable '
        'key to update an existing entry rather than duplicate, and that entry\'s `updated` stamp as '
        '`if_version` so a stale read cannot overwrite a change it never saw. Give every entry a one-line '
        '`description` — it is the recall hook shown in the index. '
        'Sensitive topics are refused unless enabled. Scope: inside a workspace session the default is '
        "project (this workspace's md-file memory); use scope='global' for user-level facts that "
        "apply everywhere, or scope='project' to force the project store. "
        'Before saving, ask: will a future session act better because of this? Runtime reminders, '
        'one-off task details and your own narration are not memories — user-stated identity, '
        'preferences, corrections and constraints are.',
        _remember,
        {
            'type': 'object',
            'properties': {
                'fact': {'type': 'string', 'description': 'The fact to remember (one concise statement).'},
                'title': {
                    'type': 'string',
                    'description': 'Optional short human label (≤60 chars) shown in the Memory UI. '
                    'Derived from the fact text when omitted.',
                },
                'kind': {
                    'type': 'string',
                    'enum': ['fact', 'lesson', 'preference', 'skill-note', 'profile', 'person'],
                    'description': 'Entry type. Default fact. `profile` is who the user IS '
                    '(name, role, expertise, standing constraints) and is always in context '
                    'instead of waiting for a keyword match — use it sparingly and precisely. '
                    '`person` is a durable detail about someone else; it stays out of the '
                    'automatic index unless the user raises that person.',
                },
                'key': {
                    'type': 'string',
                    'description': 'Optional stable key; pass it to update an existing fact instead of creating a new one.',
                },
                'if_version': {
                    'type': 'string',
                    'description': 'Optimistic locking for an update: pass the `updated` stamp or the '
                    'integer `version` list_facts reported for this key. If anything changed that entry '
                    'since you read it, nothing is written and the current text comes back to merge and '
                    'retry. Omit to write unconditionally — correct for a create.',
                },
                'category': {
                    'type': 'string',
                    'enum': ['user', 'feedback', 'project', 'reference', 'general'],
                    'description': 'Memory category. Default general.',
                },
                'scope': {
                    'type': 'string',
                    'enum': ['global', 'project'],
                    'description': 'Where to save: project (workspace md memory, the default inside a '
                    'workspace session) or global (user-level facts store, the default without a workspace).',
                },
                'details': {'type': 'string', 'description': 'Optional extra context stored alongside the fact.'},
                'description': {
                    'type': 'string',
                    'description': 'One-line summary of where this memory applies — the recall hook: '
                    'shown in the boot index and used for relevance at recall (project scope: file '
                    'frontmatter + MEMORY-INDEX.md index line; global scope: stored on the fact row). '
                    'Derived from the fact when omitted.',
                },
                'expires_at': {
                    'type': 'string',
                    'description': 'Optional ISO-8601 expiry (e.g. 2026-12-31T00:00:00Z); purged after this.',
                },
                'aliases': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Optional alternative names or phrasings a future message may use '
                    'for this fact (≤8, 60 chars each). They join the deterministic alias pre-read.',
                },
            },
            'required': ['fact'],
        },
    )
    tool_registry.register(
        'list_facts',
        'List durable long-term memory entries (key, title, category, source, updated). This is the '
        'FULL store, not a sample: page with `offset` and watch `hasMore`/`total`. Call it before '
        '`remember` (update, don\'t duplicate — and the write door requires one read per turn) and '
        'before `forget` (to get the exact key). `include_retired` also shows entries consolidation '
        'parked. Optional filters: category (user | feedback | project | reference | general) '
        'and free-text query over keys/values.',
        _list_facts,
        {
            'type': 'object',
            'properties': {
                'category': {
                    'type': 'string',
                    'enum': ['user', 'feedback', 'project', 'reference', 'general'],
                    'description': 'Optional category filter.',
                },
                'query': {'type': 'string', 'description': 'Optional text search over keys/values.'},
                'limit': {'type': 'integer', 'description': 'Max rows per page (1-200). Default 50.'},
                'offset': {
                    'type': 'integer',
                    'description': 'Rows to skip — page through the store when `hasMore` is true.',
                },
                'include_retired': {
                    'type': 'boolean',
                    'description': 'Also list retired entries (stored but no longer recalled). '
                    'Use when auditing what memory actually holds.',
                },
            },
        },
    )
    tool_registry.register(
        'forget',
        'Delete one durable memory fact by its exact key (call list_facts to see keys). Executes '
        'only when the user\'s latest message asks for the deletion — if they did not, ask first '
        'and call forget again only after they confirm. Only '
        'model/user/imported entries can be deleted; system-owned entries are refused. Inside a '
        'workspace, project-memory entries match by title (the key IS the entry title); prefix '
        '"project:" to force the project store.',
        _forget,
        {
            'type': 'object',
            'properties': {
                'key': {'type': 'string', 'description': 'The exact fact key to delete (from list_facts).'},
            },
            'required': ['key'],
        },
    )
    tool_registry.register(
        'delete_folder',
        'Delete all sessions in a folder by folder ID (their messages are deleted too). Use '
        'brain_query(store=sessions) to list sessions and folderId values first. IMPORTANT: present the '
        'exact folder and sessions to the user and wait for explicit confirmation before calling.',
        _deleteFolder,
        {
            'type': 'object',
            'properties': {'folderId': {'type': 'string', 'description': 'The folder ID whose sessions to delete.'}},
            'required': ['folderId'],
        },
    )
