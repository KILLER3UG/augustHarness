"""Chat-secret routes — the user-facing half of ``request_secret``.

The fill route is the only door that touches the value, and it writes
straight into the encrypted store. There is deliberately NO read route:
secrets requested in chat are consumable by tools and MCP env references,
never readable back over HTTP or into a conversation.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.camel_base import CamelModel

router = APIRouter(prefix='/api/chat-secrets')


class SecretFillBody(CamelModel):
    name: str
    value: str


@router.get('/pending')
async def pending():
    """Pending chat-secret requests (the transcript cards are the primary
    surface; this backs any roster-level badge)."""
    from app.services.memory_store import kv

    raw = kv.get_memory('secret_requests')
    rows = []
    if isinstance(raw, dict):
        for name, entry in raw.items():
            if isinstance(entry, dict) and entry.get('status') == 'pending':
                rows.append(
                    {
                        'name': name,
                        'description': entry.get('description', ''),
                        'sessionId': entry.get('sessionId', ''),
                    }
                )
    return {'pending': rows}


@router.post('/fill')
async def fill(body: SecretFillBody):
    """Store the user-submitted value; the value never echoes back."""
    from app.services.tools import chat_secrets

    try:
        return chat_secrets.fill(body.name, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
