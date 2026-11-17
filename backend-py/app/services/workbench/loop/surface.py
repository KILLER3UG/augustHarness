"""Per-model tool surface: capability profiles, caps and MCP tail (P1#11 split).

Moved from workbench.py: the guard-mode normalizer, the transcript
tool-result cap, the per-model capability profile (full / reduced / bare /
text) with its TTL memo, the tool-result cap resolver, the tool-definition
name extractor, and the real MCP-server tool tails in both wire formats.

Two things deliberately did NOT move, even though only the tool surface
reads them: the fallback window ``DEFAULT_CONTEXT_WINDOW`` (imported from
``app/models/config.py``, the app-wide 1M default) and
``_resolveModelContextWindow``. The two
BUILDER functions (``toolDefinitions`` / ``openaiToolDefinitions``) also
stay in workbench.py for now — they are the workbench's public tool
surface, and moving them belongs to the canonical-tool-spec change, not
this module. Every name here is re-exported under its original name.

One-way import rule: nothing under loop/ imports workbench.py
(enforced by scripts/check-loop-imports.mjs).
"""

from __future__ import annotations

import logging

from app.json_narrowing import as_bool, as_dict, as_int, as_list, as_str
from app.services.workbench.sessions import WorkbenchSession

logger = logging.getLogger('workbench')

# Cap tool results stored in the transcript (SSE already truncates separately).
MAX_TOOL_RESULT_CHARS = 64 * 1024


def normalizeGuardMode(mode: str) -> str:
    """Normalize guard mode to one of: plan, ask, edit, full."""
    lower = mode.strip().lower().replace('_', '-').replace(' ', '-')
    if not lower:
        # Unset is not a typo: the callers that create a session with an
        # explicit 'full' (Bot DMs) mean it.
        return 'full'
    aliases = {
        'plan': 'plan',
        'plan-only': 'plan',
        'plan-mode': 'plan',
        'ask': 'ask',
        'ask-before': 'ask',
        'ask-before-changes': 'ask',
        'edit': 'edit',
        'edit-auto': 'edit',
        'edit-automatically': 'edit',
        'auto': 'edit',
        'full': 'full',
        'full-access': 'full',
        'make-changes': 'full',
    }
    normalized = aliases.get(lower)
    if normalized is None:
        # An unrecognised spelling used to mean FULL ACCESS — the most
        # permissive answer to the least legible input, and silent. Clamp to the
        # most restrictive mode and say what was refused; a guard the operator
        # meant to engage cannot be inferred from a typo.
        logger.warning('guardMode %r is not a known mode — treating it as plan', mode)
        return 'plan'
    return normalized


# Per-model capability profiles (harness adaptation): a weak model gets a
# smaller tool surface and tighter result caps; a strong model keeps the full
# set. Configurable per model in Model settings.
_HEAVY_TOOL_PREFIXES = ('web_', 'browser', 'voice', 'notion', 'slack', 'discord', 'search', 'fetch')
_BARE_TOOL_ALLOW = frozenset(
    {
        # Names MUST match registered tools exactly (see
        # tests::test_bare_tool_allowlist_matches_registry). A stale name here
        # silently vanishes from the bare surface — e.g. the old 'edit_file' /
        # 'list_files' entries left weak models with no editor and no listing.
        'read_file',
        'read_files',
        'list_directory',
        'write_file',
        'edit_lines',
        # run_command is DEMOTED on the bare surface (owner ruling, STOP 1B):
        # the A/B watched a 1.7B spend its rounds fighting PowerShell while
        # the dedicated workspace tools sat unused. It stays reachable through
        # the tool_call bridge for the models that truly need it.
        'read_background_task',
        'kill_background_task',
        'update_state',
        'update_todos',
        'write_scratchpad',
        'diagnose_proxy',
        # Memory CRUD has to arrive as a set. The `<memory_policy>` block tells
        # even the weakest model to "revise an existing fact under the same
        # key" and to "forget one that is wrong" — with only `remember`
        # offered, that instruction is unreachable and the store can only grow.
        # These three are the whole door: write, enumerate keys, retire.
        'remember',
        'list_facts',
        'forget',
        # The disclosure bridges ARE the small model's way to reach everything
        # else — citation tools, web search, skills, exports — without paying
        # for their schemas on every turn. A bare surface without them is a
        # dead end: 16 tools and no way to discover the research workflow.
        'tool_search',
        'tool_describe',
        'tool_call',
    }
)

# Description budget for the small-model (bare) surface: schemas are billed on
# every turn, and a 1.7B model at 8k cannot spend ~270 tokens per tool. The
# trim keeps the FIRST sentence (the operative instruction) and drops property
# descriptions — types, enums and required stay intact, so the wire contract
# is unchanged; only the prose shrinks.
_BARE_DESCRIPTION_CAP = 140


def _trimDescription(text: object, cap: int = _BARE_DESCRIPTION_CAP) -> str:
    """First sentence of a tool/property description, hard-capped."""
    raw = as_str(text, '').strip()
    if not raw:
        return ''
    for cut in ('. ', '.\n'):
        pos = raw.find(cut)
        if 0 < pos < cap:
            return raw[: pos + 1]
    if len(raw) <= cap:
        return raw
    return raw[:cap].rsplit(' ', 1)[0] + '…'


def _trim_tool_def(t: dict[str, object], cap: int = _BARE_DESCRIPTION_CAP) -> dict[str, object]:
    """A copy of one tool definition with prose trimmed for a small surface."""
    fn = as_dict(t.get('function'), {})
    if not fn:
        # Anthropic shape: {'name', 'description', 'input_schema'}
        trimmed = dict(t)
        trimmed['description'] = _trimDescription(t.get('description'), cap)
        schema = as_dict(t.get('input_schema'), {})
        if schema:
            trimmed['input_schema'] = _trim_schema(schema)
        return trimmed
    fn = dict(fn)
    fn['description'] = _trimDescription(fn.get('description'), cap)
    schema = as_dict(fn.get('parameters'), {})
    if schema:
        fn['parameters'] = _trim_schema(schema)
    out = dict(t)
    out['function'] = fn
    return out


def _trim_schema(schema: dict[str, object]) -> dict[str, object]:
    """Schema copy with property DESCRIPTIONS dropped (structure kept)."""
    out = dict(schema)
    props = as_dict(out.get('properties'), {})
    if props:
        slim: dict[str, object] = {}
        for key, spec in props.items():
            if isinstance(spec, dict):
                item = dict(spec)
                item.pop('description', None)
                slim[key] = item
            else:
                slim[key] = spec
        out['properties'] = slim
    return out


def _trim_tools_for_small_surface(
    tools: list[dict[str, object]], cap: int = _BARE_DESCRIPTION_CAP
) -> list[dict[str, object]]:
    """Prose-trimmed copies of ``tools`` for a bare (small local) surface."""
    return [_trim_tool_def(t, cap) for t in tools]


# At a 4k window even the bare allowlist is too much prose and a few
# conveniences: the bulk read, the todo pair, the scratchpad and the proxy
# diagnostic are all reachable through the bridges or covered by siblings
# (read_file, update_state, remember). The research workflow still works —
# the bridges stay, so citation/web/export tools load on demand.
_BARE_MICRO_DROP = frozenset(
    {'read_files', 'submit_todos', 'update_todos', 'write_scratchpad', 'diagnose_proxy'}
)


def _bare_allowlist(context_window: int) -> frozenset[str]:
    """The bare allowlist for a model window; a 4k model gets the micro set."""
    if 0 < context_window <= 4096:
        return frozenset(_BARE_TOOL_ALLOW - _BARE_MICRO_DROP)
    return _BARE_TOOL_ALLOW


def provider_row_by_name(name: str) -> dict[str, object]:
    """The raw provider row for a provider NAME or id, or {} when unknown.

    Sessions carry ``provider`` as a NAME string (``sessions.py``), so code
    that needs the provider's OWN config — the model's context window, its
    tool surface — must resolve the row from the store. Best-effort: an
    unknown name or a failed read returns {} and the caller keeps its
    fallback.
    """
    cleaned = as_str(name, '').strip()
    if not cleaned:
        return {}
    try:
        from app.services import config_service

        store = config_service.getProvidersStore()
        for entry in as_list(store.get('providers'), []):
            row = as_dict(entry, {})
            if str(row.get('name', '')) == cleaned or str(row.get('id', '')) == cleaned:
                return row
    except Exception:
        logger.debug('provider row lookup failed for %s', cleaned, exc_info=True)
    return {}


def _toolDefName(t: dict[str, object]) -> str:
    """Extract a tool definition's name (Anthropic or OpenAI shape)."""
    fn = as_dict(t.get('function'), {})
    return as_str(t.get('name') or fn.get('name'), '')


_capability_profile_cache: dict[tuple[str, str], tuple[float, dict[str, object]]] = {}
_CAPABILITY_PROFILE_TTL_S = 5.0
# Capacity bound for _capability_profile_cache. The key is a (model, provider)
# pair and a session can name a different model every turn, so the key space
# grows with every pair the app has ever carried — and a TTL that is only
# tested on LOOKUP reclaims none of them, which is what made this unbounded.
# 64 pairs covers any realistic providers.json (even a large multi-fleet
# install) many times over, and a value is three scalars, so the ceiling is
# well under a kilobyte.
_CAPABILITY_PROFILE_CACHE_MAX = 64


def _pruneCapabilityProfiles(now: float) -> None:
    """Reclaim the capability-profile memo: expired entries, then capacity.

    Expiry is the real bound — a pair nobody has looked at for 5s can never
    be served again — so sweeping it on WRITE is what makes the TTL mean
    anything. The capacity trim then handles the pairs that are all still live
    (a model-switch burst inside one TTL window), oldest write first: dicts
    iterate in insertion order, so ``next(iter(...))`` is the oldest insert.
    The trim runs before the caller's own entry is stored and stops one below
    the cap, so the insert lands back on the cap and the profile about to be
    returned is never the one evicted.
    """
    expired = [
        k
        for k, v in _capability_profile_cache.items()
        if now - v[0] >= _CAPABILITY_PROFILE_TTL_S
    ]
    for k in expired:
        _capability_profile_cache.pop(k, None)
    while len(_capability_profile_cache) >= _CAPABILITY_PROFILE_CACHE_MAX:
        _capability_profile_cache.pop(next(iter(_capability_profile_cache)))


def _modelCapabilityProfile(session: WorkbenchSession) -> dict[str, object]:
    """Per-model tool profile from the provider config (never raises).

    Memoized on a 5s TTL per (model, provider): the uncached body walked
    getProvidersAsModels() — a full providers.json read + typed rebuild —
    and the tool-surface path hits it 2–3× per turn for the same pair.
    Provider edits apply within 5 seconds, which is fine for a tool
    surface; the config UI reloads the page anyway. The memo is swept on
    write and capped at ``_CAPABILITY_PROFILE_CACHE_MAX`` (see
    ``_pruneCapabilityProfiles``) so it cannot outlive its usefulness or grow
    with every model a session has ever named. Both return paths hand back a
    COPY, so a caller can mutate what it gets without re-poisoning the memo
    for every other session using that (model, provider) pair.
    """
    import time as _time

    modelId = as_str(getattr(session, 'model', '') or '')
    providerName = as_str(getattr(session, 'provider', '') or '')
    if not modelId:
        return {}
    cacheKey = (modelId, providerName)
    cached = _capability_profile_cache.get(cacheKey)
    now = _time.monotonic()
    if cached is not None and now - cached[0] < _CAPABILITY_PROFILE_TTL_S:
        # A copy, not the cached object. Returning the memo's own dict hands
        # every caller a handle on it, so one that writes — and the name is
        # re-exported from `workbench.py`, so a future caller might — silently
        # re-poisons the memo for every session sharing that (model, provider)
        # pair for the next 5s. The docstring used to describe this hazard
        # instead of removing it, in the same change that removed exactly this
        # asymmetry from `load_layered`. A cache whose entries a caller can
        # write through is not a cache. Cheap: three scalars.
        return dict(cached[1])
    profile: dict[str, object] = {}
    try:
        from app.providers.api_format import is_loopback_base_url as _isLoopback
        from app.services import config_service

        for p in config_service.getProvidersAsModels():
            if p.name != providerName and p.id != providerName:
                continue
            for m in p.models:
                if m.id == modelId:
                    profile = {
                        'tool_surface': m.tool_surface or 'full',
                        # Whether the user actually set a surface (vs the
                        # 'full' default) — the ≤16k local rule below must
                        # not override an explicit choice.
                        'tool_surface_explicit': bool(m.tool_surface),
                        'max_tools': int(m.max_tools or 0),
                        'max_tool_result_chars': int(m.max_tool_result_chars or 0),
                        'context_window': int(getattr(m, 'context_window', 0) or 0),
                        'loopback': _isLoopback(getattr(p, 'base_url', '')),
                    }
                    break
            if profile:
                break
    except Exception:
        pass
    _pruneCapabilityProfiles(now)
    _capability_profile_cache[cacheKey] = (now, profile)
    # Copy out for the same reason the hit path copies: the memo now holds this
    # exact object, so returning it would give the caller write-through access
    # to what every other session sees for the next 5 seconds.
    return dict(profile)


def _applyModelCapabilityProfile(
    session: WorkbenchSession, tools: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Filter the tool surface by the session model's capability profile."""
    profile = _modelCapabilityProfile(session)
    surface = as_str(profile.get('tool_surface'), 'full')
    window = as_int(profile.get('context_window'), 0)
    # A small window never carries the full catalog: the full surface is
    # ~48k tokens of definitions alone (A.1 measurement), so a LOCAL model at
    # ≤16k runs the compact lazy-discovery profile (bare core + discovery
    # bridge + skill-pack tools) unless the user set a surface explicitly.
    # Owner ruling, STOP 1B: never promote a 16k window to the full catalog.
    if (
        surface == 'full'
        and not as_bool(profile.get('tool_surface_explicit'), False)
        and as_bool(profile.get('loopback'), False)
        and 0 < window <= 16384
    ):
        surface = 'bare'
    if surface == 'text':
        # Text tool protocol: no native tools are offered (models that
        # ignore `tools` must not be tempted); the model calls tools via
        # `[TOOLCALL] name|json` lines parsed by the turn loop.
        setattr(session, '_text_tool_protocol', True)
        return []
    if surface == 'bare':
        tools = [t for t in tools if _toolDefName(t) in _bare_allowlist(window)]
        # Small models pay for prose on every turn: first-sentence
        # descriptions, no per-property descriptions — tighter on a 4k window.
        tools = _trim_tools_for_small_surface(tools, 90 if 0 < window <= 4096 else _BARE_DESCRIPTION_CAP)
    elif surface == 'reduced':
        tools = [t for t in tools if not _toolDefName(t).startswith(_HEAVY_TOOL_PREFIXES)]
    maxTools = as_int(profile.get('max_tools'), 0)
    if maxTools > 0 and len(tools) > maxTools:
        tools = tools[:maxTools]
    return tools


def _applySessionStableOrder(
    session: WorkbenchSession, tools: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Keep the wire tool array a prefix extension of the previous round's.

    Sorting by name is deterministic for a FIXED tool set, and the cached
    builders rely on that. The set is not fixed: an MCP server that starts
    mid-session or a skill that registers a tool adds a name, and a name sort
    then shifts EVERY later tool — the array stops being a prefix extension and
    the provider's tool-array cache breakpoint is busted for the rest of the
    session. The bare-essential group still leads (a self-heal downgrade must
    remain a prefix), and within each group the order is the order this session
    first offered each name; a new name is appended rather than inserted.
    """
    names = [_toolDefName(t) for t in tools]
    prev = getattr(session, '_wireToolOrder', None)
    prevOrder = prev if isinstance(prev, list) else []
    if prev is not None:
        previousNames = {str(name) for name in prevOrder}
        currentNames = set(names)
        added = sorted(currentNames - previousNames)
        removed = sorted(previousNames - currentNames)
        if added or removed:
            pending = getattr(session, '_pendingToolSurfaceChanges', None)
            pending = pending if isinstance(pending, list) else []
            pending.extend(
                change
                for change in ({'added': added} if added else {}, {'removed': removed} if removed else {})
                if change
            )
            session._pendingToolSurfaceChanges = pending  # type: ignore[attr-defined]
    rank = {str(n): i for i, n in enumerate(prevOrder)}

    def sortKey(pair: tuple[int, str]) -> tuple[int, int, int, str]:
        _idx, name = pair
        bare = 0 if name in _BARE_TOOL_ALLOW else 1
        slot = rank.get(name)
        # Known names keep their slot; new names sort among themselves after
        # the known ones in their group.
        return (bare, 0, slot, '') if slot is not None else (bare, 1, 0, name)

    ordered = [t for _i, t in sorted(enumerate(tools), key=lambda p: sortKey((p[0], names[p[0]])))]
    try:
        setattr(session, '_wireToolOrder', [_toolDefName(t) for t in ordered])
    except Exception:
        logger.debug('session tool-order stamp failed', exc_info=True)
    return ordered


def _finalize_session_tools(
    session: WorkbenchSession, tools: list[dict[str, object]]
) -> list[dict[str, object]]:
    tools = _applyModelCapabilityProfile(session, tools)
    from app.services.harness_mode import (
        filter_planner_tools,
        is_orchestrator_mode,
    )

    if is_orchestrator_mode(session):
        return _applySessionStableOrder(session, filter_planner_tools(tools))
    return _applySessionStableOrder(session, tools)


def _toolResultCap(session: WorkbenchSession) -> int:
    """Per-model tool-result truncation cap.

    Falls back to the harness default — SCALED TO THE MODEL'S WINDOW when the
    profile carries one: a 64 KiB result inside an 8k window is the overflow
    the A.2 reliability run hit (three tool rounds exceeded the whole context).
    The scale is window//4, not //2: the coding A/B on the 1.7B hit "context
    size exceeded" at 16k with six tool rounds of //2-sized results (~2k
    tokens each ≈ 12.5% of the window per round — six rounds alone are 75%).
    A small window gets a proportionally small cap; an explicit profile value
    still wins; large windows floor at the 64 KiB harness default.
    """
    profile = _modelCapabilityProfile(session)
    cap = as_int(profile.get('max_tool_result_chars'), 0)
    if cap > 0:
        return cap
    window = as_int(profile.get('context_window'), 0)
    if window > 0:
        return max(2048, min(MAX_TOOL_RESULT_CHARS, window // 4))
    return MAX_TOOL_RESULT_CHARS


def _mcpToolDefinitionsAnthropic(seen: set[str]) -> list[dict[str, object]]:
    """Real MCP server tools in Anthropic format, deduped against ``seen``."""
    from app.adapters.proxy_tools import openai_to_anthropic_tool_definition
    from app.services.tools.mcp_client import getMcpToolDefinitionsSync

    out: list[dict[str, object]] = []
    for raw in getMcpToolDefinitionsSync():
        t = openai_to_anthropic_tool_definition(raw)
        name = as_str(t.get('name', ''))
        if name and name not in seen:
            seen.add(name)
            out.append(t)
    return out


def _mcpToolDefinitionsOpenai(seen: set[str]) -> list[dict[str, object]]:
    """Real MCP server tools in OpenAI format, deduped against ``seen``."""
    from app.services.tools.mcp_client import getMcpToolDefinitionsSync

    out: list[dict[str, object]] = []
    for raw in getMcpToolDefinitionsSync():
        fn = as_dict(raw.get('function', {})) if raw.get('type') == 'function' else {}
        name = as_str(fn.get('name', ''))
        if name and name not in seen:
            seen.add(name)
            out.append(raw)
    return out
