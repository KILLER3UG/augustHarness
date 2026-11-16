"""Wake-up brief — the "pick up where you left off" note when the user returns.

Phase 4 of the chat-first redesign: the app runs in the background (tray),
and when the user comes back the harness composes ONE short brief from what
it actually holds — recent sessions, open background automations, and one
question to re-engage.

The shell detects the triggers (first app open after a long idle period,
login/unlock, resume from sleep — all surface as an app start or a window
focus) and reports them here. ONE configurable cooldown gates briefs across
every trigger, and at most one brief is generated per backend start, so a
flapping focus connection cannot spam the model.

The brief is composed by the cerebellum fleet model (the same cheap role the
daemons use) from a fact digest; when no model is configured the digest
itself is the brief — never a fabricated "the model said" text.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.json_narrowing import as_bool, as_dict, as_int, as_str
from app.services import config_service
from app.services.best_effort import best_effort
from app.services.filestore import STATE_DIR, home, read_json, write_json

logger = logging.getLogger(__name__)

# One brief per backend start, on top of the cooldown: a focus storm after
# resume must not re-enter the model every time.
_briefed_this_boot = False

_STATE_FILE = 'wake-up.json'
_DEFAULT_COOLDOWN_MINUTES = 30
_MIN_COOLDOWN_MINUTES = 5
_MAX_COOLDOWN_MINUTES = 1440

MAX_SESSIONS_IN_BRIEF = 4
MAX_JOBS_IN_BRIEF = 5


def _state_path():
    return home(STATE_DIR) / _STATE_FILE


def _load_state() -> dict[str, object]:
    raw = read_json(_state_path(), default={})
    return raw if isinstance(raw, dict) else {}


def _save_state(state: dict[str, object]) -> None:
    write_json(_state_path(), state)


# ── config (config.json → wakeUp) ───────────────────────────────────────────

def get_wake_up_config() -> dict[str, object]:
    cfg = config_service.getConfig()
    user = as_dict(cfg.get('wakeUp'), {})
    return {
        'enabled': as_bool(user.get('enabled'), True),
        'quiet': as_bool(user.get('quiet'), False),
        'cooldownMinutes': max(
            _MIN_COOLDOWN_MINUTES,
            min(_MAX_COOLDOWN_MINUTES, as_int(user.get('cooldownMinutes'), _DEFAULT_COOLDOWN_MINUTES)),
        ),
    }


def set_wake_up_config(patch: dict[str, object]) -> tuple[bool, str, dict[str, object]]:
    """Validate and apply a partial wake-up config patch."""
    current = get_wake_up_config()
    for field, value in patch.items():
        if field not in ('enabled', 'quiet', 'cooldownMinutes'):
            return (False, f'unknown field: {field!r}', current)
        if field == 'cooldownMinutes':
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return (False, "'cooldownMinutes' must be a number", current)
            if not (_MIN_COOLDOWN_MINUTES <= value <= _MAX_COOLDOWN_MINUTES):
                return (
                    False,
                    f"'cooldownMinutes' must be between {_MIN_COOLDOWN_MINUTES} "
                    f'and {_MAX_COOLDOWN_MINUTES}',
                    current,
                )
        elif not isinstance(value, bool):
            return (False, f'{field!r} must be a boolean', current)
        current[field] = value  # type: ignore[literal-required]
    cfg = config_service.getConfig()
    cfg['wakeUp'] = dict(current)
    config_service.saveConfig(cfg)
    return (True, '', get_wake_up_config())


# ── fact digest ─────────────────────────────────────────────────────────────

def _relative_age(iso: object) -> str:
    """Compact age of an ISO timestamp ('12m ago', '3h ago', '2d ago')."""
    try:
        from datetime import datetime

        ts = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
        age = max(0, time.time() - ts.timestamp())
    except (TypeError, ValueError, OverflowError, OSError):
        return 'recently'
    if age < 3600:
        return f'{max(1, int(age // 60))}m ago'
    if age < 86400:
        return f'{int(age // 3600)}h ago'
    return f'{int(age // 86400)}d ago'


def _gather_facts() -> dict[str, object]:
    """What the harness actually holds — the raw material of the brief."""
    sessions: list[dict[str, object]] = []
    with best_effort('wake-up.session-facts'):
        from app.services.workbench.sessions import list_workbench_sessions

        for s in list_workbench_sessions():
            if as_int(s.get('messageCount'), 0) <= 0:
                continue
            sessions.append(
                {
                    'title': as_str(s.get('title'), 'Untitled'),
                    'updated': _relative_age(s.get('updatedAt')),
                    'messages': as_int(s.get('messageCount'), 0),
                }
            )
            if len(sessions) >= MAX_SESSIONS_IN_BRIEF:
                break

    jobs: list[dict[str, object]] = []
    with best_effort('wake-up.automation-facts'):
        from app.services.automations_store import list_jobs

        for j in list_jobs():
            status = as_str(j.get('status'), '')
            if status in ('cancelled', 'disabled'):
                continue
            jobs.append(
                {
                    'name': as_str(j.get('name') or j.get('title'), 'automation'),
                    'status': status or 'unknown',
                }
            )
            if len(jobs) >= MAX_JOBS_IN_BRIEF:
                break
    return {'recentSessions': sessions, 'automations': jobs}


def _fallback_brief(facts: dict[str, object]) -> str:
    """The fact digest itself, when no model is available to phrase it."""
    lines: list[str] = ['Welcome back.']
    sessions = as_list_of_dicts(facts.get('recentSessions'))
    if sessions:
        titled = ' · '.join(f"{as_str(s.get('title'))} ({as_str(s.get('updated'))})" for s in sessions[:3])
        lines.append(f'Where you left off: {titled}.')
    jobs = as_list_of_dicts(facts.get('automations'))
    if jobs:
        names = ', '.join(as_str(j.get('name')) for j in jobs[:3])
        lines.append(f'Background automations: {names}.')
    lines.append('What would you like to pick up first?')
    return '\n'.join(lines)


def as_list_of_dicts(value: object) -> list[dict[str, object]]:
    """Narrow an object to a list of dicts (local helper — wake-up only)."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


async def _compose(facts: dict[str, object]) -> str:
    """Model-phrased brief from the fact digest; falls back to the digest."""
    prompt = (
        'The user just returned to their desk. Write their wake-up brief: '
        '2-4 short lines covering where things left off (recent sessions) and '
        'any open background automations, ending with ONE concrete question to '
        're-engage. Use only the facts below — do not invent work, deadlines, '
        'or results. No pleasantries beyond "Welcome back.".\n\nFacts:\n'
        + json.dumps(facts, ensure_ascii=False)
    )
    with best_effort('wake-up.model-compose'):
        from app.services.workbench.model_fleet import resolveRoleModel

        model, provider_hint = resolveRoleModel('cerebellum')
        if not model:
            return _fallback_brief(facts)
        from app.providers import resolver as providerResolver
        from app.providers.clients import getClient

        provider = providerResolver.resolve(provider_hint or model)
        if not provider:
            return _fallback_brief(facts)
        client = getClient(provider)
        if client is None:
            return _fallback_brief(facts)
        system = (
            'You are August, the user’s local assistant. You write one short '
            'wake-up brief when the user returns. Be concrete and brief; never '
            'invent tasks or results that are not in the facts.'
        )
        response = await client.generate(prompt, system=system)
        text = str(response or '').strip()
        return text or _fallback_brief(facts)
    return _fallback_brief(facts)


# ── the brief check ─────────────────────────────────────────────────────────

async def brief(reason: str) -> dict[str, Any]:
    """Decide whether the user returning now deserves a brief — and give it.

    ``reason`` is the shell-reported trigger ('startup' | 'focus' | ...), kept
    for the receipt. Gated by: the on/off switch, the cooldown since the last
    brief, and at most one brief per backend start.
    """
    global _briefed_this_boot

    cfg = get_wake_up_config()
    if not cfg['enabled']:
        return {'shouldBrief': False, 'reason': 'disabled'}

    now = time.time()
    state = _load_state()
    last_brief_at = float(str(state.get('lastBriefAt') or 0) or 0)
    elapsed = now - last_brief_at
    cooldown_s = as_int(cfg['cooldownMinutes'], _DEFAULT_COOLDOWN_MINUTES) * 60
    if last_brief_at > 0 and elapsed < cooldown_s:
        return {
            'shouldBrief': False,
            'reason': 'cooldown',
            'nextBriefInS': int(cooldown_s - elapsed),
        }
    if _briefed_this_boot:
        return {'shouldBrief': False, 'reason': 'already-briefed-this-start'}

    facts = _gather_facts()
    text = await _compose(facts)

    state['lastBriefAt'] = now
    state['lastBriefReason'] = as_str(reason, 'unknown')[:40]
    _save_state(state)
    _briefed_this_boot = True
    logger.info('wake-up brief delivered (reason=%s)', reason)

    sessions = as_list_of_dicts(facts.get('recentSessions'))
    return {
        'shouldBrief': True,
        'reason': as_str(reason, 'unknown'),
        'quiet': cfg['quiet'],
        'text': text,
        'recentSessions': sessions[:3],
        'automations': as_list_of_dicts(facts.get('automations')),
    }


def mark_active() -> None:
    """Record that the user is here (focus heartbeat) — reserved for the
    cooldown's future "ignore briefs while actively chatting" refinement."""
    state = _load_state()
    state['lastActiveAt'] = time.time()
    _save_state(state)


def dismiss() -> dict[str, object]:
    """Record a dismissal of the queued (quiet-mode) brief."""
    state = _load_state()
    state['lastDismissAt'] = time.time()
    _save_state(state)
    return {'ok': True}
