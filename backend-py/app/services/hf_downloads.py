"""Hugging Face downloads: resumable, backgrounded, honest progress.

One GGUF file per job, downloaded with HTTP Range resume into
``{dataDir}/models/hf/<org>__<repo>/<filename>`` — user state under the
data dir, never the installer payload, kept on update. Jobs are tracked
in memory (plus a status file a restart reads back), polled by the UI
over ``GET /api/hf/downloads``; state changes push
``emit_invalidate('hf-downloads')``. No new SSE frames — the realtime
invalidate + poll is the established channel for long jobs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any

import httpx

from app.lib.paths import dataDir
from app.services import hf_hub, http_env
from app.services.realtime_bus import emit_invalidate

logger = logging.getLogger(__name__)

_MODELS_ROOT = 'models'
_HF_DIR = 'hf'
_STATUS_FILE = 'downloads.json'
_TIMEOUT = httpx.Timeout(connect=15, read=60, write=60, pool=15)
_CHUNK = 1 << 20


class DownloadError(RuntimeError):
    """A user-facing download failure."""


def _jobs_root() -> Path:
    return dataDir() / _MODELS_ROOT / _HF_DIR


def _part_path(job: dict[str, Any]) -> Path:
    return Path(str(job['destPath']) + '.part')


def _status_file() -> Path:
    return _jobs_root() / _STATUS_FILE


# In-memory registry of this process's jobs. A restart re-reads the status
# file (finished files are also discovered on disk), but a *running*
# download does not survive a restart — it is reported as interrupted.
_jobs: dict[str, dict[str, Any]] = {}
_tasks: dict[str, asyncio.Task[None]] = {}
_seq = 0


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds')


def _notify() -> None:
    try:
        emit_invalidate('hf-downloads')
    except Exception:  # noqa: BLE001 — the bus is best-effort
        pass


def _persist() -> None:
    try:
        _jobs_root().mkdir(parents=True, exist_ok=True)
        rows = sorted(_jobs.values(), key=lambda j: str(j.get('startedAt', '')), reverse=True)
        _status_file().write_text(json.dumps(rows, indent=2), encoding='utf-8')
    except Exception:  # noqa: BLE001 — the status file is a convenience
        logger.warning('could not persist download status', exc_info=True)


def _mark_interrupted_on_boot() -> None:
    try:
        rows = json.loads(_status_file().read_text('utf-8'))
    except Exception:  # noqa: BLE001
        return
    if not isinstance(rows, list):
        return
    for r in rows:
        if isinstance(r, dict) and r.get('status') == 'downloading':
            r['status'] = 'interrupted'
            r['error'] = 'August restarted while the download was running'


_mark_interrupted_on_boot()


def list_downloads() -> dict[str, Any]:
    """All known jobs (active, done, failed) + finished files found on disk."""
    on_disk: list[dict[str, Any]] = []
    root = _jobs_root()
    if root.is_dir():
        known = {str(j.get('destPath')) for j in _jobs.values()}
        for org_dir in sorted(root.iterdir()):
            if not org_dir.is_dir():
                continue
            for f in sorted(org_dir.rglob('*.gguf')):
                if str(f) in known:
                    continue
                size = f.stat().st_size
                on_disk.append(
                    {
                        'jobId': f'file:{f}',
                        'repoId': org_dir.name.replace('__', '/', 1),
                        'filename': f.name,
                        'destPath': str(f),
                        'size': size,
                        'bytesDone': size,
                        'status': 'done',
                        'speedBps': None,
                        'error': None,
                        'startedAt': None,
                    }
                )
    rows = sorted(
        list(_jobs.values()) + on_disk,
        key=lambda j: str(j.get('startedAt') or ''),
        reverse=True,
    )
    return {'downloads': rows}


def _dest_path(repo_id: str, filename: str) -> Path:
    """Where one file lands, preserving the repo's subfolders.

    Quants live in subdirectories (``IQ2_XS/…-00001-of-00002.gguf``), and
    flattening to the basename would collide across quants. Path parts are
    validated first — the filename comes from the Hub listing, but this
    function must never assemble an escape.
    """
    parts = [p for p in filename.split('/') if p not in ('', '.')]
    if any(p == '..' for p in parts) or (len(parts) > 0 and Path(parts[0]).drive):
        raise DownloadError(f'Refusing unsafe file path from repo listing: {filename}')
    return _jobs_root() / repo_id.replace('/', '__') / Path(*parts)


def _free_disk() -> int:
    return shutil.disk_usage(_jobs_root()).free


async def start_download(repo_id: str, filename: str) -> dict[str, Any]:
    """Start one background GGUF download; returns the job row immediately."""
    global _seq
    repo_id = (repo_id or '').strip().strip('/')
    filename = (filename or '').strip().lstrip('/')
    # Any file in the repo is downloadable (browsing is not limited to what
    # August can RUN — only serving is, and that gates on the extension).
    if repo_id.count('/') != 1 or not filename:
        raise DownloadError('Expected a file inside an org/name repository')

    # Meta first: the exact size drives the disk-space check and the bar.
    meta = await hf_hub.repo_files(repo_id)
    sizes = {f['filename']: int(f['size']) for f in meta['files']}
    if filename not in sizes:
        raise DownloadError(f'{filename} is not a file in {repo_id}')
    size = sizes[filename]

    dest = _dest_path(repo_id, filename)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size == size:
        return {
            'jobId': f'existing:{dest}',
            'repoId': repo_id,
            'filename': filename,
            'destPath': str(dest),
            'size': size,
            'bytesDone': size,
            'status': 'done',
            'speedBps': None,
            'error': None,
            'startedAt': None,
        }
    free = _free_disk()
    if free < size + (1 << 30):
        raise DownloadError(
            f'Not enough disk space: the file is {size / (1 << 30):.1f} GB '
            f'but only {free / (1 << 30):.1f} GB is free'
        )

    _seq += 1
    job: dict[str, Any] = {
        'jobId': f'hf-{_seq}',
        'repoId': repo_id,
        'filename': filename,
        'destPath': str(dest),
        'size': size,
        'bytesDone': 0,
        'status': 'downloading',
        'speedBps': None,
        'error': None,
        'startedAt': _now(),
    }
    _jobs[job['jobId']] = job
    _persist()
    _notify()
    _tasks[job['jobId']] = asyncio.create_task(_run_download(job))
    return dict(job)


async def cancel_download(job_id: str) -> dict[str, Any]:
    task = _tasks.pop(job_id, None)
    job = _jobs.get(job_id)
    if task:
        task.cancel()
        if job:
            job['status'] = 'cancelled'
            _persist()
            _notify()
    return {'cancelled': job_id, 'found': job is not None}


async def _run_download(job: dict[str, Any]) -> None:
    dest = Path(str(job['destPath']))
    part = _part_path(job)
    url = hf_hub.download_url(str(job['repoId']), str(job['filename']))
    started_loop = asyncio.get_running_loop().time()

    try:
        done = 0
        # Two attempts: a stale .part (416) is deleted and the download
        # restarts once; anything else fails the job with the reason.
        for attempt in range(2):
            done = part.stat().st_size if part.exists() else 0
            job['bytesDone'] = done
            headers = {'Range': f'bytes={done}-'} if done else {}
            async with http_env.client(follow_redirects=True, timeout=_TIMEOUT) as client:
                async with client.stream('GET', url, headers=headers) as resp:
                    if resp.status_code == 416 and attempt == 0:
                        part.unlink(missing_ok=True)
                        continue
                    if resp.status_code not in (200, 206):
                        raise DownloadError(f'Download returned HTTP {resp.status_code}')
                    if resp.status_code == 200 and done:
                        # Server ignored the Range — start over, not append.
                        done = 0
                        job['bytesDone'] = 0
                    mode = 'ab' if resp.status_code == 206 else 'wb'
                    with part.open(mode) as fh:
                        async for chunk in resp.aiter_bytes(_CHUNK):
                            fh.write(chunk)
                            done += len(chunk)
                            job['bytesDone'] = done
                            elapsed = max(asyncio.get_running_loop().time() - started_loop, 1e-6)
                            job['speedBps'] = round(done / elapsed)
            break
        if done != int(job['size']):
            raise DownloadError(f'Downloaded {done} bytes but the file is {job["size"]} bytes')
        part.replace(dest)
        job['status'] = 'done'
        job['bytesDone'] = done
        logger.info('HF download finished: %s (%s bytes)', dest, done)
    except asyncio.CancelledError:
        job['status'] = 'cancelled'
    except Exception as exc:  # noqa: BLE001 — the job row is the error surface
        job['status'] = 'failed'
        job['error'] = str(exc)
        logger.error('HF download failed (%s): %s', job['filename'], exc)
    finally:
        _tasks.pop(job['jobId'], None)
        _persist()
        _notify()
