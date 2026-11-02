"""Pinned llama.cpp release the local runtime installs from.

Pure data. Every asset is pinned by name, exact size, and SHA256 (the
digest GitHub publishes on the release object — verified against the
b11960 release listing on 2026-11-03). Nothing here is fetched at
runtime except the assets themselves; bumping the pin means replacing
this file's constants from the new release page, never trusting a
redirect or a "latest" URL.

Windows x64 only — the desktop product ships Windows installers. Other
platforms need their own rows before the runtime service will offer
them; until then the service refuses with a clear error instead of
guessing an asset name.
"""

from __future__ import annotations

MANIFEST_TAG = 'b11960'
DOWNLOAD_BASE_URL = f'https://github.com/ggml-org/llama.cpp/releases/download/{MANIFEST_TAG}'

# A CUDA build is two assets: the llama binaries and the CUDA runtime DLLs
# (cudart) that go with that CUDA major. Both are verified independently.
_VARIANTS: dict[str, dict[str, object]] = {
    'cuda-13.4': {
        'label': 'NVIDIA CUDA 13',
        'requires': 'NVIDIA driver supporting CUDA 13+',
        'assets': [
            {
                'name': f'llama-{MANIFEST_TAG}-bin-win-cuda-13.4-x64.zip',
                'size': 164986387,
                'sha256': '31c75ea62b80f0816c855ea783ef68757bc2d48028edaa57d904e4982bddea4a',
            },
            {
                'name': 'cudart-llama-bin-win-cuda-13.4-x64.zip',
                'size': 423535356,
                'sha256': '738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668',
            },
        ],
    },
    'cuda-12.4': {
        'label': 'NVIDIA CUDA 12',
        'requires': 'NVIDIA driver supporting CUDA 12.4+',
        'assets': [
            {
                'name': f'llama-{MANIFEST_TAG}-bin-win-cuda-12.4-x64.zip',
                'size': 279763224,
                'sha256': '8d9d819261de10cc2f03921b8b79c779c5c458e21edb60c170ee30a7d462d684',
            },
            {
                'name': 'cudart-llama-bin-win-cuda-12.4-x64.zip',
                'size': 391443627,
                'sha256': '8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6',
            },
        ],
    },
    'vulkan': {
        'label': 'Vulkan GPU',
        'requires': 'Any GPU with a Vulkan driver (NVIDIA / AMD / Intel)',
        'assets': [
            {
                'name': f'llama-{MANIFEST_TAG}-bin-win-vulkan-x64.zip',
                'size': 37345851,
                'sha256': 'bac6914b5e83523eb008c6789eaa12669f93fbc196c3b9e8237b6672323b33e9',
            },
        ],
    },
    'cpu': {
        'label': 'CPU only',
        'requires': 'No GPU required',
        'assets': [
            {
                'name': f'llama-{MANIFEST_TAG}-bin-win-cpu-x64.zip',
                'size': 24407745,
                'sha256': '73f962f82850d244db47eaacb9d913637027b82399124324298576d58c5f4287',
            },
        ],
    },
}

# Auto-detection order. nvidia-smi reports the highest CUDA runtime the
# installed driver supports; 13 wins, then 12.4, then Vulkan (small,
# works on every vendor), CPU last.
_AUTO_ORDER = ('cuda-13.4', 'cuda-12.4', 'vulkan', 'cpu')


def variant_names() -> list[str]:
    return list(_VARIANTS.keys())


def variant_label(variant: str) -> str:
    entry = _VARIANTS.get(variant)
    return str(entry['label']) if entry else variant


def variant_assets(variant: str) -> list[dict[str, object]]:
    entry = _VARIANTS.get(variant)
    if not entry:
        raise ValueError(f'unknown runtime variant {variant!r} — known: {", ".join(_VARIANTS)}')
    assets = entry['assets']
    return list(assets) if isinstance(assets, list) else []


def variant_total_bytes(variant: str) -> int:
    return sum(int(str(a['size'])) for a in variant_assets(variant))


def variant_requires(variant: str) -> str:
    entry = _VARIANTS.get(variant)
    return str(entry['requires']) if entry else ''


def auto_variant_for_cuda(cuda_version: str | None, nvidia_present: bool) -> str:
    """Pick the auto variant from ``nvidia-smi``'s reported CUDA version.

    The reported version is the *maximum* runtime the driver supports, so
    ``13.2`` means a cuda-13.4 build may not match (13.4 > 13.2) but any
    13.x driver runs the 13.4 binary family — the cudart DLLs ship with
    the build, only the driver matters. A 12.9 driver cannot load a
    cuda-13 build, so it takes 12.4. No NVIDIA, or a driver older than
    12.4, lands on Vulkan; CPU stays a manual choice because Vulkan works
    on far more machines than "no NVIDIA" implies.
    """
    if not nvidia_present:
        return 'vulkan'
    if cuda_version:
        try:
            major, minor = (int(p) for p in cuda_version.split('.')[:2])
        except ValueError:
            return 'vulkan'
        if (major, minor) >= (13, 0):
            return 'cuda-13.4'
        if (major, minor) >= (12, 4):
            return 'cuda-12.4'
    return 'vulkan'
