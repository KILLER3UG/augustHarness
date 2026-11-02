"""ComfyUI connection + image generation.

A ComfyUI install (the user's own, usually ``http://127.0.0.1:8188``) is
the image engine; August holds the connection settings, the txt2img
workflow, and the ``generate_image`` tool. The tool is REGISTERED ONLY
WHEN a connection is enabled — a knob that always refuses is a lie, so a
disabled ComfyUI is not advertised to the model at all. Toggling the
setting registers/unregisters and the registry's generation bump drops
every cached tool-definition list.

Generation follows ComfyUI's own API: POST the workflow graph to
``/prompt``, poll ``/history/{id}``, fetch bytes from ``/view``. The
default graph is a plain txt2img; a pasted custom API-format workflow
wins when present, with the same ``%MARKER%`` substitution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from app.json_narrowing import as_bool, as_dict, as_list, as_str
from app.services import config_service

logger = logging.getLogger(__name__)

DEFAULTS: dict[str, object] = {
    'enabled': False,
    'baseUrl': 'http://127.0.0.1:8188',
    'workflowJson': '',
    'defaultCheckpoint': '',
}

_TIMEOUT = httpx.Timeout(10.0)
_GENERATE_TIMEOUT_S = 300
_POLL_INTERVAL_S = 1.0

_TOOL_NAME = 'generate_image'


def get_config() -> dict[str, object]:
    cfg = config_service.getConfig()
    user = as_dict(as_dict(cfg.get('auxiliary'), {}).get('comfyui'), {})
    out = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        if key not in user:
            continue
        if isinstance(default, bool):
            out[key] = as_bool(user.get(key), default)
        else:
            out[key] = as_str(user.get(key), as_str(default))
    return out


def validate_patch(patch: dict[str, object]) -> tuple[bool, str]:
    for key, value in patch.items():
        if key not in DEFAULTS:
            return False, f'unknown field: {key!r}'
        default = DEFAULTS[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                return False, f'{key!r} must be a boolean'
        elif not isinstance(value, str):
            return False, f'{key!r} must be a string'
    # A pasted workflow must at least parse — a workflow that breaks the
    # serializer should fail at save time, not mid-generation.
    wf = str(patch.get('workflowJson', '')).strip() if 'workflowJson' in patch else ''
    if wf:
        try:
            parsed = json.loads(wf)
        except ValueError as exc:
            return False, f'workflowJson is not valid JSON: {exc}'
        if not isinstance(parsed, dict) or not parsed:
            return False, 'workflowJson must be a non-empty JSON object (ComfyUI API format)'
    return True, ''


def update_config(patch: dict[str, object]) -> dict[str, object]:
    ok, err = validate_patch(patch)
    if not ok:
        raise ValueError(err)
    cfg = config_service.getConfig()
    aux = cfg.get('auxiliary')
    if not isinstance(aux, dict):
        aux = {}
        cfg['auxiliary'] = aux
    block = aux.get('comfyui')
    if not isinstance(block, dict):
        block = {}
        aux['comfyui'] = block
    for key, value in patch.items():
        block[key] = value
    config_service.saveConfig(cfg)
    apply_tool_registration()
    return get_config()


def get_config_with_status() -> dict[str, object]:
    c = get_config()
    return {
        **c,
        'toolAdvertised': is_tool_registered(),
        'note': (
            'Set the ComfyUI URL (default 127.0.0.1:8188). When enabled, the '
            'generate_image tool is offered in chat; a custom API-format '
            'workflow (from ComfyUI "Save (API format)") replaces the '
            'built-in txt2img graph, with %PROMPT% / %NEGATIVE% / '
            '%CHECKPOINT% / %WIDTH% / %HEIGHT% / %STEPS% / %CFG% / %SEED% '
            'markers substituted.'
        ),
    }


# ── tool registration (advertise only when usable) ─────────────────────────


def is_tool_registered() -> bool:
    from app.services.tool_registry import get

    return get(_TOOL_NAME) is not None


def apply_tool_registration() -> None:
    """Register or withdraw ``generate_image`` to match the connection."""
    from app.services import tool_registry

    if as_bool(get_config().get('enabled'), False):
        if not is_tool_registered():
            _register_tool()
    else:
        tool_registry.unregister(_TOOL_NAME)


def _register_tool() -> None:
    from app.services.tool_registry import register

    register(
        _TOOL_NAME,
        'Generate an image with the local ComfyUI installation (txt2img). '
        'Returns the path of the written PNG in the workspace. Use when the '
        'user asks to create or generate an image.',
        _tool_generate,
        {
            'type': 'object',
            'properties': {
                'prompt': {'type': 'string', 'description': 'What to draw'},
                'negative_prompt': {'type': 'string', 'description': 'What to avoid'},
                'checkpoint': {'type': 'string', 'description': 'SD checkpoint name; default is the configured one or ComfyUI\'s first'},
                'width': {'type': 'integer', 'description': 'Pixels (64-multiple), default 1024'},
                'height': {'type': 'integer', 'description': 'Pixels (64-multiple), default 1024'},
                'steps': {'type': 'integer', 'description': 'Sampler steps, default 25'},
                'cfg': {'type': 'number', 'description': 'CFG scale, default 7.0'},
                'seed': {'type': 'integer', 'description': 'Seed; omit for a random one'},
                'path': {'type': 'string', 'description': 'Output PNG path in the workspace, e.g. images/idea.png'},
            },
            'required': ['prompt'],
        },
        keywords=['image', 'draw', 'picture', 'generate image', 'comfyui', 'txt2img', 'paint'],
    )


# ── ComfyUI HTTP ───────────────────────────────────────────────────────────


def _root(cfg: dict[str, object] | None = None) -> str:
    base = as_str((cfg or get_config()).get('baseUrl'), 'http://127.0.0.1:8188').strip().rstrip('/')
    return base or 'http://127.0.0.1:8188'


async def health() -> dict[str, Any]:
    """Connection test: ComfyUI answers /system_stats with a system block."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f'{_root()}/system_stats')
        if resp.status_code != 200:
            return {'ok': False, 'error': f'HTTP {resp.status_code} from {_root()}/system_stats'}
        data = resp.json()
        system = as_dict(data.get('system'), {})
        devices_raw = data.get('devices', [])
        if isinstance(devices_raw, dict):
            devices_iter = list(devices_raw.values())
        elif isinstance(devices_raw, list):
            devices_iter = devices_raw
        else:
            devices_iter = []
        devices = []
        for d in devices_iter:
            dd = as_dict(d, {})
            devices.append(
                {
                    'name': str(dd.get('name', '')),
                    'type': str(dd.get('type', '')),
                    'vramTotal': dd.get('vram_total', 0),
                }
            )
        return {
            'ok': isinstance(data.get('system'), dict),
            'comfyuiVersion': str(system.get('comfyui_version', '')),
            'pythonVersion': str(system.get('python_version', '')),
            'devices': devices,
        }
    except Exception as exc:  # noqa: BLE001 — a down ComfyUI is a status, not a crash
        return {'ok': False, 'error': str(exc)}


async def checkpoints() -> dict[str, Any]:
    """Checkpoint names ComfyUI can load (from /object_info)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(f'{_root()}/object_info/CheckpointLoaderSimple')
    if resp.status_code != 200:
        raise RuntimeError(f'HTTP {resp.status_code} from /object_info/CheckpointLoaderSimple')
    info = resp.json()
    node = as_dict(info.get('CheckpointLoaderSimple'), {})
    required = as_dict(node.get('input'), {}).get('required')
    names: list[str] = []
    if isinstance(required, dict):
        ckpt = required.get('ckpt_name')
        if isinstance(ckpt, list) and ckpt and isinstance(ckpt[0], list):
            names = [str(n) for n in ckpt[0]]
    elif isinstance(required, list):
        for entry in required:
            if isinstance(entry, list) and entry and entry[0] == 'ckpt_name' and len(entry) > 1:
                options = entry[1]
                if isinstance(options, list) and options and isinstance(options[0], list):
                    names = [str(n) for n in options[0]]
    return {'checkpoints': names}


# ── workflows ──────────────────────────────────────────────────────────────

_MARKERS = ('%PROMPT%', '%NEGATIVE%', '%CHECKPOINT%', '%WIDTH%', '%HEIGHT%', '%STEPS%', '%CFG%', '%SEED%')


def _default_workflow() -> dict[str, Any]:
    """Plain txt2img in ComfyUI API format (the "Save (API format)" shape)."""
    return {
        '1': {
            'class_type': 'CheckpointLoaderSimple',
            'inputs': {'ckpt_name': '%CHECKPOINT%'},
            '_meta': {'title': 'Load Checkpoint'},
        },
        '2': {
            'class_type': 'CLIPTextEncode',
            'inputs': {'text': '%PROMPT%', 'clip': ['1', 1]},
            '_meta': {'title': 'Prompt'},
        },
        '3': {
            'class_type': 'CLIPTextEncode',
            'inputs': {'text': '%NEGATIVE%', 'clip': ['1', 1]},
            '_meta': {'title': 'Negative'},
        },
        '4': {
            'class_type': 'EmptyLatentImage',
            'inputs': {'width': '%WIDTH%', 'height': '%HEIGHT%', 'batch_size': 1},
            '_meta': {'title': 'Empty Latent'},
        },
        '5': {
            'class_type': 'KSampler',
            'inputs': {
                'seed': '%SEED%',
                'steps': '%STEPS%',
                'cfg': '%CFG%',
                'sampler_name': 'euler',
                'scheduler': 'normal',
                'denoise': 1,
                'model': ['1', 0],
                'positive': ['2', 0],
                'negative': ['3', 0],
                'latent_image': ['4', 0],
            },
            '_meta': {'title': 'KSampler'},
        },
        '6': {
            'class_type': 'VAEDecode',
            'inputs': {'samples': ['5', 0], 'vae': ['1', 2]},
            '_meta': {'title': 'VAE Decode'},
        },
        '7': {
            'class_type': 'SaveImage',
            'inputs': {'filename_prefix': 'august', 'images': ['6', 0]},
            '_meta': {'title': 'Save Image'},
        },
    }


def _substitute(value: Any, slots: dict[str, Any]) -> Any:
    """Replace %MARKER% leaves (and string leaves wrapping a marker) deep in
    the workflow graph."""
    if isinstance(value, dict):
        return {k: _substitute(v, slots) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, slots) for v in value]
    if isinstance(value, str):
        for marker, replacement in slots.items():
            if value == marker:
                return replacement
        for marker, replacement in slots.items():
            if marker in value and isinstance(replacement, str):
                return value.replace(marker, replacement)
    return value


def _build_graph(args: dict[str, Any], checkpoint: str) -> dict[str, Any]:
    custom = as_str(get_config().get('workflowJson'), '').strip()
    if custom:
        try:
            graph = json.loads(custom)
        except ValueError as exc:
            raise RuntimeError(f'The custom workflow is not valid JSON: {exc}') from exc
        if not isinstance(graph, dict) or not graph:
            raise RuntimeError('The custom workflow must be a non-empty JSON object')
    else:
        graph = _default_workflow()
    slots = {
        '%PROMPT%': str(args['prompt']),
        '%NEGATIVE%': str(args.get('negative_prompt') or ''),
        '%CHECKPOINT%': checkpoint,
        '%WIDTH%': int(str(args.get('width') or 1024)),
        '%HEIGHT%': int(str(args.get('height') or 1024)),
        '%STEPS%': int(str(args.get('steps') or 25)),
        '%CFG%': float(str(args.get('cfg') or 7.0)),
        '%SEED%': int(str(args.get('seed'))) if args.get('seed') is not None else time.time_ns() % (1 << 31),
    }
    return _substitute(graph, slots)


async def _resolve_checkpoint(explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    configured = as_str(get_config().get('defaultCheckpoint'), '').strip()
    if configured:
        return configured
    names = (await checkpoints())['checkpoints']
    if not names:
        raise RuntimeError(
            'No checkpoints found in ComfyUI — place an SD model in ComfyUI/models/checkpoints'
        )
    return names[0]


# ── generation ─────────────────────────────────────────────────────────────


async def generate_image(
    *,
    prompt: str,
    negative_prompt: str = '',
    checkpoint: str | None = None,
    width: int = 1024,
    height: int = 1024,
    steps: int = 25,
    cfg: float = 7.0,
    seed: int | None = None,
    out_path: str | None = None,
) -> dict[str, Any]:
    """Run one txt2img through ComfyUI and write the PNG into the workspace.

    Returns ``{path, filename, seed}``; the caller (tool or panel) surfaces
    the path and the chat renders the artifact from it.
    """
    if not as_bool(get_config().get('enabled'), False):
        raise RuntimeError('ComfyUI is not enabled — configure it in Settings → ComfyUI first')
    prompt = (prompt or '').strip()
    if not prompt:
        raise ValueError('prompt is required')

    ckpt = await _resolve_checkpoint(checkpoint)
    width = max(64, int(width)) // 64 * 64
    height = max(64, int(height)) // 64 * 64
    args = {
        'prompt': prompt,
        'negative_prompt': negative_prompt,
        'width': width,
        'height': height,
        'steps': steps,
        'cfg': cfg,
        'seed': seed,
    }
    graph = _build_graph(args, ckpt)

    client_id = uuid.uuid4().hex
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(f'{_root()}/prompt', json={'prompt': graph, 'client_id': client_id})
        if resp.status_code != 200:
            detail = ''
            try:
                detail = str(resp.json().get('error', {}))[:400]
            except Exception:  # noqa: BLE001
                detail = resp.text[:400]
            raise RuntimeError(f'ComfyUI rejected the workflow (HTTP {resp.status_code}): {detail}')
        prompt_id = str(resp.json().get('prompt_id', ''))
        if not prompt_id:
            raise RuntimeError('ComfyUI did not return a prompt_id')

        history: dict[str, Any] = {}
        deadline = asyncio.get_running_loop().time() + _GENERATE_TIMEOUT_S
        while asyncio.get_running_loop().time() < deadline:
            hresp = await client.get(f'{_root()}/history/{prompt_id}')
            if hresp.status_code == 200:
                data = hresp.json()
                if isinstance(data, dict) and prompt_id in data:
                    history = as_dict(data.get(prompt_id), {})
                    status = as_dict(history.get('status'), {})
                    if status.get('status_str') == 'error':
                        raise RuntimeError('ComfyUI reported an error executing the workflow')
                    if history.get('outputs'):
                        break
            await asyncio.sleep(_POLL_INTERVAL_S)
        else:
            raise RuntimeError('Image generation timed out — is ComfyUI busy or the model too large?')

    outputs = as_dict(history.get('outputs'), {})
    image = None
    for node in outputs.values():
        for img in as_list(as_dict(node, {}).get('images'), []):
            if isinstance(img, dict) and img.get('type', 'output') == 'output':
                image = img
                break
        if image:
            break
    if not image:
        raise RuntimeError('ComfyUI finished but produced no output image — does the workflow save one?')

    params = {
        'filename': str(image.get('filename', '')),
        'subfolder': str(image.get('subfolder', '')),
        'type': str(image.get('type', 'output')),
    }
    vresp = await client.get(f'{_root()}/view', params=params)
    if vresp.status_code != 200:
        raise RuntimeError(f'Could not fetch the image from /view (HTTP {vresp.status_code})')

    from app.services.sandbox.paths import bind_path

    workspace = _workspace()
    suggested = (out_path or f"images/comfyui-{time.strftime('%Y%m%d-%H%M%S')}.png").strip()
    bound, err = bind_path(suggested, workspace, for_write=True)
    if err or bound is None:
        raise ValueError(err or f'Invalid output path: {suggested}')
    if Path(str(bound)).suffix.lower() != '.png':
        raise ValueError('Output path must end with .png')
    Path(str(bound)).parent.mkdir(parents=True, exist_ok=True)
    Path(str(bound)).write_bytes(vresp.content)

    logger.info('ComfyUI wrote %s (%s bytes, checkpoint %s)', bound, len(vresp.content), ckpt)
    return {'path': str(bound), 'filename': Path(str(bound)).name, 'seed': args['seed']}


def _workspace() -> str:
    from app.services.workbench.context import currentSessionId
    from app.services.workbench.sessions import get_workbench_session

    sid = currentSessionId.get()
    if sid and sid != 'default':
        sess = get_workbench_session(sid)
        if sess is not None:
            return str(getattr(sess, 'workspacePath', '') or '')
    return ''


async def _tool_generate(args: dict[str, Any]) -> str:
    """The generate_image tool handler — returns the file path as text."""
    try:
        result = await generate_image(
            prompt=str(args.get('prompt', '')),
            negative_prompt=str(args.get('negative_prompt') or ''),
            checkpoint=str(args.get('checkpoint') or '') or None,
            width=int(args.get('width') or 1024),
            height=int(args.get('height') or 1024),
            steps=int(args.get('steps') or 25),
            cfg=float(args.get('cfg') or 7.0),
            seed=int(args['seed']) if args.get('seed') is not None else None,
            out_path=str(args.get('path') or '') or None,
        )
        return (
            f"Image written to {result['path']} (seed {result['seed']}). "
            'The chat renders it as a file card; reference the path to discuss it.'
        )
    except Exception as exc:  # noqa: BLE001 — tool receipts must stay strings
        return f'Error: {exc}'
