"""Chat-requested secrets — the rerun ``request_secret`` pattern.

When a turn needs a credential (an API key for an MCP server it just
registered, a service token), the user pasting it into the chat would put
the secret into the transcript forever. Instead the model calls
``request_secret(NAME)``: a masked form appears as a card on the tool call,
the submitted value goes straight into the encrypted store under
``chatSecrets.<NAME>``, and the model is told only the NAME and the
status — the value never enters the conversation.

The store is the standard ``secret_store`` (DPAPI on Windows), the same
file that holds provider credentials, under its own prefix so pruning
stays scoped. Requests are tracked in the brain KV (``secret_requests``)
with the transcript holding the card.
"""

from __future__ import annotations

import logging
import re

from app.json_narrowing import as_str
from app.services import secret_store

logger = logging.getLogger(__name__)

SECRET_PREFIX = 'chatSecrets.'  # noqa: S105 — a key prefix, not a credential
_KV_KEY = 'secret_requests'
_NAME_RE = re.compile(r'^[A-Z][A-Z0-9_]{1,63}$')
_MAX_PENDING = 20


def _load_requests() -> dict[str, dict[str, object]]:
    """The request map from the brain KV — untyped JSON, narrowed once."""
    from app.json_narrowing import as_dict
    from app.services.memory_store import kv

    raw = as_dict(kv.get_memory(_KV_KEY), {})
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


def _save_requests(requests: dict[str, dict[str, object]]) -> None:
    from typing import cast

    from app.services.memory_store import kv
    from app.type_aliases import JsonValue

    kv.save_internal(_KV_KEY, cast(JsonValue, requests))


def validate_name(name: str) -> str:
    """Env-style name (UPPER_SNAKE) — it is how the secret is referenced."""
    cleaned = (name or '').strip()
    if not _NAME_RE.match(cleaned):
        raise ValueError(
            "Secret name must be UPPER_SNAKE (letters, digits, underscore, "
            f"2-64 chars) so tools can reference it — got {name!r}"
        )
    return cleaned


def request(name: str, description: str, session_id: str) -> dict[str, object]:
    """Register a pending secret request; idempotent on name."""
    clean = validate_name(name)
    requests = _load_requests()
    existing = requests.get(clean)
    if isinstance(existing, dict) and existing.get('status') == 'filled':
        return existing
    entry: dict[str, object] = {
        'name': clean,
        'description': (description or '').strip()[:400],
        'sessionId': session_id,
        'status': 'pending',
        'createdAt': _now(),
    }
    requests[clean] = entry
    # Bound the map. The oldest FILLED entries drop first — a filled form's
    # card stays answerable via secret_check either way (the value is in the
    # encrypted store, not here). When everything is pending — a model minting
    # unique names — the oldest PENDING entry drops instead, so a runaway
    # request_secret loop cannot grow the KV without bound; the unwritten
    # forms simply disappear from the pending list, which is the honest
    # outcome for a request nobody filled.
    while len(requests) > _MAX_PENDING:
        oldest = min(
            requests,
            key=lambda k: (
                0 if isinstance(requests[k], dict) and requests[k].get('status') == 'filled' else 1,
                str(requests[k].get('createdAt', '')) if isinstance(requests[k], dict) else '',
            ),
            default=None,
        )
        if oldest is None:
            break
        del requests[oldest]
    _save_requests(requests)
    return entry


def check(name: str) -> dict[str, object]:
    """Status of one request — never the value.

    A FILLED entry whose request row was evicted by the map bound is still
    answerable, because the value lives in the encrypted store and not in the
    map: ``secret_check`` reports it filled from the store alone. Consulting
    the map only would make an evicted-but-filled secret read as ``unknown``,
    which contradicts the bound's own contract ("a filled form's card stays
    answerable via secret_check either way") — and would tell the model a
    secret it can use does not exist.
    """
    clean = validate_name(name)
    entry = _load_requests().get(clean)
    if not isinstance(entry, dict):
        stored = secret_store.getSecret(SECRET_PREFIX + clean) is not None
        return {'name': clean, 'status': 'filled' if stored else 'unknown'}
    status = str(entry.get('status', 'pending'))
    if status == 'pending' and secret_store.getSecret(SECRET_PREFIX + clean) is not None:
        entry['status'] = 'filled'
        entry['filledAt'] = _now()
        requests = _load_requests()
        requests[clean] = entry
        _save_requests(requests)
        status = 'filled'
    return {
        'name': clean,
        'status': status,
        'description': as_str(entry.get('description')),
        'secretRef': f'{SECRET_PREFIX}{clean}',
    }


def fill(name: str, value: str) -> dict[str, object]:
    """Store the user-submitted value (the ONLY path that touches it)."""
    clean = validate_name(name)
    value = (value or '').strip()
    if not value:
        raise ValueError('Refusing to store an empty secret')
    secret_store.setSecret(SECRET_PREFIX + clean, value)
    requests = _load_requests()
    prior = requests.get(clean)
    entry: dict[str, object] = dict(prior) if isinstance(prior, dict) else {}
    entry.update({'name': clean, 'status': 'filled', 'filledAt': _now()})
    if 'sessionId' not in entry:
        entry['sessionId'] = ''
    requests[clean] = entry
    _save_requests(requests)
    logger.info('chat secret %s filled (value never transits the conversation)', clean)
    return {'name': clean, 'status': 'filled', 'secretRef': f'{SECRET_PREFIX}{clean}'}


def resolve_reference(value: str) -> str:
    """Resolve a ``${NAME}`` reference against the chat-secrets store.

    Used where MCP env values are materialized: ``${GITHUB_TOKEN}`` in an
    MCP server's env map becomes the stored secret's value; an unresolved
    reference passes through untouched (so a literal `${X}` in some config
    is never corrupted).
    """
    stripped = (value or '').strip()
    m = re.fullmatch(r'\$\{([A-Z][A-Z0-9_]{1,63})\}', stripped)
    if not m:
        return value
    resolved = secret_store.getSecret(SECRET_PREFIX + m.group(1))
    if resolved is None:
        resolved = secret_store.getSecret('mcpGlobalEnv.' + m.group(1))
    return value if resolved is None else resolved


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds')
