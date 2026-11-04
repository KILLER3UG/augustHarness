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
import zipfile
from pathlib import Path
from typing import Any

import httpx

from app.lib.paths import dataDir
from app.services import llama_runtime_manifest as manifest

logger = logging.getLogger(__name__)

_RUNTIME_ROOT = 'runtimes'
_RUNTIME_DIR = 'llama.cpp'
_TEMP_DIR = 'tmp'
_MARKER_FILE = 'installed.json'
_SERVERS_FILE = 'servers.json'
_SERVER_NAME_PREFIX = 'GGUF · '
# The ONE provider all served GGUFs live under (user-facing name in the
# picker's provider group).
_LOCAL_PROVIDER_ID = 'local-models'
_LOCAL_PROVIDER_NAME = 'Local Models'

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
        return {'nvidia': False, 'cudaVersion': None, 'gpuName': None}
    flags = getattr(asyncio.subprocess, 'CREATE_NO_WINDOW', 0) if hasattr(asyncio.subprocess, 'CREATE_NO_WINDOW') else 0
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            '--query-gpu=name,driver_version',
            '--format=csv,noheader',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=flags,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            return {'nvidia': False, 'cudaVersion': None, 'gpuName': None}
        if proc.returncode != 0:
            return {'nvidia': False, 'cudaVersion': None, 'gpuName': None}
        first = out.decode('utf-8', 'replace').strip().splitlines()
        gpu_name = first[0].split(',')[0].strip() if first and first[0].strip() else None
    except Exception:  # noqa: BLE001 — a broken driver install is "no NVIDIA"
        return {'nvidia': False, 'cudaVersion': None, 'gpuName': None}

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
    return {'nvidia': True, 'cudaVersion': cuda_version, 'gpuName': gpu_name}


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
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
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
    variant = installed_variant()
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
    }
    rows.append(row)
    _save_servers(rows)

    asyncio.create_task(_await_server_ready(row, proc))

    from app.services.realtime_bus import emit_invalidate

    emit_invalidate('llama-servers')
    return row


class HTTPishError(RuntimeError):
    """A user-facing failure (becomes a 400 in the router)."""


async def _await_server_ready(
    row: dict[str, Any],
    proc: asyncio.subprocess.Process,
) -> None:
    """Poll /health until the model is loaded, then register + price."""
    url = f'http://127.0.0.1:{row["port"]}/health'
    deadline = asyncio.get_running_loop().time() + _SERVER_START_TIMEOUT_S
    ready = False
    while asyncio.get_running_loop().time() < deadline:
        if proc.returncode is not None:
            row['status'] = 'failed'
            row['detail'] = f'llama-server exited with code {proc.returncode} while loading'
            _persist_row(row)
            return
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                ready = True
                break
        except Exception:  # noqa: BLE001 — not up yet
            pass
        await asyncio.sleep(_HEALTH_POLL_S)

    if not ready:
        row['status'] = 'failed'
        row['detail'] = 'model did not finish loading in time — try again or pick a smaller context'
        _persist_row(row)
        return

    row['status'] = 'ready'
    _persist_row(row)
    provider_id = await _upsert_local_provider(row)
    row['providerId'] = provider_id
    _persist_row(row)
    logger.info('GGUF server ready on port %s for %s (provider %s)', row['port'], row['name'], provider_id)


def _persist_row(row: dict[str, Any]) -> None:
    rows = _load_servers()
    for i, r in enumerate(rows):
        if r.get('id') == row['id']:
            rows[i] = row
            break
    else:
        rows.append(row)
    _save_servers(rows)


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
