"""Hugging Face Hub read side: search GGUF models, list a repo's files.

Thin, typed reads over the public Hub API (no auth — gated repos come back
401 and the message says so). The downloader lives in ``hf_downloads``;
this module never writes to disk.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_HUB_API = 'https://huggingface.co/api'
_TIMEOUT = httpx.Timeout(15.0)


async def search_models(query: str, *, limit: int = 20) -> dict[str, Any]:
    """Search the Hub for GGUF model repos, most downloads first."""
    q = (query or '').strip()
    params: dict[str, str | int] = {
        'search': q,
        'filter': 'gguf',
        'sort': 'downloads',
        'direction': -1,
        'limit': max(1, min(int(limit), 50)),
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(f'{_HUB_API}/models', params=params)
        if resp.status_code != 200:
            raise RuntimeError(f'Hugging Face search returned HTTP {resp.status_code}')
        rows = resp.json()
    models = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        models.append(
            {
                'repoId': r.get('id') or r.get('modelId') or '',
                'downloads': r.get('downloads', 0),
                'likes': r.get('likes', 0),
                'lastModified': r.get('lastModified', ''),
                'pipelineTag': r.get('pipeline_tag', ''),
            }
        )
    return {'query': q, 'models': [m for m in models if m['repoId']]}


def _looks_like_gguf(filename: str) -> bool:
    low = filename.lower()
    return low.endswith('.gguf')


async def repo_files(repo_id: str) -> dict[str, Any]:
    """A repo's GGUF files with exact sizes — the quant picker's source.

    ``blobs=true`` makes the API carry per-file sizes; without it every
    size would be a guess, and a quant picker that guesses sizes lies
    about what a download will cost.
    """
    repo_id = (repo_id or '').strip().strip('/')
    if not repo_id or repo_id.count('/') != 1:
        raise ValueError('repoId must look like org/name')
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
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
        if not _looks_like_gguf(name):
            continue
        files.append({'filename': name, 'size': int(s.get('size') or 0)})
    files.sort(key=lambda f: str(f['filename']))
    return {
        'repoId': data.get('id') or repo_id,
        'lastModified': data.get('lastModified', ''),
        'downloads': data.get('downloads', 0),
        'files': files,
    }


def download_url(repo_id: str, filename: str) -> str:
    """The resolve URL for one file — used verbatim, no path munging."""
    return f'https://huggingface.co/{repo_id.strip("/")}/resolve/main/{filename.lstrip("/")}'
