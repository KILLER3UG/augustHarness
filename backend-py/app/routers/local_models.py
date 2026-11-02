"""Local model servers: scan loopback and offer what is found as providers.

The scan is read-only — nothing is written to ``providers.json`` until the
user adds a server, and the add is the ordinary provider-create path (same
store write, same cache invalidations, same realtime push), not a parallel
one.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.json_narrowing import as_list, as_str
from app.models.camel_base import CamelModel

router = APIRouter(prefix='/api/local-models')


class ScanBody(CamelModel):
    """Optional extra ports to probe (the well-known list is always probed)."""

    ports: list[int] = []


class AddLocalServer(CamelModel):
    """One detected server, offered back for the add."""

    base_url: str
    name: str = ''
    api_format: str = 'openaiChat'


@router.post('/scan')
async def scan(body: ScanBody | None = None):
    """Probe loopback ports for local model servers; read-only."""
    from app.providers.api_format import normalize_provider_base_url
    from app.services import config_service
    from app.services.local_models import scan_local_servers

    result = await scan_local_servers(list(body.ports) if body else None)
    servers = as_list(result.get('servers'), [])

    # Mark servers the user already added (same normalized base URL), so the
    # UI can show "added" instead of offering a duplicate.
    existing: set[str] = set()
    for entry in as_list(config_service.getProvidersStore().get('providers'), []):
        if isinstance(entry, dict):
            base = normalize_provider_base_url(entry.get('baseUrl'))
            if base:
                existing.add(base.lower())
    for server in servers:
        if isinstance(server, dict):
            server['added'] = (
                normalize_provider_base_url(server.get('baseUrl')).lower() in existing
                if server.get('baseUrl')
                else False
            )
    return result


@router.post('/add')
async def add(body: AddLocalServer):
    """Create a provider for a detected local server and fetch its models.

    Reuses the ordinary provider-create route (id minting, base-URL
    normalization, store write, realtime invalidations) so a local server is
    stored exactly like any provider the user typed in by hand.
    """
    from app.models.config import ProviderCreate, ProviderUpdate
    from app.routers.providers import (
        _discoverProviderModels,
        _public,
        createProvider,
        updateProvider,
    )
    from app.services import config_service

    if not body.base_url.strip():
        raise HTTPException(status_code=400, detail='baseUrl is required')

    created = await createProvider(
        ProviderCreate(
            name=body.name.strip() or 'Local server',
            base_url=body.base_url,
            api_format=body.api_format or 'openaiChat',
            api_key='',
            enabled=True,
        )
    )
    providerId = as_str(created.get('id'), '')
    if not providerId:
        raise HTTPException(status_code=500, detail='provider create returned no id')

    # Local servers should auto-discover their model list (keyless hosts are
    # now fetched too); the create route cannot know that intent.
    await updateProvider(providerId, ProviderUpdate(auto_fetch=True))
    discovery = await _discoverProviderModels(providerId, persist=True)

    entry = None
    for p in as_list(config_service.getProvidersStore().get('providers'), []):
        if isinstance(p, dict) and as_str(p.get('id', '')) == providerId:
            entry = p
            break
    return {
        'provider': _public(entry) if entry else created,
        'discovery': {
            'added': discovery[0],
            'updated': discovery[1],
            'removed': discovery[2],
        },
    }
