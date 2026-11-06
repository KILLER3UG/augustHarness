"""Hugging Face Hub read side: search GGUF models, list a repo's files.

Thin, typed reads over the public Hub API (no auth — gated repos come back
401 and the message says so). The downloader lives in ``hf_downloads``;
this module never writes to disk.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.services import http_env

logger = logging.getLogger(__name__)

_HUB_API = 'https://huggingface.co/api'
_TIMEOUT = httpx.Timeout(15.0)
_MAX_PAGE = 100

# The Hub's own sort keys, behind the labels the picker shows. `recommended`
# is the Hub's trending score — what its own "Recommended" ordering is.
_SORTS: dict[str, str] = {
    'recommended': 'trendingScore',
    'downloads': 'downloads',
    'likes': 'likes',
    'recent': 'lastModified',
}


def _quantization(filename: str) -> str:
    """The quant token in a GGUF filename ('' when the name carries none).

    Parsed from the name, never guessed from the size: a picker that inferred
    Q4_K_M from a byte count would mislabel what the download costs in quality.
    """
    stem = filename.rsplit('/', 1)[-1]
    m = re.search(
        r'((?:IQ|IQ\d*_?|Q)\d+(?:_[A-Z0-9]+)*|F16|BF16|FP16|F32)',
        stem.upper(),
    )
    return m.group(1) if m else ''


def _is_sharded(filename: str) -> bool:
    return bool(re.search(r'\d+-of-\d+', filename, re.IGNORECASE))


def _recommended_file(files: list[dict[str, Any]]) -> str:
    """The one file a browser should default to, or '' when nothing is runnable.

    Preference order is a stated rule, not a score: a single-file Q4_K_M is the
    build almost every local runner recommends, then the other Q4s, then the
    higher quants by ascending size, then anything single-file.
    """
    single = [f for f in files if str(f.get('filename', '')).lower().endswith('.gguf') and not f['sharded']]
    if not single:
        return ''
    by_quant = {str(f.get('quantization') or '').upper(): f for f in single}
    for want in ('Q4_K_M', 'Q4_K_S', 'Q4_0', 'IQ4_XS', 'IQ4_0'):
        if want in by_quant:
            return str(by_quant[want]['filename'])
    q4 = [f for f in single if str(f.get('quantization', '')).upper().startswith('Q4')]
    if q4:
        return str(min(q4, key=lambda f: int(f['size']))['filename'])
    ordered = sorted(
        single,
        key=lambda f: (
            0 if str(f.get('quantization', '')).upper().startswith(('Q5', 'Q6', 'Q8', 'IQ')) else 1,
            int(f['size']),
        ),
    )
    return str(ordered[0]['filename'])


async def search_models(
    query: str,
    *,
    limit: int = 20,
    model_filter: str | None = None,
    sort: str = 'recommended',
) -> dict[str, Any]:
    """Search the Hub — ALL models by default (the Hub is a browser, not a
    GGUF shelf); pass ``model_filter='gguf'`` for the runnable-only view.

    ``sort`` maps the picker's labels onto the Hub's own keys; an unknown label
    falls back to trending rather than silently changing the ordering.
    """
    q = (query or '').strip()
    hub_sort = _SORTS.get((sort or '').strip().lower(), 'trendingScore')
    # The list endpoint omits `lastModified` unless it is asked for — and once
    # `expand` is used it returns ONLY the expanded fields, so every column the
    # row shows has to be listed here. That is also why `pipelineTag` and
    # `gated` are not returned from search any more: they would arrive empty
    # and the UI would render a field it never received. The detail read
    # (`repo_files`) carries both.
    params: list[tuple[str, str | int | float | bool | None]] = [
        ('search', q),
        ('sort', hub_sort),
        ('direction', -1),
        ('limit', max(1, min(int(limit), _MAX_PAGE))),
        ('expand', 'lastModified'),
        ('expand', 'downloads'),
        ('expand', 'likes'),
        ('expand', 'pipeline_tag'),
    ]
    if model_filter:
        params.append(('filter', str(model_filter).strip()))
    async with http_env.client(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(f'{_HUB_API}/models', params=params)
        if resp.status_code != 200:
            raise RuntimeError(f'Hugging Face search returned HTTP {resp.status_code}')
        rows = resp.json()
    models = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        repo_id = str(r.get('id') or r.get('modelId') or '')
        if not repo_id:
            continue
        models.append(
            {
                'repoId': repo_id,
                # The row shows org/user under the name. It is the first
                # segment of the repo id — the API carries no separate author.
                'author': repo_id.split('/', 1)[0],
                'downloads': int(r.get('downloads') or 0),
                'likes': int(r.get('likes') or 0),
                'lastModified': str(r.get('lastModified') or ''),
                # Absent on repos with no pipeline tag — '' is the honest
                # value, and the Local Models tab reads it.
                'pipelineTag': str(r.get('pipeline_tag') or ''),
            }
        )
    return {'query': q, 'sort': hub_sort, 'count': len(models), 'models': models}



async def repo_files(repo_id: str) -> dict[str, Any]:
    """A repo's files with exact sizes — every file, any format (browsing is
    not limited to what August can run; the UI marks what is servable).

    ``blobs=true`` makes the API carry per-file sizes; without it every
    size would be a guess, and a quant picker that guesses sizes lies
    about what a download will cost.
    """
    repo_id = (repo_id or '').strip().strip('/')
    if not repo_id or repo_id.count('/') != 1:
        raise ValueError('repoId must look like org/name')
    async with http_env.client(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(f'{_HUB_API}/models/{repo_id}', params={'blobs': 'true'})
        if resp.status_code == 401:
            raise RuntimeError(f'{repo_id} is gated — Hugging Face sign-in is not supported yet')
        if resp.status_code == 404:
            raise RuntimeError(f'Repository not found: {repo_id}')
        if resp.status_code != 200:
            raise RuntimeError(f'Hugging Face returned HTTP {resp.status_code} for {repo_id}')
        data = resp.json()

    files = []
    for s in data.get('siblings', []) or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get('rfilename', ''))
        if not name:
            continue
        sharded = _is_sharded(name)
        is_gguf = name.lower().endswith('.gguf')
        files.append(
            {
                'filename': name,
                'size': int(s.get('size') or 0),
                'quantization': _quantization(name) if is_gguf else '',
                'sharded': sharded,
                # What August can actually serve: one file, GGUF container.
                'serveable': is_gguf and not sharded and 'mmproj' not in name.lower(),
            }
        )
    files.sort(key=lambda f: str(f['filename']))
    recommended = _recommended_file(files)
    suffixes = {str(f['filename']).rsplit('.', 1)[-1].lower() for f in files if '.' in str(f['filename'])}
    formats = [f for f in ('gguf', 'safetensors', 'onnx', 'bin') if f in suffixes]
    # A multimodal projector file is how a GGUF repo ships vision; there is no
    # cheaper honest signal, and claiming a capability the repo does not carry
    # is worse than claiming nothing.
    has_mmproj = any('mmproj' in str(f['filename']).lower() for f in files)
    capabilities = ['vision'] if has_mmproj else []
    return {
        'repoId': data.get('id') or repo_id,
        'lastModified': data.get('lastModified', ''),
        'downloads': data.get('downloads', 0),
        'likes': data.get('likes', 0),
        'gated': bool(data.get('gated', False)),
        'pipelineTag': data.get('pipeline_tag', '') or '',
        'license': str(data.get('cardData', {}).get('license', '') or '')
        if isinstance(data.get('cardData'), dict)
        else '',
        'files': files,
        'recommended': recommended,
        'formats': formats,
        'capabilities': capabilities,
    }


async def repo_readme(repo_id: str, *, max_chars: int = 60_000) -> dict[str, Any]:
    """The repo's README.md as text ('' when the repo has none).

    Fetched from the `raw` endpoint so the model card arrives as Markdown and
    the renderer can use the same component the rest of the app uses. A missing
    README is a normal result, not an error.
    """
    repo_id = (repo_id or '').strip().strip('/')
    if not repo_id or repo_id.count('/') != 1:
        raise ValueError('repoId must look like org/name')
    url = f'https://huggingface.co/{repo_id}/raw/main/README.md'
    async with http_env.client(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url)
    if resp.status_code == 404:
        return {'repoId': repo_id, 'markdown': '', 'truncated': False}
    if resp.status_code == 401:
        raise RuntimeError(f'{repo_id} is gated — Hugging Face sign-in is not supported yet')
    if resp.status_code != 200:
        raise RuntimeError(f'Hugging Face returned HTTP {resp.status_code} for {repo_id}')
    text = resp.text or ''
    # A model card opens with a YAML frontmatter block (license, base_model,
    # tags). Rendered as Markdown it becomes a wall of `key: value` above the
    # prose nobody asked to read, so the envelope is stripped here — the same
    # rule the skill loader applies to SKILL.md.
    fm = re.match(r'\A---[ \t]*\n.*?\n---[ \t]*\n', text, re.DOTALL)
    if fm:
        text = text[fm.end():]
    truncated = len(text) > max_chars
    return {'repoId': repo_id, 'markdown': text[:max_chars], 'truncated': truncated}


def download_url(repo_id: str, filename: str) -> str:
    """The resolve URL for one file — used verbatim, no path munging."""
    return f'https://huggingface.co/{repo_id.strip("/")}/resolve/main/{filename.lstrip("/")}'
