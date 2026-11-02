"""Detect local model servers on loopback.

Read-only probes of well-known ports on 127.0.0.1; the user decides what
becomes a provider via the add endpoint. Backend-originated localhost calls
are the same trust class as the health monitor and the LAN features — the
SSRF guard lives on the web *tools*, not here.

Identification is by response shape, never by port alone: a port only breaks
ties between generic OpenAI-compatible responders. The classifier has to
survive an "LM Studio-like" server that no release of LM Studio ever shipped
(the user's own words: "it's the same as LM Studio") — anything answering
``/v1/models`` with an OpenAI body is offered as ``openai-compatible``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Port → the first probe that identifies a server there. Ordered probes only
# when the first one answers, so an idle port costs one connect timeout.
_PROBE_PORTS = (11434, 1234, 8080, 1337, 4891, 8188)

_PROBE_TIMEOUT = httpx.Timeout(1.5, connect=0.4)

_KIND_LABELS = {
    'ollama': 'Ollama',
    'lmstudio': 'LM Studio',
    'llamacpp': 'llama.cpp server',
    'jan': 'Jan',
    'gpt4all': 'GPT4All',
    'comfyui': 'ComfyUI',
    'openai': 'OpenAI-compatible server',
}


def _models_from_openai_payload(data: Any) -> list[str]:
    """Model ids from a ``/v1/models`` body (``data``/``models``/bare list)."""
    raw = data.get('data', data.get('models')) if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    ids: list[str] = []
    for m in raw:
        if isinstance(m, dict):
            mid = str(m.get('id') or m.get('name') or '').strip()
            if mid:
                ids.append(mid)
        elif isinstance(m, str) and m.strip():
            ids.append(m.strip())
    return ids


async def _get_json(client: httpx.AsyncClient, url: str) -> tuple[int, Any, dict[str, str]]:
    """GET a URL, returning ``(status, json_or_none, headers)`` without raising."""
    try:
        resp = await client.get(url)
        headers = {k.lower(): v for k, v in resp.headers.items()}
        if resp.status_code != 200:
            return resp.status_code, None, headers
        return resp.status_code, resp.json(), headers
    except Exception:  # noqa: BLE001 — an unreachable port is a scan result, not an error
        return 0, None, {}


def _descriptor(
    kind: str,
    port: int,
    base_url: str,
    models: list[str],
    *,
    detail: str = '',
) -> dict[str, object]:
    return {
        'kind': kind,
        'name': _KIND_LABELS.get(kind, kind),
        'port': port,
        'baseUrl': base_url,
        'apiFormat': 'openaiChat' if kind != 'comfyui' else 'comfyui',
        'models': models,
        'detail': detail,
    }


async def _probe_port(client: httpx.AsyncClient, port: int) -> dict[str, object] | None:
    """Probe one loopback port; return a server descriptor or None."""
    root = f'http://127.0.0.1:{port}'

    # Ollama's native API answers /api/tags with its model list.
    status, tags, _ = await _get_json(client, f'{root}/api/tags')
    if status == 200 and isinstance(tags, dict) and isinstance(tags.get('models'), list):
        models = [
            str(m.get('name') or m.get('model') or '').strip()
            for m in tags['models']
            if isinstance(m, dict) and str(m.get('name') or m.get('model') or '').strip()
        ]
        return _descriptor('ollama', port, f'{root}/v1', models)

    # ComfyUI answers /system_stats.
    status, stats, _ = await _get_json(client, f'{root}/system_stats')
    if status == 200 and isinstance(stats, dict) and isinstance(stats.get('system'), dict):
        return _descriptor('comfyui', port, root, [])

    # Everything else on the list speaks OpenAI at /v1/models.
    status, payload, headers = await _get_json(client, f'{root}/v1/models')
    if status == 200:
        models = _models_from_openai_payload(payload)
        server_header = str(headers.get('server', '')).lower()
        if 'llama.cpp' in server_header:
            kind = 'llamacpp'
        else:
            v0status, _, _ = await _get_json(client, f'{root}/api/v0/models')
            if v0status == 200:
                kind = 'lmstudio'
            elif port == 1234:
                # Default LM Studio port, OpenAI body, no llama.cpp header —
                # label it LM Studio; a different server could live there and
                # still works, the label is only a display hint.
                kind = 'lmstudio'
            elif port == 1337:
                kind = 'jan'
            elif port == 4891:
                kind = 'gpt4all'
            else:
                kind = 'openai'
        return _descriptor(kind, port, f'{root}/v1', models)

    # llama.cpp answers /health even before a model is loaded.
    status, health, _ = await _get_json(client, f'{root}/health')
    if status == 200 and isinstance(health, dict) and health.get('status') == 'ok':
        return _descriptor('llamacpp', port, f'{root}/v1', [], detail='no model loaded yet')

    return None


async def scan_local_servers(extra_ports: list[int] | None = None) -> dict[str, object]:
    """Probe loopback ports for local model servers; read-only.

    Returns ``{'servers': [...]}`` sorted by port. Connection-refused ports
    cost microseconds; filtered ports cost one connect timeout each.
    """
    ports = list(_PROBE_PORTS)
    for p in extra_ports or []:
        try:
            port = int(p)
        except (TypeError, ValueError):
            continue
        if 1 <= port <= 65535 and port not in ports:
            ports.append(port)

    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT, follow_redirects=False) as client:
        results = await asyncio.gather(*(_probe_port(client, port) for port in ports))

    servers = [r for r in results if r is not None]
    servers.sort(key=lambda s: int(str(s.get('port', 0))) if s.get('port') is not None else 0)
    logger.info('local model scan found %d server(s) on %d port(s)', len(servers), len(ports))
    return {'servers': servers}
