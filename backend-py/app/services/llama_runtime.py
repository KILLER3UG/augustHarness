"""On-demand llama.cpp runtime: download once into app data, serve GGUF files.

Everything the user owns lives under ``{dataDir}/runtimes/`` — the same
class of state as the brain DB, deliberately OUTSIDE the installer
payload (the two install guards: a fresh install starts empty, an update
keeps everything). Nothing here touches the update path.

The install verifies every asset against the SHA256 pinned in
``llama_runtime_manifest`` before a byte is extracted, and extraction is
zip-slip guarded. A GPU is detected through ``nvidia-smi``; the user can
always override the variant from the UI because no detection is honest
about every machine.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import socket
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

from app.json_narrowing import as_list
from app.lib.paths import dataDir
from app.services import http_env
from app.services import llama_runtime_manifest as manifest

logger = logging.getLogger(__name__)

_RUNTIME_ROOT = 'runtimes'
_RUNTIME_DIR = 'llama.cpp'
_TEMP_DIR = 'tmp'
_MARKER_FILE = 'installed.json'
_SERVERS_FILE = 'servers.json'
_SERVER_NAME_PREFIX = 'GGUF · '
# The ONE provider all served GGUFs live under (user-facing name in the
# picker's provider group). The name is one word by owner choice; migration v3
# renames an existing entry so an install does not have to serve again first.
_LOCAL_PROVIDER_ID = 'local-models'
_LOCAL_PROVIDER_NAME = 'LocalModels'

# Health-wait for a loading model: big GGUFs can take minutes; /health
# answers 503 "loading" until the server is ready, and we keep polling
# rather than failing a serve that is making progress.
_SERVER_START_TIMEOUT_S = 900
_HEALTH_POLL_S = 1.0
_DOWNLOAD_CHUNK = 1 << 20


# ── paths ──────────────────────────────────────────────────────────────────


def runtime_root() -> Path:
    return dataDir() / _RUNTIME_ROOT / _RUNTIME_DIR


def variant_dir(variant: str) -> Path:
    return runtime_root() / variant


def _temp_dir() -> Path:
    p = runtime_root() / _TEMP_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _servers_file() -> Path:
    return runtime_root() / _SERVERS_FILE


def _marker(variant: str) -> Path:
    return variant_dir(variant) / _MARKER_FILE


# ── install state (module-scoped; one install at a time) ──────────────────

_install_lock = asyncio.Lock()
_install_state: dict[str, Any] = {
    'running': False,
    'variant': None,
    'asset': None,
    'bytesDone': 0,
    'totalBytes': 0,
    'error': None,
    'done': False,
}


def _reset_install_state(variant: str | None = None) -> None:
    _install_state.update(
        running=False,
        variant=variant,
        asset=None,
        bytesDone=0,
        totalBytes=0,
        error=None,
        done=False,
    )


# ── detection ──────────────────────────────────────────────────────────────


async def detect_gpu() -> dict[str, Any]:
    """What GPU (and CUDA driver level) this machine has, via nvidia-smi.

    ``nvidia-smi``'s banner carries the *maximum* CUDA runtime the
    installed driver supports — exactly what variant selection needs. A
    missing or failing nvidia-smi is a normal result (no NVIDIA), not an
    error.
    """
    exe = shutil.which('nvidia-smi')
    if not exe:
        return {
            'nvidia': False,
            'cudaVersion': None,
            'gpuName': None,
            'driverVersion': None,
            'vramTotalMb': None,
            'vramFreeMb': None,
        }
    flags = getattr(asyncio.subprocess, 'CREATE_NO_WINDOW', 0) if hasattr(asyncio.subprocess, 'CREATE_NO_WINDOW') else 0
    vram_total_mb: int | None = None
    vram_free_mb: int | None = None

    # "8188 MiB" → int. Absent or unparseable stays None rather than 0: a zero
    # would read as "no VRAM" to the offload badge and refuse a model this
    # machine can run.
    def _mib(raw: str | None) -> int | None:
        digits = ''.join(ch for ch in (raw or '') if ch.isdigit())
        return int(digits) if digits else None

    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            '--query-gpu=name,driver_version,memory.total,memory.free',
            '--format=csv,noheader',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=flags,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            return {
                'nvidia': False,
                'cudaVersion': None,
                'gpuName': None,
                'driverVersion': None,
                'vramTotalMb': None,
                'vramFreeMb': None,
            }
        if proc.returncode != 0:
            return {
                'nvidia': False,
                'cudaVersion': None,
                'gpuName': None,
                'driverVersion': None,
                'vramTotalMb': None,
                'vramFreeMb': None,
            }
        first = out.decode('utf-8', 'replace').strip().splitlines()
        cols = [c.strip() for c in (first[0].split(',') if first else [])]
        gpu_name = cols[0] if cols and cols[0] else None
        driver_version = cols[1] if len(cols) > 1 and cols[1] else None
        vram_total_mb = _mib(cols[2] if len(cols) > 2 else None)
        vram_free_mb = _mib(cols[3] if len(cols) > 3 else None)
    except Exception:  # noqa: BLE001 — a broken driver install is "no NVIDIA"
        return {
            'nvidia': False,
            'cudaVersion': None,
            'gpuName': None,
            'driverVersion': None,
            'vramTotalMb': None,
            'vramFreeMb': None,
        }

    # Plain `nvidia-smi` header for the CUDA level. Two banner generations:
    # "| NVIDIA-SMI ... CUDA Version: 12.9 |" (classic) and
    # "NVIDIA-SMI 616.64  KMD Version: ... CUDA UMD Version: 13.4" (616+).
    # Query flags do not print it.
    cuda_version = None
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=flags,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            out = b''
        match = re.search(
            r'CUDA(?: UMD)? Version:\s*(\d+\.\d+)', out.decode('utf-8', 'replace')
        )
        if match:
            cuda_version = match.group(1)
    except Exception:  # noqa: BLE001
        cuda_version = None
    return {
        'nvidia': True,
        'cudaVersion': cuda_version,
        'gpuName': gpu_name,
        'driverVersion': driver_version,
        'vramTotalMb': vram_total_mb,
        'vramFreeMb': vram_free_mb,
    }


async def resolve_variant(override: str | None = None) -> dict[str, Any]:
    """The variant to install: the override, or detection, with the reason."""
    if override:
        manifest.variant_assets(override)  # raises on an unknown variant
        return {
            'variant': override,
            'auto': False,
            'reason': 'chosen manually',
            'gpu': await detect_gpu(),
        }
    gpu = await detect_gpu()
    chosen = manifest.auto_variant_for_cuda(gpu.get('cudaVersion'), gpu.get('nvidia', False))
    if gpu.get('nvidia'):
        reason = f"NVIDIA driver reports CUDA {gpu.get('cudaVersion') or 'unknown'}"
    else:
        reason = 'no NVIDIA GPU detected — Vulkan works on every vendor'
    return {'variant': chosen, 'auto': True, 'reason': reason, 'gpu': gpu}


# ── installed? ─────────────────────────────────────────────────────────────


def installed_variant(variant: str | None = None) -> str | None:
    """The variant currently installed (matches the pinned tag), if any."""
    if variant:
        info = _read_marker(variant)
        return variant if info and info.get('tag') == manifest.MANIFEST_TAG else None
    for name in manifest.variant_names():
        info = _read_marker(name)
        if info and info.get('tag') == manifest.MANIFEST_TAG:
            return name
    return None


def _read_marker(variant: str) -> dict[str, Any] | None:
    p = _marker(variant)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text('utf-8'))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 — corrupt marker = not installed
        return None


# ── install ────────────────────────────────────────────────────────────────


async def install_runtime(override: str | None = None) -> dict[str, Any]:
    """Download, verify, and extract the runtime for ``override`` or the
    detected variant. Idempotent: an installed, current variant is a no-op.
    Returns the final install state; progress is observable on
    ``install_status()`` from another request while this runs."""
    async with _install_lock:
        resolved = await resolve_variant(override)
        variant = str(resolved['variant'])
        if installed_variant(variant):
            _reset_install_state(variant)
            _install_state['done'] = True
            return dict(_install_state)

        if not _is_windows():
            raise RuntimeError(
                'The local llama.cpp runtime is pinned to Windows x64 assets; '
                'run August on Windows or extend llama_runtime_manifest.'
            )

        _reset_install_state(variant)
        _install_state['running'] = True
        _install_state['totalBytes'] = manifest.variant_total_bytes(variant)
        target = variant_dir(variant)
        try:
            assets = manifest.variant_assets(variant)
            for asset in assets:
                await _download_and_extract(asset, target)
            target.mkdir(parents=True, exist_ok=True)
            _marker(variant).write_text(
                json.dumps(
                    {
                        'variant': variant,
                        'tag': manifest.MANIFEST_TAG,
                        'installedAt': _now(),
                        'assets': [a['name'] for a in assets],
                    }
                ),
                encoding='utf-8',
            )
            _cleanup_temp()
            _install_state['running'] = False
            _install_state['done'] = True
            logger.info('llama.cpp runtime %s installed (%s)', variant, manifest.MANIFEST_TAG)
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI verbatim
            _install_state['running'] = False
            _install_state['done'] = False
            _install_state['error'] = str(exc)
            logger.error('llama.cpp runtime install failed: %s', exc)
        return dict(_install_state)


def _is_windows() -> bool:
    import sys

    return sys.platform == 'win32'


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds')


def install_status() -> dict[str, Any]:
    return dict(_install_state)


async def _download_and_extract(asset: dict[str, object], target: Path) -> None:
    url = f'{manifest.DOWNLOAD_BASE_URL}/{asset["name"]}'
    part = _temp_dir() / f'{asset["name"]}.part'
    expected_size = int(str(asset['size']))
    expected_sha = str(asset['sha256'])

    _install_state['asset'] = str(asset['name'])
    _install_state['bytesDone'] = 0

    timeout = httpx.Timeout(connect=15, read=60, write=60, pool=15)
    digest = hashlib.sha256()
    done = 0
    async with http_env.client(follow_redirects=True, timeout=timeout) as client:
        async with client.stream('GET', url) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f'{asset["name"]}: download returned HTTP {resp.status_code}')
            with part.open('wb') as fh:
                async for chunk in resp.aiter_bytes(_DOWNLOAD_CHUNK):
                    fh.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    _install_state['bytesDone'] = done

    if done != expected_size:
        raise RuntimeError(
            f'{asset["name"]}: downloaded {done} bytes, expected {expected_size} — refusing'
        )
    actual = digest.hexdigest()
    if actual != expected_sha:
        raise RuntimeError(
            f'{asset["name"]}: SHA256 mismatch (got {actual}, pinned {expected_sha}) — refusing'
        )

    _extract_flat(part, target)
    part.unlink(missing_ok=True)


def _extract_flat(zip_path: Path, target: Path) -> None:
    """Extract, stripping one common top-level folder if the zip has one.

    Release zips are mostly flat, but the cudart bundles carry their own
    folder; flattening both into the variant dir keeps ``llama-server`` and
    the CUDA DLLs side by side, which is how the loader finds them.
    """
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if not n.endswith('/')]
        if not names:
            raise RuntimeError(f'{zip_path.name}: empty archive')
        prefix = _common_prefix(names)
        resolved_target = target.resolve()
        for name in names:
            rel = name[len(prefix):] if prefix else name
            out = (target / rel).resolve()
            if not str(out).startswith(str(resolved_target)):
                raise RuntimeError(f'{zip_path.name}: refusing zip-slip member {name!r}')
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, out.open('wb') as dst:
                shutil.copyfileobj(src, dst)


def _common_prefix(names: list[str]) -> str:
    first = names[0].split('/', 1)
    if len(first) < 2:
        return ''
    candidate = first[0] + '/'
    return candidate if all(n.startswith(candidate) for n in names) else ''


def _cleanup_temp() -> None:
    for leftover in _temp_dir().glob('*.part'):
        leftover.unlink(missing_ok=True)


# ── serving GGUFs ──────────────────────────────────────────────────────────


def list_servers() -> dict[str, Any]:
    """Running llama-server instances (read from disk; one process per row)."""
    return {'servers': _load_servers()}


def _load_servers() -> list[dict[str, Any]]:
    p = _servers_file()
    if not p.exists():
        return []
    try:
        rows = json.loads(p.read_text('utf-8'))
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    except Exception:  # noqa: BLE001
        return []


def _save_servers(rows: list[dict[str, Any]]) -> None:
    runtime_root().mkdir(parents=True, exist_ok=True)
    _servers_file().write_text(json.dumps(rows, indent=2), encoding='utf-8')


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return int(s.getsockname()[1])


async def start_gguf_server(
    gguf_path: str,
    *,
    ctx_size: int = 8192,
) -> dict[str, Any]:
    """Spawn ``llama-server`` for one GGUF and register it under the ONE
    "Local Models" provider (id ``local-models``) — never one provider per
    GGUF file, which littered the roster with unreachable per-port entries.

    One local model is served at a time: a new serve stops the previous
    llama-server and repoints the provider. Returns immediately with the
    instance row (status ``starting``); the background task flips it to
    ``ready`` when /health answers, fetches the model list, and prices the
    models at 0.0. A second start for a GGUF that is already served returns
    the existing row.
    """
    variant = _startup_variant()
    if not variant:
        raise RuntimeError('The llama.cpp runtime is not installed yet — install it first.')
    exe = variant_dir(variant) / 'llama-server.exe'
    if not _is_windows():
        raise RuntimeError('Serving GGUFs currently requires the Windows llama.cpp build.')
    if not exe.exists():
        raise RuntimeError(f'llama-server not found at {exe} — reinstall the runtime.')

    path = Path(gguf_path)
    if not path.is_file():
        raise HTTPishError(f'GGUF not found: {gguf_path}')
    if path.suffix.lower() != '.gguf':
        raise HTTPishError(f'Not a GGUF file: {path.name}')

    rows = _load_servers()
    for existing in rows:
        if str(existing.get('ggufPath', '')).lower() == str(path).lower() and await _pid_alive(
            int(existing.get('pid', 0))
        ):
            return existing  # already served

    # One active local model: serving a different GGUF stops the previous
    # llama-server (it holds the GPU and the provider points at ONE port).
    for existing in rows:
        if await _pid_alive(int(existing.get('pid', 0))):
            await _kill_pid(int(existing.get('pid', 0)))
            logger.info('stopped previous llama-server pid %s for the new serve', existing.get('pid'))
    _save_servers([])

    port = _free_port()
    flags = getattr(asyncio.subprocess, 'CREATE_NO_WINDOW', 0) if hasattr(asyncio.subprocess, 'CREATE_NO_WINDOW') else 0
    proc = await asyncio.create_subprocess_exec(
        str(exe),
        '--host', '127.0.0.1',
        '--port', str(port),
        '--ctx-size', str(max(1024, int(ctx_size))),
        '--model', str(path),
        # llama-server uses the --model PATH as the model id unless an alias
        # is given — a composer entry that reads "C:\Users\…\model.gguf" is
        # not a name anyone picked.
        '--alias', path.stem,
        '--jinja',
        cwd=str(exe.parent),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        creationflags=flags,
    )

    server_id = f'gguf-{port}'
    row: dict[str, Any] = {
        'id': server_id,
        'ggufPath': str(path),
        'name': path.stem,
        'port': port,
        'pid': proc.pid,
        'ctxSize': ctx_size,
        'providerId': None,
        'status': 'starting',
        'startedAt': _now(),
        'lastUsedAt': _now(),
        # Which runtime build serves it — the crash pass records the variant
        # with the failure so a CUDA crash can demote CUDA to Vulkan.
        'variant': variant,
        'restartAttempts': 0,
    }
    rows.append(row)
    _save_servers(rows)
    # Remember the last-served model so wake/login preload can bring it back
    # without asking again — the boot sweep clears the server rows, so this
    # warm record is the only survivor of a restart.
    _save_warm({'ggufPath': str(path), 'ctxSize': int(ctx_size), 'savedAt': _now()})

    asyncio.create_task(_await_server_ready(row, proc))

    from app.services.realtime_bus import emit_invalidate

    emit_invalidate('llama-servers')
    return row


class HTTPishError(RuntimeError):
    """A user-facing failure (becomes a 400 in the router)."""


async def _ctx_fallback_reload(row: dict[str, Any]) -> bool:
    """A context change that failed to load falls back, ONCE, to the previous
    working context length — visibly, with the reason on the realtime bus.

    The fallback is recorded by the row that carries ``previousCtxSize`` (set
    only by :func:`change_server_context`), so an ordinary serve that fails
    keeps its honest ``failed`` row instead of silently retrying.
    """
    prev = int(row.get('previousCtxSize') or 0)
    current = int(row.get('ctxSize') or 0)
    if not prev or prev == current:
        return False
    name = str(row.get('name', ''))
    logger.warning(
        'ctx %s failed to load for %s — falling back to the previous %s', current, name, prev
    )
    _emit_status(
        'ctx-fallback',
        name,
        f'context {current} exceeded what this machine could load — reloading with the previous {prev}',
    )
    await start_gguf_server(str(row.get('ggufPath', '')), ctx_size=prev)
    return True


async def _await_server_ready(
    row: dict[str, Any],
    proc: asyncio.subprocess.Process,
) -> None:
    """Poll /health until the model is loaded, then register + price."""
    url = f'http://127.0.0.1:{row["port"]}/health'
    deadline = asyncio.get_running_loop().time() + _SERVER_START_TIMEOUT_S
    ready = False
    # "Not up yet" is the common case and stays quiet, but this loop used to
    # swallow EVERY failure silently — a poisoned NO_PROXY sat here burning the
    # full 900 s while llama-server was healthy, and the only trace was a
    # status that never left "starting". The FIRST occurrence of each failure
    # class is a warning, so a hang that never resolves still has one line in
    # the log at the default level; repeats drop to debug at a slow cadence so
    # a 900-second poll loop cannot flood it.
    lastPollError: str = ''
    pollsSinceLog = 0
    while asyncio.get_running_loop().time() < deadline:
        if proc.returncode is not None:
            row['status'] = 'failed'
            row['detail'] = f'llama-server exited with code {proc.returncode} while loading'
            if await _ctx_fallback_reload(row):
                return
            _persist_row(row)
            return
        try:
            async with http_env.client(timeout=2.0) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                # A 200 from a server that was killed a moment ago must not
                # resurrect its row as 'ready' (stop_gguf_server already
                # dropped it): re-verify the process before declaring ready.
                if proc.returncode is not None or not await _pid_alive(proc.pid):
                    row['status'] = 'failed'
                    row['detail'] = 'llama-server stopped while loading'
                    _persist_row(row)
                    return
                ready = True
                break
        except Exception as exc:  # noqa: BLE001 — not up yet
            kind = type(exc).__name__
            pollsSinceLog += 1
            if kind != lastPollError:
                logger.warning(
                    'health poll for the local model: %s (server not answering yet)', kind
                )
                lastPollError = kind
                pollsSinceLog = 0
            elif pollsSinceLog >= 30:
                logger.debug('health poll: %s persists (%s)', kind, exc)
                pollsSinceLog = 0
        await asyncio.sleep(_HEALTH_POLL_S)

    if not ready:
        row['status'] = 'failed'
        row['detail'] = 'model did not finish loading in time — try again or pick a smaller context'
        if await _ctx_fallback_reload(row):
            return
        _persist_row(row)
        return

    row['status'] = 'ready'
    _persist_row(row)
    provider_id = await _upsert_local_provider(row)
    row['providerId'] = provider_id
    _persist_row(row)
    logger.info('GGUF server ready on port %s for %s (provider %s)', row['port'], row['name'], provider_id)


def _persist_row(row: dict[str, Any]) -> None:
    """Update a row IN PLACE in servers.json.

    The append branch this used to have was a resurrect bug: the ready-task's
    second persist (after ``_upsert_local_provider``) ran a beat behind
    ``stop_gguf_server``'s save of the remaining rows, found the id gone, and
    re-appended the just-killed server as a phantom 'ready' row — the exact
    stale-ready state the crash pass exists to catch. A row whose id is no
    longer on disk was STOPPED while this async task was finishing; the stop
    wins, and the update is dropped.
    """
    rows = _load_servers()
    for i, r in enumerate(rows):
        if r.get('id') == row['id']:
            rows[i] = row
            _save_servers(rows)
            return
    logger.debug(
        'persist: row %s vanished from the store (stopped) — not resurrecting', row.get('id')
    )


async def _upsert_local_provider(row: dict[str, Any]) -> str | None:
    """Upsert the ONE "Local Models" provider against the served GGUF.

    The provider is a fixed record (id ``local-models``): serving updates its
    baseUrl to the live port, enables it, refreshes the model list, and
    prices the models at 0.0 — a local host charges nothing, and 0.0 is a
    price, not an absence. Legacy per-GGUF entries ("GGUF · …") are disabled
    here and by the data-dir ladder, so a roster that predates this design
    cleans itself up on the next serve.
    """
    from app.json_narrowing import as_list
    from app.routers.providers import _discoverProviderModels
    from app.services import config_service, model_service
    from app.services.realtime_bus import emit_invalidate

    base_url = f'http://127.0.0.1:{row["port"]}/v1'
    store = config_service.getProvidersStore()
    providers = [e for e in as_list(store.get('providers'), []) if isinstance(e, dict)]

    entry = next((e for e in providers if str(e.get('id', '')) == _LOCAL_PROVIDER_ID), None)
    if entry is None:
        entry = {
            'id': _LOCAL_PROVIDER_ID,
            'name': _LOCAL_PROVIDER_NAME,
            'baseUrl': base_url,
            'apiFormat': 'openaiChat',
            'apiKey': '',
            'enabled': True,
            'autoFetch': True,
            'models': [],
        }
        providers.append(entry)
    else:
        legacy = str(entry.get('name', ''))
        if legacy and legacy != _LOCAL_PROVIDER_NAME:
            entry['name'] = _LOCAL_PROVIDER_NAME
    entry['baseUrl'] = base_url
    entry['enabled'] = True

    # Belt alongside the ladder: disable the old per-GGUF entries.
    for e in providers:
        if str(e.get('name', '')).startswith(_SERVER_NAME_PREFIX) and str(e.get('id', '')) != _LOCAL_PROVIDER_ID and e.get('enabled', True):
            e['enabled'] = False

    store['providers'] = providers
    config_service.saveProvidersStore(store)
    model_service.invalidate_cache()

    await _discoverProviderModels(_LOCAL_PROVIDER_ID, persist=True)
    _price_local_models(_LOCAL_PROVIDER_ID, int(row.get('ctxSize', 8192)))
    emit_invalidate('aggregated-models', 'ws-providers', 'provider-availability', 'providers')
    return _LOCAL_PROVIDER_ID


def _price_local_models(provider_id: str, ctx_size: int) -> None:  # noqa: ARG001 — id kept for symmetry
    """Stamp fetched models with price 0.0 and the served context window."""
    from app.json_narrowing import as_list
    from app.services import config_service, model_service

    store = config_service.getProvidersStore()
    for entry in as_list(store.get('providers'), []):
        if isinstance(entry, dict) and str(entry.get('id', '')) == provider_id:
            models = entry.get('models')
            if isinstance(models, list):
                for m in models:
                    if isinstance(m, dict):
                        m['priceInPerM'] = 0.0
                        m['priceOutPerM'] = 0.0
                        if ctx_size >= 1024:
                            m['contextWindow'] = ctx_size
            entry['models'] = models if isinstance(models, list) else []
            config_service.saveProvidersStore(store)
            model_service.invalidate_cache()
            return


async def stop_gguf_server(server_id: str) -> dict[str, Any]:
    """Stop one llama-server (process tree on Windows) and drop its row.

    The provider entry stays — it is the user's config; availability
    machinery already shows a stopped host as unreachable.
    """
    rows = _load_servers()
    target = next((r for r in rows if r.get('id') == server_id), None)
    if not target:
        raise HTTPishError(f'No such local server: {server_id}')
    pid = int(target.get('pid', 0))
    await _kill_pid(pid)
    remaining = [r for r in rows if r.get('id') != server_id]
    _save_servers(remaining)

    # No local model is serving: the Local Models provider goes unavailable
    # rather than advertising a dead port.
    if not remaining:
        from app.json_narrowing import as_list
        from app.services import config_service, model_service

        store = config_service.getProvidersStore()
        for entry in as_list(store.get('providers'), []):
            if isinstance(entry, dict) and str(entry.get('id', '')) == _LOCAL_PROVIDER_ID and entry.get('enabled', True):
                entry['enabled'] = False
                config_service.saveProvidersStore(store)
                model_service.invalidate_cache()
                break

    from app.services.realtime_bus import emit_invalidate

    emit_invalidate('llama-servers', 'aggregated-models', 'ws-providers', 'provider-availability')
    return {'stopped': server_id}


# ── context length: the user-set window of a served model ──────────────────

_CTX_MIN_TOKENS = 1024
_CTX_MAX_TOKENS = 131072
_CTX_WARN_ABOVE = 32768


async def change_server_context(server_id: str, ctx_size: int) -> dict[str, Any]:
    """Change a served local model's context length: stop it, relaunch
    llama-server with the new ``--ctx-size``, and let the ordinary serve path
    repoint the provider, re-price ``contextWindow``, and refresh the warm
    record (so wake/login preload and crash restarts keep the new length).

    A generation in flight is never interrupted — the change refuses while the
    server is busy and the caller retries after the turn. If the model cannot
    load at the new size (typically out of memory), the ready-watcher falls
    back once to the previous working length and says so on the realtime bus.
    """
    try:
        ctx = int(ctx_size)
    except (TypeError, ValueError) as exc:
        raise HTTPishError('context size must be a number of tokens') from exc
    if ctx < _CTX_MIN_TOKENS or ctx > _CTX_MAX_TOKENS:
        raise HTTPishError(
            f'context size must be between {_CTX_MIN_TOKENS} and {_CTX_MAX_TOKENS} tokens'
        )

    rows = _load_servers()
    row = next((r for r in rows if r.get('id') == server_id), None)
    if not row:
        raise HTTPishError(f'No such local server: {server_id}')
    if str(row.get('status', '')) != 'ready':
        raise HTTPishError(
            f'{row.get("name", server_id)} is {row.get("status", "unknown")} — '
            'wait for it to finish loading, then change the context'
        )
    if server_busy(int(row.get('port', 0))):
        raise HTTPishError(
            'a generation is in flight on this model — wait for the current '
            'reply to finish, then change the context'
        )

    previous = int(row.get('ctxSize', 8192) or 8192)
    if ctx == previous:
        return {'row': row, 'changed': False, 'previousCtxSize': previous}

    await stop_gguf_server(server_id)
    new_row = await start_gguf_server(str(row.get('ggufPath', '')), ctx_size=ctx)
    new_row['previousCtxSize'] = previous
    _persist_row(new_row)
    _emit_status(
        'ctx-changing',
        str(new_row.get('name', '')),
        f'context {previous} → {ctx} — loading model…',
    )
    return {'row': new_row, 'changed': True, 'previousCtxSize': previous}


def context_warning_for(ctx_size: int) -> str:
    """The trained-window caveat, kept in one place for the API and the tool."""
    if ctx_size > _CTX_WARN_ABOVE:
        return (
            f'{ctx_size} tokens is above most small models\' trained context '
            'window — quality may degrade past it, and memory use grows with '
            'the window'
        )
    return ''


async def shutdown_all() -> None:
    """App-shutdown hook: kill every llama-server we spawned."""
    for row in _load_servers():
        try:
            await _kill_pid(int(row.get('pid', 0)))
        except Exception:  # noqa: BLE001 — shutdown must not raise
            pass
    _save_servers([])


async def sweep_orphans() -> None:
    """Boot hook: llama-servers from a previous process that never stopped."""
    rows = _load_servers()
    kept: list[dict[str, Any]] = []
    for row in rows:
        pid = int(row.get('pid', 0))
        if await _pid_alive(pid):
            await _kill_pid(pid)
            logger.info('swept orphan llama-server pid %s (port %s)', pid, row.get('port'))
        # Rows are dropped either way: a live pid is killed, a dead one is stale.
    _save_servers(kept)


def _local_provider_row() -> dict[str, Any]:
    """The ONE LocalModels provider row, read live from the store."""
    try:
        from app.services import config_service

        store = config_service.getProvidersStore()
        for entry in as_list(store.get('providers'), []) if isinstance(store, dict) else []:
            if isinstance(entry, dict) and str(entry.get('id', '')) == _LOCAL_PROVIDER_ID:
                return entry
    except Exception:  # noqa: BLE001 — a failed read is "no row"
        pass
    return {}


async def ensure_local_ready(timeout_s: float = 120.0) -> bool:
    """Wait until the LocalModels provider points at a live server.

    The post-release path: a released model left the provider disabled and
    its port dead. The next message must RELOAD — with a visible 'loading
    model' state — instead of dying on connection refused. This waits on the
    provider row's LIVE baseUrl (a reload repoints the port, so polling the
    caller's stale URL would wait on a port that never comes back), fires the
    warm preload once, and reports every state change on the realtime bus.
    """
    announced = ''
    deadline = time.monotonic() + max(5.0, timeout_s)
    preload_started = False
    # A local provider can be down for two very different reasons: the model
    # was released (a warm record exists — reload it) or it was never served
    # (no warm record — waiting 120s to fail is a hang, not a recovery; the
    # normal provider error is the honest answer there).
    if not _load_warm().get('ggufPath'):
        return False
    while time.monotonic() < deadline:
        row = _local_provider_row()
        port = port_from_base_url(row.get('baseUrl'))
        enabled = row.get('enabled', True) is not False
        if port and enabled and await _port_healthy(port):
            if announced:
                _emit_status('ready', str(row.get('name', '')), 'local model ready')
            return True
        if not preload_started:
            preload_started = True
            announced = 'loading'
            _emit_status(
                'loading',
                str(row.get('name', '')) or 'local model',
                'loading the local model — the first message after a release waits for it',
            )
            try:
                asyncio.create_task(preload_warm())
            except Exception:  # noqa: BLE001 — the wait loop still polls the row
                logger.debug('ensure_local_ready could not start the preload', exc_info=True)
        await asyncio.sleep(1.0)
    _emit_status('failed', 'local model', 'the local model did not load in time — try again, or check Model settings')
    return False


async def _port_healthy(port: int, timeout: float = 2.0) -> bool:
    """/health answers 200 — the difference between a live pid and a live server."""
    if port <= 0:
        return False
    try:
        async with http_env.client(timeout=timeout) as client:
            resp = await client.get(f'http://127.0.0.1:{port}/health')
        return resp.status_code == 200
    except Exception:  # noqa: BLE001 — connection refused IS the crash signal
        return False


def _startup_variant() -> str | None:
    """The installed variant to serve from.

    A CUDA crash demotes CUDA: the warm record carries ``preferVariant``, and
    when that variant is installed it wins over the detection order. This is
    the automatic "fall back to the stable runtime after a crash" half.
    """
    warm = _load_warm()
    preferred = str(warm.get('preferVariant', '') or '')
    if preferred:
        info = _read_marker(preferred)
        if info and info.get('tag') == manifest.MANIFEST_TAG:
            return preferred
    return installed_variant()


async def reconcile_servers(*, allow_restart: bool = True) -> dict[str, Any]:
    """Crash pass: a 'ready' row whose process or /health is gone is FAILED.

    The stale-ready bug: ``_await_server_ready`` marks failures only while a
    server is STARTING; a mid-generation crash left a 'ready' row pointing at
    a dead port, and every turn against it died on connection refused. This
    pass re-checks liveness (pid + /health), marks the row failed with the
    GPU and driver recorded for the report, restarts ONCE — preferring the
    stable Vulkan runtime after a CUDA crash — and says what happened on the
    realtime bus so the UI shows a visible state instead of a silent error.
    """
    from app.services.realtime_bus import emit_invalidate

    checked = 0
    failed = 0
    restarted = 0
    rows_changed = False
    for row in _load_servers():
        if str(row.get('status', '')) != 'ready':
            continue
        checked += 1
        pid = int(row.get('pid', 0))
        port = int(row.get('port', 0))
        alive = await _pid_alive(pid)
        if alive and await _port_healthy(port):
            continue
        failed += 1
        gpu = await detect_gpu()
        variant = str(row.get('variant', '') or '')
        row['status'] = 'failed'
        row['crashedAt'] = _now()
        row['gpu'] = gpu.get('gpuName')
        row['driver'] = gpu.get('driverVersion')
        row['detail'] = (
            'llama-server crashed mid-run'
            + (f' (variant {variant})' if variant else '')
            + (' — process gone' if not alive else ' — health check failed')
        )
        _persist_row(row)
        rows_changed = True
        _emit_status('crashed', str(row.get('name', '')), str(row['detail']))
        logger.warning('local server %s crashed: %s', row.get('id'), row['detail'])
        # A CUDA crash on this machine is a mark against the CUDA build: the
        # next (re)start prefers the stable Vulkan runtime automatically.
        if variant.startswith('cuda'):
            warm = _load_warm()
            if warm.get('preferVariant') != 'vulkan':
                warm['preferVariant'] = 'vulkan'
                warm['preferVariantReason'] = f'cuda crash at {row["crashedAt"]}'
                _save_warm(warm)
        if allow_restart and int(row.get('restartAttempts', 0) or 0) < 1:
            gguf = str(row.get('ggufPath', ''))
            if gguf and Path(gguf).is_file():
                try:
                    row['restartAttempts'] = int(row.get('restartAttempts', 0) or 0) + 1
                    _persist_row(row)
                    _emit_status('restarting', str(row.get('name', '')), 'reloading the local model (one restart)')
                    fresh = await start_gguf_server(gguf, ctx_size=int(row.get('ctxSize', 8192) or 8192))
                    restarted += 1
                    _emit_status('ready', str(fresh.get('name', '')), 'local model back up')
                except Exception as exc:  # noqa: BLE001 — a failed restart is a reported state
                    logger.warning('restart after crash failed for %s: %s', gguf, exc)
                    _emit_status('failed', str(row.get('name', '')), f'restart failed: {exc}')
    if rows_changed:
        emit_invalidate('llama-servers', 'aggregated-models', 'provider-availability')
    return {'checked': checked, 'failed': failed, 'restarted': restarted}


# ── keep-warm: preload at wake/login, release on idle/battery/load ──────────

def _warm_file() -> Path:
    return runtime_root() / 'warm.json'


def _load_warm() -> dict[str, Any]:
    p = _warm_file()
    if not p.exists():
        return {}
    try:
        rec = json.loads(p.read_text('utf-8'))
        return rec if isinstance(rec, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_warm(rec: dict[str, Any]) -> None:
    runtime_root().mkdir(parents=True, exist_ok=True)
    _warm_file().write_text(json.dumps(rec, indent=2), encoding='utf-8')


def _ts_age_seconds(raw: object) -> float | None:
    """Age of an ISO timestamp in seconds; None when unparsable."""
    import datetime

    try:
        stamp = datetime.datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    now = datetime.datetime.now(datetime.UTC)
    return max(0.0, (now - stamp).total_seconds())


def touch_server(port: int) -> None:
    """Mark a local server as used now — the idle release measures from the
    last real use, not from when the process happened to start."""
    rows = _load_servers()
    changed = False
    for row in rows:
        if int(row.get('port', 0)) == port and row.get('lastUsedAt') != _now():
            row['lastUsedAt'] = _now()
            changed = True
    if changed:
        _save_servers(rows)


def touch_loopback_base_url(base_url: object) -> None:
    """Touch from a provider ``baseUrl`` like ``http://127.0.0.1:<port>/v1``."""
    try:
        port = int(str(base_url).rstrip('/').rsplit(':', 1)[-1].split('/')[0])
    except (ValueError, IndexError):
        return
    touch_server(port)


# ── in-flight generations: the release must never kill a running turn ───────

_BUSY_UNTIL: dict[int, float] = {}
_BUSY_DEFAULT_TTL_S = 180.0


_LAST_STATUS_EMITTED: dict[tuple[str, str, str], float] = {}
_LAST_STATUS_TTL_S = 90.0


def _emit_status(status: str, name: str, detail: str) -> None:
    """A user-visible local-model lifecycle event on the realtime bus.

    The release pass runs on a schedule, so an unchanged condition re-emits the
    same event every tick — six identical "release waits" toasts stacked during
    one wedged turn (found live, 2026-11-17). A repeat of the SAME
    (status, name, detail) within the TTL is dropped; a real state change
    always emits.
    """
    key = (status, name, detail)
    now = time.monotonic()
    last = _LAST_STATUS_EMITTED.get(key, 0.0)
    if now - last < _LAST_STATUS_TTL_S:
        return
    _LAST_STATUS_EMITTED[key] = now
    from app.services.realtime_bus import emit_realtime

    emit_realtime('local-model-status', status=status, name=name, detail=detail)


def mark_server_busy(port: int, ttl_s: float = _BUSY_DEFAULT_TTL_S) -> None:
    """A generation is in flight on this local server: the release waits.

    Called by the provider client when a request targets a loopback local
    server. The TTL is a safety net — a client that dies mid-call must not
    pin the model forever — and long generations renew it per request.
    """
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        return
    if port_i > 0:
        _BUSY_UNTIL[port_i] = time.monotonic() + max(5.0, float(ttl_s))


def release_server_busy(port: int) -> None:
    """The generation finished (or the client gave up): allow release again."""
    try:
        _BUSY_UNTIL.pop(int(port), None)
    except (TypeError, ValueError):
        pass


def server_busy(port: int) -> bool:
    """True while a generation is believed in flight on this port."""
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        return False
    deadline = _BUSY_UNTIL.get(port_i, 0.0)
    if deadline and time.monotonic() >= deadline:
        _BUSY_UNTIL.pop(port_i, None)
        return False
    return deadline > 0.0


def port_from_base_url(base_url: object) -> int:
    """The loopback port in a ``http://127.0.0.1:<port>/v1`` URL, else 0."""
    try:
        text = str(base_url or '')
        if '127.0.0.1' not in text and 'localhost' not in text:
            return 0
        return int(text.rstrip('/').rsplit(':', 1)[-1].split('/')[0])
    except (ValueError, IndexError):
        return 0


def on_battery() -> bool:
    """True when the machine runs on battery (Windows GetSystemPowerStatus).
    Unknown states (desktops, non-Windows) count as NOT on battery — the
    release must never fire on a machine that cannot report power."""
    if not _is_windows():
        return False
    import ctypes

    class _POWER_STATUS(ctypes.Structure):
        _fields_ = [
            ('ACLineStatus', ctypes.c_byte),
            ('BatteryFlag', ctypes.c_byte),
            ('BatteryLifePercent', ctypes.c_byte),
            ('Reserved0', ctypes.c_byte),
            ('BatteryLifeTime', ctypes.c_ulong),
            ('BatteryFullLifeTime', ctypes.c_ulong),
        ]

    status = _POWER_STATUS()
    try:
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return False
    except Exception:  # noqa: BLE001
        return False
    return status.ACLineStatus == 0  # 0 = on battery, 1 = AC, 255 = unknown


def memory_pressure() -> int:
    """Windows memory load percent (0-100); -1 when it cannot be read."""
    if not _is_windows():
        return -1
    import ctypes

    class _MEM_STATUS(ctypes.Structure):
        _fields_ = [
            ('dwLength', ctypes.c_ulong),
            ('dwMemoryLoad', ctypes.c_ulong),
            ('ullTotalPhys', ctypes.c_ulonglong),
            ('ullAvailPhys', ctypes.c_ulonglong),
            ('ullTotalPageFile', ctypes.c_ulonglong),
            ('ullAvailPageFile', ctypes.c_ulonglong),
            ('ullTotalVirtual', ctypes.c_ulonglong),
            ('ullAvailVirtual', ctypes.c_ulonglong),
            ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
        ]

    status = _MEM_STATUS()
    status.dwLength = ctypes.sizeof(_MEM_STATUS)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return -1
    except Exception:  # noqa: BLE001
        return -1
    return int(status.dwMemoryLoad)


async def preload_warm() -> dict[str, Any]:
    """Wake/login preload: bring back the last-served local model so the
    selected model answers warm instead of cold. Best-effort — a failure
    never blocks the wake path."""
    rec = _load_warm()
    path = str(rec.get('ggufPath', ''))
    if not path or not Path(path).is_file():
        return {'preloaded': False, 'reason': 'no remembered local model'}
    for existing in _load_servers():
        if str(existing.get('ggufPath', '')).lower() == path.lower() and await _pid_alive(
            int(existing.get('pid', 0))
        ):
            touch_server(int(existing.get('port', 0)))
            return {'preloaded': False, 'reason': 'already warm'}
    try:
        row = await start_gguf_server(path, ctx_size=int(rec.get('ctxSize', 8192)))
        logger.info('warm preload started local model %s (port %s)', row.get('name'), row.get('port'))
        return {'preloaded': True, 'server': row.get('id'), 'port': row.get('port')}
    except Exception as exc:  # noqa: BLE001 — preload is best-effort
        logger.warning('warm preload failed for %s: %s', path, exc)
        return {'preloaded': False, 'reason': str(exc)[:200]}


async def release_warm(
    *,
    idle_minutes: int,
    release_on_battery: bool = True,
    memory_load_percent: int = 90,
) -> list[str]:
    """The release half of keep-warm: stop served local models that are idle
    past the TTL, when the machine runs on battery, or under memory pressure.
    Returns the human-readable names of what was released.

    A server with a generation in flight is NEVER released — the pass defers
    it and says so on the realtime bus, so the user sees why the model is
    still holding memory instead of watching their answer die mid-stream.
    """
    stopped: list[str] = []
    for row in _load_servers():
        if str(row.get('status', '')) != 'ready':
            continue
        port = int(row.get('port', 0))
        if server_busy(port):
            _emit_status(
                'release-deferred',
                str(row.get('name', '')),
                'a generation is in flight — the release waits for it to finish',
            )
            continue
        # A CHAT TURN active on the local provider also defers the release:
        # the busy mark above only covers a request in flight, and the gaps
        # between a turn's requests (tool execution, retry backoff) are where
        # a memory-pressure release killed a fresh server mid-turn (found
        # live, 2026-11-16 — the turn then died on its retries).
        try:
            from app.services.workbench.workbench import anyActiveTurnOnProvider

            if anyActiveTurnOnProvider({'local-models', 'localmodels'}):
                _emit_status(
                    'release-deferred',
                    str(row.get('name', '')),
                    'a chat turn is active on this model — the release waits for it to finish',
                )
                continue
        except (ImportError, AttributeError, RuntimeError):
            pass
        reason = ''
        if release_on_battery and on_battery():
            reason = 'on battery'
        else:
            load = memory_pressure()
            if load >= memory_load_percent:
                reason = f'memory load {load}%'
            else:
                age = _ts_age_seconds(row.get('lastUsedAt') or row.get('startedAt'))
                if age is not None and age >= idle_minutes * 60:
                    reason = f'idle {int(age / 60)} min'
        if not reason:
            continue
        try:
            await stop_gguf_server(str(row.get('id', '')))
            stopped.append(f"{row.get('name')} ({reason})")
            _emit_status(
                'released',
                str(row.get('name', '')),
                f'released: {reason} — the next message reloads it',
            )
            logger.info('released warm local model %s: %s', row.get('name'), reason)
        except Exception:  # noqa: BLE001
            logger.debug('warm release failed for %s', row.get('id'), exc_info=True)
    return stopped


async def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _is_windows():
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        try:
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        except AttributeError:
            return False
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


async def _kill_pid(pid: int) -> None:
    if pid <= 0:
        return
    if _is_windows():
        await asyncio.create_subprocess_exec(
            'taskkill', '/PID', str(pid), '/F', '/T',
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(asyncio.subprocess, 'CREATE_NO_WINDOW', 0),
        )
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
