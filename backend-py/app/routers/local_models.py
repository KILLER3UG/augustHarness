"""Local model servers: scan loopback, GGUF files, the llama.cpp runtime,
and Hugging Face downloads.

The scan is read-only — nothing is written to ``providers.json`` until the
user adds a server or serves a GGUF, and both writes go through the
ordinary provider-create path (same store write, same realtime push), not
a parallel one.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app.json_narrowing import as_list, as_str
from app.models.camel_base import CamelModel

router = APIRouter(prefix='/api/local-models')
hf = APIRouter(prefix='/api/hf')


class ScanBody(CamelModel):
    """Optional extra ports to probe (the well-known list is always probed)."""

    ports: list[int] = []


class AddLocalServer(CamelModel):
    """One detected server, offered back for the add."""

    base_url: str
    name: str = ''
    api_format: str = 'openaiChat'


class GgufScanBody(CamelModel):
    path: str


class GgufServeBody(CamelModel):
    path: str
    ctx_size: int = 8192


class ServerStopBody(CamelModel):
    server_id: str


class RuntimeInstallBody(CamelModel):
    """Empty body = auto-detect the variant; ``variant`` overrides it."""

    variant: str | None = None


class HfSearchQuery(CamelModel):
    q: str = ''
    limit: int = 20


class HfDownloadBody(CamelModel):
    repo_id: str
    filename: str


class HfCancelBody(CamelModel):
    job_id: str


# ── server scan / add (Phase 1) ────────────────────────────────────────────


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


# ── llama.cpp runtime (Phase 2) ────────────────────────────────────────────


@router.get('/runtime')
async def runtime_status():
    """Runtime install state + the variant auto-detection, for the UI card."""
    from app.services import llama_runtime

    resolved = await llama_runtime.resolve_variant(None)
    from app.services import llama_runtime_manifest as mf

    return {
        'installed': llama_runtime.installed_variant(),
        'tag': mf.MANIFEST_TAG,
        'install': llama_runtime.install_status(),
        'suggestion': {
            'variant': resolved['variant'],
            'auto': resolved['auto'],
            'reason': resolved['reason'],
            'gpu': resolved['gpu'],
        },
        'variants': [
            {
                'name': name,
                'label': mf.variant_label(name),
                'requires': mf.variant_requires(name),
                'totalBytes': mf.variant_total_bytes(name),
            }
            for name in mf.variant_names()
        ],
    }


@router.post('/runtime/install')
async def runtime_install(body: RuntimeInstallBody | None = None):
    """Kick off the runtime install in the background; poll GET /runtime.

    The download is hundreds of megabytes — the request returns at once and
    the install state (progress, phase, error) is what the UI polls.
    """
    from app.services import llama_runtime

    if llama_runtime.install_status().get('running'):
        return {'started': False, 'install': llama_runtime.install_status()}
    variant = body.variant if body else None

    async def _run() -> None:
        try:
            await llama_runtime.install_runtime(variant)
        except Exception as exc:  # noqa: BLE001 — state carries the error
            import logging

            logging.getLogger(__name__).error('runtime install task failed: %s', exc)

    asyncio.create_task(_run())
    return {'started': True, 'install': llama_runtime.install_status()}


# ── GGUF files (Phase 2) ───────────────────────────────────────────────────


@router.post('/gguf/scan')
async def gguf_scan(body: GgufScanBody):
    """List GGUF files under a user-picked folder (the Tauri folder picker's
    answer); read-only, bounded walk."""
    from app.services.local_models import scan_gguf_folder

    try:
        return scan_gguf_folder(body.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get('/servers')
async def servers():
    """Running llama-server instances and their status."""
    from app.services import llama_runtime

    return llama_runtime.list_servers()


@router.post('/gguf/serve')
async def gguf_serve(body: GgufServeBody):
    """Serve one GGUF via llama-server and register it as a provider.

    Returns the instance row immediately (status ``starting``); poll
    GET /servers for ``ready``/``failed``. The provider is created through
    the ordinary create path, models discovered, priced 0.0.
    """
    from app.services import llama_runtime

    try:
        row = await llama_runtime.start_gguf_server(body.path, ctx_size=body.ctx_size)
    except llama_runtime.HTTPishError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return row


@router.post('/servers/stop')
async def server_stop(body: ServerStopBody):
    from app.services import llama_runtime

    try:
        return await llama_runtime.stop_gguf_server(body.server_id)
    except llama_runtime.HTTPishError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ── Hugging Face (Phase 2) ─────────────────────────────────────────────────


@hf.get('/search')
async def hf_search(q: str = '', limit: int = 20, filter: str = ''):
    """Search the Hub. Empty ``filter`` browses ALL models; ``gguf`` narrows
    to what August can serve."""
    from app.services import hf_hub

    try:
        return await hf_hub.search_models(q, limit=limit, model_filter=filter or None)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@hf.get('/repos/{repoId:path}')
async def hf_repo(repoId: str):
    from app.services import hf_hub

    try:
        return await hf_hub.repo_files(repoId)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        status = 404 if 'not found' in str(exc).lower() else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@hf.get('/downloads')
async def hf_downloads():
    from app.services import hf_downloads

    return hf_downloads.list_downloads()


@hf.post('/downloads')
async def hf_start_download(body: HfDownloadBody):
    from app.services import hf_downloads

    try:
        return await hf_downloads.start_download(body.repo_id, body.filename)
    except hf_downloads.DownloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@hf.post('/downloads/cancel')
async def hf_cancel_download(body: HfCancelBody):
    from app.services import hf_downloads

    return await hf_downloads.cancel_download(body.job_id)
