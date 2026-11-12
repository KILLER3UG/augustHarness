"""Offline copies of the brain record and the skills tree: verify, back up,
and restore the RECORD safely.

The skills archive is a verified offline COPY, not a second restore target:
it is written and hash-checked on every backup, but ``apply_pending_restore``
swaps only the brain record. Putting a skill tree back is extracting the
``skills-*.tar.gz`` beside the copy by hand — the archive carries its manifest,
so that extraction is self-describing.

The brain record is the user's memory. It is the file tree the store reads and
writes — ``memory/`` (the markdown fact record), ``<state>/`` (kv, proposals,
exams, episodes, timeline, machine state) and ``config.json`` — so a backup is
a verified ``tar.gz`` of exactly those. Before the file store, the only copy
the app ever made of the SQL database was one rolling file nothing read back;
the archive keeps the same guarantees with different bytes.

The scope is deliberate (owner decision, 2026-11-12): ``transcripts/`` and
``telemetry/`` are NOT archived. The transcript is session history the owner
has chosen not to snapshot offline, and telemetry is a cap-bound ledger whose
old rows exist to be rotated away. Do not widen the archive to "all of
data/" without an owner decision — the exclusion is a scope statement, not an
oversight.

The skills tree is the user's *procedure* memory, and it had no offline copy at
all: every skill write is auto-applied (``skillAutonomy`` defaults on), so a
bad bulk edit lands with no human step and the only undo is the per-skill
``.versions`` snapshot taken at write time. Since 2026-11 the same verified
rolling copy includes a ``tar.gz`` of the durable skill roots, recorded in an
archive manifest beside the record copy's name.

Three constraints shape the design:

* **A restore cannot happen while the app is running.** Store threads hold the
  record files open for atomic writes, and replacing them underneath the
  writers yields half-old, half-new reads. So a restore is *staged* and
  applied at the next startup, before any store opens — and the response says
  so.
* **The pending marker is a file, not a row.** The situation where a restore is
  needed is precisely the one where the record may not open.
* **Both halves are verified before either is trusted.** The record archive is
  re-opened and every file's SHA-256 is checked against the manifest written
  inside it; the skills archive is verified the same way. An unverified
  artifact is deleted, never listed as good.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

logger = logging.getLogger('brain_backup')

KEEP_BACKUPS: Final = 5
BACKUP_SUBDIR: Final = 'backups'
PENDING_FILE: Final = 'brain-restore.pending'
PRE_RESTORE_SUFFIX: Final = '.pre-restore'
# The skills half of a backup: ``skills-<stamp>-<slug>.tar.gz``, sharing the
# stamp of the record copy beside it. The pair is one backup — prune and list
# treat the stamp as the unit.
SKILLS_ARCHIVE_PREFIX: Final = 'skills-'
SKILLS_ARCHIVE_SUFFIX: Final = '.tar.gz'
_ARCHIVE_NAME_RE: Final = re.compile(
    r'^skills-(\d{8}T\d{6}Z)-([a-z0-9][a-z0-9-]{0,20})\.tar\.gz$'
)
# The manifest is the archive's own record of what it holds: the record copy
# it belongs to, the roots it came from, and one SHA-256 per archived file.
_MANIFEST_NAME: Final = '__august_manifest__.json'
_MANIFEST_FORMAT: Final = 'august-skills-archive/1'
# The brain record archive's own manifest — a DIFFERENT name from the skills
# archive's, so neither verify pass can mistake one archive for the other.
_BRAIN_MANIFEST_NAME: Final = '__august_brain_manifest__.json'
_BRAIN_MANIFEST_FORMAT: Final = 'august-brain-archive/1'
# The record half of a backup: ``brain-<stamp>-<slug>.tar.gz`` over the three
# things the store reads and writes — the markdown fact record, the state
# stores, and the settings file.
_BRAIN_ARCHIVE_RE: Final = re.compile(
    r'^brain-(\d{8}T\d{6}Z)-([a-z0-9][a-z0-9-]{0,20})\.tar\.gz$'
)
_BRAIN_ARCHIVE_PARTS: Final = re.compile(
    r'^brain-(\d{8})T(\d{6})Z-([a-z0-9][a-z0-9-]{0,20})\.tar\.gz$'
)
# The legacy SQL-era copies. They still list (they hold user data — deleting
# one would be a loss the user did not ask for), but the SQL engine is
# retired, so restore is refused with the reason rather than silently
# attempting something impossible.
_LEGACY_BACKUP_NAME_RE: Final = re.compile(r'^brain-\d{8}T\d{6}Z-[a-z0-9][a-z0-9-]{0,20}\.sqlite$')
# The usage sidecar is derived telemetry, not content: it changes on every
# load, so archiving it would make every backup differ and would carry counters
# a restore has no door to reconstruct. See usage_sidecar_path().
_USAGE_SIDECAR_NAME: Final = '.usage.json'
# A bounded walk, like the discovery walker: a root sits where the app has no
# business reading the whole tree, and an unbounded archive is how one bad
# external root fills the disk. A truncation is recorded in the manifest, so
# "verified" never silently means "complete".
_ARCHIVE_MAX_FILES: Final = 5000
# The count cap alone does not bound bytes: one 4 GB file would still be read
# whole into memory by read_bytes(). Per-file and total caps mirror
# skill_packs._MAX_BYTES (20 MB) and keep a single external root from taking
# the process or the disk down. A file over either cap is recorded in the
# manifest, so the gap is visible on verify rather than silent.
_ARCHIVE_MAX_FILE_BYTES: Final = 20 * 1024 * 1024
_ARCHIVE_MAX_TOTAL_BYTES: Final = 200 * 1024 * 1024
# Reason is model/user input, so the accepted name is a strict shape: a restore
# that could name an arbitrary path would be a file-write primitive wearing a
# settings button. The slug class below must accept every name ``create_backup``
# can write — a copy whose name this rejects still lists as "verified" with
# Restore enabled, and then fails, which is worse than refusing it at write time.
_BACKUP_NAME_RE: Final = _BRAIN_ARCHIVE_RE
# The same name split into its two parts, so a reader gets WHEN and WHY without
# parsing a filename. `create_backup` is the only writer, and this accepts every
# slug it can produce.


def reason_of(name: str) -> str:
    """Why a copy was taken, as the slug ``create_backup`` wrote into its name.

    The filename is the only durable record of the reason, so this is the door
    the settings UI reads through: showing the raw name asked the user to
    decode ``brain-20260924T063947Z-startup.tar.gz`` when "Auto-copy at startup,
    Sep 24 6:39 AM" is the same fact in words. '' when the name is not ours.
    """
    match = _BRAIN_ARCHIVE_PARTS.match(name or '')
    return match.group(3) if match else ''


def _data_dir() -> Path:
    from app.lib.paths import dataDir

    return Path(dataDir())


def backups_dir() -> Path:
    return _data_dir() / BACKUP_SUBDIR


def _record_sources() -> list[tuple[str, Path]]:
    """What the brain archive holds: the markdown record, the state stores,
    and the settings file. One definition shared by create/verify/restore so
    the three can never disagree about what a backup IS."""
    data = _data_dir()
    out: list[tuple[str, Path]] = []
    for name in ('memory', 'state'):
        path = data / name
        if path.is_dir():
            out.append((name, path))
    config = data / 'config.json'
    if config.is_file():
        out.append(('config.json', config))
    return out


def _backup_key(name: str) -> str:
    """The ``<stamp>-<slug>`` key that pairs both halves of one backup.

    The stamp alone is NOT the unit: ``create_backup`` can run twice inside one
    second (manual + startup, a bulk caller), and two copies that share a stamp
    but differ in slug are two backups, not one. The key sorts in the same
    order as the filename, so retention keeps exactly the files the old
    filename sort kept.
    """
    match = _BRAIN_ARCHIVE_PARTS.match(name)
    if match:
        return f'{match.group(1)}T{match.group(2)}Z-{match.group(3)}'
    match = _ARCHIVE_NAME_RE.match(name)
    if match:
        return f'{match.group(1)}-{match.group(2)}'
    return ''


def _prune(directory: Path) -> list[str]:
    """Delete every artifact of all but the newest ``KEEP_BACKUPS`` backups.

    Both halves of a backup — the record copy and its skills archive — are
    pruned by the SAME key, so a kept record never loses its archive and a
    pruned one never leaves an orphan behind. Legacy SQL-era copies prune by
    their key too: they were part of the same rolling set and the cap exists
    to bound the directory. Returns removed names.
    """
    pairs: list[tuple[str, Path]] = []
    for path in directory.glob('brain-*.tar.gz'):
        key = _backup_key(path.name)
        if key:
            pairs.append((key, path))
    for path in directory.glob('brain-*.sqlite'):
        key = _backup_key(path.name)
        if key:
            pairs.append((key, path))
    for path in directory.glob(f'{SKILLS_ARCHIVE_PREFIX}*{SKILLS_ARCHIVE_SUFFIX}'):
        key = _backup_key(path.name)
        if key:
            pairs.append((key, path))
    # Distinct backups, newest first — a backup is ONE key even though it is
    # two files, and the current one may not have its archive yet at prune
    # time. Counting files here would keep KEEP_BACKUPS/2 backups.
    keys = sorted({key for key, _path in pairs}, reverse=True)
    kept = set(keys[:KEEP_BACKUPS])
    removed: list[str] = []
    for key, path in pairs:
        if key in kept:
            continue
        try:
            path.unlink()
            removed.append(path.name)
        except OSError as exc:
            # A backup we could not delete is worth a log line: silently
            # keeping a full disk is how the next backup fails too.
            logger.warning('Could not prune old backup %s: %s', path.name, exc)
    return removed


def _root_label(scope: str, path: Path) -> str:
    """A human-readable archive label for one root (Bot id, project name, …)."""
    if scope == 'bot':
        return path.parent.name
    if scope == 'project':
        return path.parent.parent.name
    return path.name


def _archive_prefixes(roots: list[tuple[str, Path]]) -> list[str]:
    """One collision-free ``scope/label`` archive prefix per root."""
    prefixes: list[str] = []
    used: set[str] = set()
    for scope, path in roots:
        label = re.sub(r'[^A-Za-z0-9_.-]+', '_', _root_label(scope, path)).strip('._') or 'root'
        base = f'{scope}/{label}'
        candidate = base
        suffix = 2
        while candidate.lower() in used:
            candidate = f'{base}-{suffix}'
            suffix += 1
        used.add(candidate.lower())
        prefixes.append(candidate)
    return prefixes


def _iter_archive_files(root: Path) -> list[Path]:
    """Every file under ``root``, deterministically.

    Symlinks are never followed (a root sits where the app has no business
    reading the whole tree) and the usage sidecar is skipped — it is derived
    telemetry, not content.
    """
    out: list[Path] = []
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    stack.append(entry)
                elif entry.is_file() and entry.name != _USAGE_SIDECAR_NAME:
                    out.append(entry)
            except OSError:
                continue
    return out


def _write_skills_archive(
    target: Path,
    roots: list[tuple[str, Path]],
    *,
    db_name: str,
    db_bytes: int,
    db_version: int,
) -> dict[str, object]:
    """Write the ``tar.gz`` and its manifest. Returns the manifest."""
    prefixes = _archive_prefixes(roots)
    entries: list[tuple[str, Path]] = []
    for (scope, root), prefix in zip(roots, prefixes):
        for src in _iter_archive_files(root):
            try:
                rel = src.relative_to(root).as_posix()
            except ValueError:
                continue
            entries.append((f'{prefix}/{rel}', src))
    entries.sort(key=lambda item: item[0])

    files: list[dict[str, object]] = []
    skipped: list[dict[str, str]] = []
    truncated = False
    total_bytes = 0
    with tarfile.open(str(target), 'w:gz') as tar:
        for arcname, src in entries:
            if len(files) >= _ARCHIVE_MAX_FILES:
                truncated = True
                logger.warning(
                    'skills archive hit the %d-file cap — files beyond it are not backed up',
                    _ARCHIVE_MAX_FILES,
                )
                break
            try:
                stat = src.stat()
            except OSError as exc:
                # One unreadable file must not cost the whole archive: it is
                # recorded in the manifest, so the gap is visible on verify.
                # Any gap makes the archive incomplete, so `truncated` is set
                # too — "verified" must never read as "complete".
                truncated = True
                skipped.append({'path': str(src), 'reason': str(exc)})
                continue
            size = stat.st_size
            if size > _ARCHIVE_MAX_FILE_BYTES:
                truncated = True
                skipped.append(
                    {
                        'path': str(src),
                        'reason': f'file exceeds the {_ARCHIVE_MAX_FILE_BYTES}-byte per-file cap',
                    }
                )
                logger.warning(
                    'skills archive skipped %s: over the %d-byte per-file cap',
                    src,
                    _ARCHIVE_MAX_FILE_BYTES,
                )
                continue
            if total_bytes + size > _ARCHIVE_MAX_TOTAL_BYTES:
                truncated = True
                skipped.append(
                    {
                        'path': str(src),
                        'reason': f'archive exceeds the {_ARCHIVE_MAX_TOTAL_BYTES}-byte total cap',
                    }
                )
                logger.warning(
                    'skills archive hit the %d-byte total cap at %s — files beyond it are not backed up',
                    _ARCHIVE_MAX_TOTAL_BYTES,
                    src,
                )
                break
            try:
                data = src.read_bytes()
            except (OSError, MemoryError) as exc:
                # One unreadable file must not cost the whole archive: it is
                # recorded in the manifest, so the gap is visible on verify.
                # MemoryError is caught here too — a stat/read race can still
                # hand read_bytes() more than the caps promised. Either way the
                # archive is incomplete, so `truncated` is set.
                truncated = True
                skipped.append({'path': str(src), 'reason': str(exc)})
                continue
            info = tarfile.TarInfo(name=arcname)
            info.size = len(data)
            info.mtime = int(stat.st_mtime)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
            files.append(
                {'path': arcname, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            )
            total_bytes += len(data)
        manifest: dict[str, object] = {
            'format': _MANIFEST_FORMAT,
            'createdAt': datetime.now(timezone.utc).isoformat(),
            'backup': {'db': db_name, 'dbBytes': db_bytes, 'dbAppliedVersion': db_version},
            'roots': [
                {'prefix': prefix, 'scope': scope, 'source': str(root)}
                for (scope, root), prefix in zip(roots, prefixes)
            ],
            'files': files,
            'skipped': skipped,
            'truncated': truncated,
            'totals': {'files': len(files), 'bytes': total_bytes},
        }
        raw = json.dumps(manifest, indent=2, ensure_ascii=False).encode('utf-8')
        info = tarfile.TarInfo(name=_MANIFEST_NAME)
        info.size = len(raw)
        info.mtime = int(time.time())
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(raw))
    return manifest


def verify_skills_archive(path: Path) -> dict[str, object]:
    """Re-read a skills archive and prove every file in it matches its hash.

    Reading every member streams the whole gzip member list, so the container's
    own CRC is checked too; the per-file SHA-256 against the manifest is what
    proves the CONTENT is the tree that was archived, not merely that the
    container is intact. Never raises — a verdict is a dict with ``verified``.
    """
    out: dict[str, object] = {
        'verified': False,
        'files': 0,
        'bytes': 0,
        'roots': 0,
        'skipped': 0,
        'truncated': False,
    }
    try:
        with tarfile.open(str(path), 'r:gz') as tar:
            by_name = {member.name: member for member in tar.getmembers()}
            manifest_member = by_name.get(_MANIFEST_NAME)
            if manifest_member is None:
                out['error'] = 'archive carries no manifest'
                return out
            handle = tar.extractfile(manifest_member)
            if handle is None:
                out['error'] = 'manifest is not a regular file'
                return out
            manifest = json.loads(handle.read().decode('utf-8'))
            if not isinstance(manifest, dict):
                out['error'] = 'manifest is not an object'
                return out
            listed = manifest.get('files')
            if not isinstance(listed, list):
                out['error'] = 'manifest has no file list'
                return out
            expected = {_MANIFEST_NAME}
            for item in listed:
                if isinstance(item, dict) and isinstance(item.get('path'), str):
                    expected.add(item['path'])
            if set(by_name) != expected:
                out['error'] = 'archive members do not match the manifest'
                return out
            total = 0
            for item in listed:
                if not isinstance(item, dict):
                    out['error'] = 'manifest file entry is not an object'
                    return out
                name = str(item.get('path'))
                member = by_name.get(name)
                if member is None:
                    out['error'] = f'manifest names a missing file: {name}'
                    return out
                file_handle = tar.extractfile(member)
                if file_handle is None:
                    out['error'] = f'cannot read archived file: {name}'
                    return out
                data = file_handle.read()
                if hashlib.sha256(data).hexdigest() != item.get('sha256') or len(data) != item.get(
                    'size'
                ):
                    out['error'] = f'archived file does not match its manifest hash: {name}'
                    return out
                total += len(data)
            roots = manifest.get('roots')
            skipped = manifest.get('skipped')
            out['verified'] = True
            out['files'] = len(listed)
            out['bytes'] = total
            out['roots'] = len(roots) if isinstance(roots, list) else 0
            out['skipped'] = len(skipped) if isinstance(skipped, list) else 0
            # The manifest's own truncation flag: a verified archive can still
            # be incomplete, and "verified" must not read as "complete".
            out['truncated'] = bool(manifest.get('truncated'))
            return out
    except (tarfile.TarError, OSError, EOFError, ValueError, UnicodeDecodeError, MemoryError) as exc:
        out['error'] = str(exc)
        return out


def _archive_skills(
    target: Path,
    *,
    db_name: str,
    db_bytes: int,
    db_version: int,
) -> dict[str, object]:
    """Write and verify the skills half of a backup. Never raises.

    A failure here is reported, not propagated: the record copy this runs
    beside is already verified and must survive a skills-tree problem. An
    archive that fails its own verification is deleted, so a later list can
    never show it as good.
    """
    try:
        from app.services.skill_service import all_skill_roots

        roots = all_skill_roots()
    except Exception as exc:  # noqa: BLE001 — a discovery failure must not cost the verified record copy
        return {'archived': False, 'error': f'skill root discovery failed: {exc}'}
    if not roots:
        return {'archived': False, 'reason': 'no skill roots found'}
    try:
        _write_skills_archive(
            target, roots, db_name=db_name, db_bytes=db_bytes, db_version=db_version
        )
    except (OSError, tarfile.TarError, MemoryError) as exc:
        target.unlink(missing_ok=True)
        return {'archived': False, 'error': f'archive write failed: {exc}'}
    verdict = verify_skills_archive(target)
    if not verdict.get('verified'):
        target.unlink(missing_ok=True)
        return {
            'archived': False,
            'error': f'archive failed verification: {verdict.get("error") or "unknown"}',
        }
    try:
        size = target.stat().st_size
    except OSError as exc:
        return {'archived': False, 'error': f'archive stat failed: {exc}'}
    return {
        'archived': True,
        'name': target.name,
        'bytes': size,
        'files': verdict.get('files', 0),
        'roots': verdict.get('roots', 0),
        'skipped': verdict.get('skipped', 0),
        'truncated': bool(verdict.get('truncated')),
    }


def _write_brain_archive(target: Path, sources: list[tuple[str, Path]]) -> dict[str, object]:
    """Write the record ``tar.gz`` and its manifest. Returns the manifest."""
    entries: list[tuple[str, Path]] = []
    for name, root in sources:
        if root.is_dir():
            for src in _iter_archive_files(root):
                try:
                    rel = src.relative_to(root).as_posix()
                except ValueError:
                    continue
                entries.append((f'{name}/{rel}', src))
        else:
            entries.append((name, root))
    entries.sort(key=lambda item: item[0])

    files: list[dict[str, object]] = []
    skipped: list[dict[str, str]] = []
    truncated = False
    total_bytes = 0
    with tarfile.open(str(target), 'w:gz') as tar:
        for arcname, src in entries:
            if len(files) >= _ARCHIVE_MAX_FILES:
                truncated = True
                logger.warning(
                    'brain archive hit the %d-file cap — files beyond it are not backed up',
                    _ARCHIVE_MAX_FILES,
                )
                break
            try:
                stat = src.stat()
            except OSError as exc:
                truncated = True
                skipped.append({'path': str(src), 'reason': str(exc)})
                continue
            size = stat.st_size
            if size > _ARCHIVE_MAX_FILE_BYTES:
                truncated = True
                skipped.append(
                    {
                        'path': str(src),
                        'reason': f'file exceeds the {_ARCHIVE_MAX_FILE_BYTES}-byte per-file cap',
                    }
                )
                continue
            if total_bytes + size > _ARCHIVE_MAX_TOTAL_BYTES:
                truncated = True
                skipped.append(
                    {
                        'path': str(src),
                        'reason': f'archive exceeds the {_ARCHIVE_MAX_TOTAL_BYTES}-byte total cap',
                    }
                )
                break
            try:
                data = src.read_bytes()
            except (OSError, MemoryError) as exc:
                truncated = True
                skipped.append({'path': str(src), 'reason': str(exc)})
                continue
            info = tarfile.TarInfo(name=arcname)
            info.size = len(data)
            info.mtime = int(stat.st_mtime)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
            files.append(
                {'path': arcname, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            )
            total_bytes += len(data)
        manifest: dict[str, object] = {
            'format': _BRAIN_MANIFEST_FORMAT,
            'createdAt': datetime.now(timezone.utc).isoformat(),
            'sources': [
                {'name': name, 'source': str(root)} for name, root in sources
            ],
            'files': files,
            'skipped': skipped,
            'truncated': truncated,
            'totals': {'files': len(files), 'bytes': total_bytes},
        }
        raw = json.dumps(manifest, indent=2, ensure_ascii=False).encode('utf-8')
        info = tarfile.TarInfo(name=_BRAIN_MANIFEST_NAME)
        info.size = len(raw)
        info.mtime = int(time.time())
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(raw))
    return manifest


def verify_brain_archive(path: Path) -> dict[str, object]:
    """Re-open the record archive and verify every SHA-256 (the retired
    ``PRAGMA integrity_check``, in the archive's own terms).

    The container's gzip CRC is checked by the streamed read; the per-file
    hash against the manifest is what proves the CONTENT is the record that
    was archived. Never raises — a verdict is a dict with ``verified``.
    """
    out: dict[str, object] = {
        'verified': False,
        'files': 0,
        'bytes': 0,
        'sources': 0,
        'skipped': 0,
        'truncated': False,
    }
    try:
        with tarfile.open(str(path), 'r:gz') as tar:
            by_name = {member.name: member for member in tar.getmembers()}
            manifest_member = by_name.get(_BRAIN_MANIFEST_NAME)
            if manifest_member is None:
                out['error'] = 'archive carries no manifest'
                return out
            handle = tar.extractfile(manifest_member)
            if handle is None:
                out['error'] = 'manifest is not a regular file'
                return out
            manifest = json.loads(handle.read().decode('utf-8'))
            if not isinstance(manifest, dict):
                out['error'] = 'manifest is not an object'
                return out
            listed = manifest.get('files')
            if not isinstance(listed, list):
                out['error'] = 'manifest has no file list'
                return out
            expected = {_BRAIN_MANIFEST_NAME}
            for item in listed:
                if isinstance(item, dict) and isinstance(item.get('path'), str):
                    expected.add(item['path'])
            if set(by_name) != expected:
                out['error'] = 'archive members do not match the manifest'
                return out
            total = 0
            for item in listed:
                if not isinstance(item, dict):
                    out['error'] = 'manifest file entry is not an object'
                    return out
                name = str(item.get('path'))
                member = by_name.get(name)
                if member is None:
                    out['error'] = f'manifest names a missing file: {name}'
                    return out
                file_handle = tar.extractfile(member)
                if file_handle is None:
                    out['error'] = f'cannot read archived file: {name}'
                    return out
                data = file_handle.read()
                if hashlib.sha256(data).hexdigest() != item.get('sha256') or len(data) != item.get(
                    'size'
                ):
                    out['error'] = f'archived file does not match its manifest hash: {name}'
                    return out
                total += len(data)
            sources = manifest.get('sources')
            skipped = manifest.get('skipped')
            out['verified'] = True
            out['files'] = len(listed)
            out['bytes'] = total
            out['sources'] = len(sources) if isinstance(sources, list) else 0
            out['skipped'] = len(skipped) if isinstance(skipped, list) else 0
            out['truncated'] = bool(manifest.get('truncated'))
            return out
    except (tarfile.TarError, OSError, EOFError, ValueError, UnicodeDecodeError, MemoryError) as exc:
        out['error'] = str(exc)
        return out


def create_backup(reason: str = 'manual') -> dict[str, object]:
    """Take a verified offline copy of the brain record and the skills tree.

    The record copy is a tar.gz of ``memory/`` + ``<state>/`` + ``config.json``
    with a SHA-256 manifest; it is re-opened and hash-verified before it is
    reported as good. The skills archive is built and verified the same way,
    and shares this backup's stamp and retention slot.
    """
    slug = re.sub(r'[^a-z0-9-]+', '-', str(reason or 'manual').lower()).strip('-')[:20] or 'manual'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    name = f'brain-{stamp}-{slug}.tar.gz'
    directory = backups_dir()
    sources = _record_sources()
    if not sources:
        return {'ok': False, 'error': 'no brain record to back up yet'}
    verified = False
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name
        _write_brain_archive(target, sources)
        verdict = verify_brain_archive(target)
        if not verdict.get('verified'):
            target.unlink(missing_ok=True)
            return {'ok': False, 'error': f'backup failed verification: {verdict.get("error") or "unknown"}'}
        if verdict.get('truncated'):
            target.unlink(missing_ok=True)
            return {'ok': False, 'error': 'backup failed verification: archive is incomplete'}
        # Past this line the copy is proven good, so the cleanup in the except
        # must leave it alone: `_prune` failing afterwards is not a reason to
        # delete a backup that verification already passed.
        verified = True
        removed = _prune(directory)
        db_bytes = target.stat().st_size
        # The skills archive is the second half of this backup. It is built
        # only after the record copy is proven good, so a failure here can
        # never cost the memory copy — it is reported in `skills`/`skillsError`
        # instead of failing a backup that actually succeeded.
        skills = _archive_skills(
            directory / f'{SKILLS_ARCHIVE_PREFIX}{stamp}-{slug}{SKILLS_ARCHIVE_SUFFIX}',
            db_name=name,
            db_bytes=db_bytes,
            db_version=1,
        )
        result: dict[str, object] = {
            'ok': True,
            'name': name,
            'bytes': db_bytes,
            'appliedVersion': 1,
            'pruned': removed,
            'skills': skills,
        }
        if skills.get('error'):
            result['skillsError'] = str(skills['error'])
            logger.warning('Skills archive for %s failed: %s', name, skills['error'])
        return result
    except (tarfile.TarError, OSError, MemoryError) as exc:
        logger.warning('Brain backup failed: %s', exc)
        # A failed archive leaves a husk no list may show as good. Only an
        # unverified copy is removed — never a good backup.
        if not verified:
            try:
                (directory / name).unlink(missing_ok=True)
            except OSError as cleanup_exc:
                logger.warning('Could not remove unfinished backup %s: %s', name, cleanup_exc)
        return {'ok': False, 'error': str(exc)}


def _archive_entry(path: Path) -> dict[str, object]:
    """One skills archive as the backup list shows it — verified on the spot."""
    entry: dict[str, object] = {'name': path.name, 'verified': False, 'truncated': False}
    try:
        entry['bytes'] = path.stat().st_size
    except OSError as exc:
        entry['error'] = str(exc)
        return entry
    verdict = verify_skills_archive(path)
    entry['verified'] = bool(verdict.get('verified'))
    entry['files'] = verdict.get('files', 0)
    entry['roots'] = verdict.get('roots', 0)
    entry['skipped'] = verdict.get('skipped', 0)
    entry['truncated'] = bool(verdict.get('truncated'))
    if not verdict.get('verified'):
        entry['error'] = str(verdict.get('error') or 'verification failed')
    return entry


def list_backups() -> list[dict[str, object]]:
    """Newest first, each labelled with its own health — a stale or corrupt
    backup is not a way out.

    Each entry carries its skills archive (``skills``) when the pair exists,
    verified the same way on the spot; ``healthy`` stays the record's own
    verdict so an archive problem can never make a restorable backup look
    unrestorable. Legacy SQL-era copies (``brain-*.sqlite``) list with
    ``healthy: false`` and the retired-engine reason — they hold user data and
    stay on disk, but the engine that could open them is gone.
    """
    directory = backups_dir()
    if not directory.is_dir():
        return []
    archives: dict[str, Path] = {}
    for path in directory.glob(f'{SKILLS_ARCHIVE_PREFIX}*{SKILLS_ARCHIVE_SUFFIX}'):
        key = _backup_key(path.name)
        if key:
            archives[key] = path
    out: list[dict[str, object]] = []
    seen_keys: set[str] = set()
    for path in sorted(
        [*directory.glob('brain-*.tar.gz'), *directory.glob('brain-*.sqlite')],
        key=lambda p: p.name,
        reverse=True,
    ):
        key = _backup_key(path.name)
        if key and key in seen_keys:
            # A stamp+slug can exist as both a legacy .sqlite and a new
            # .tar.gz only across the engine swap; the new format wins.
            continue
        if key:
            seen_keys.add(key)
        entry: dict[str, object] = {
            'name': path.name,
            'reason': reason_of(path.name),
            'bytes': path.stat().st_size,
            'createdAt': datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            'healthy': False,
            'appliedVersion': 0,
            'fromTheFuture': False,
        }
        archive = archives.get(key) if key else None
        if archive is not None:
            entry['skills'] = _archive_entry(archive)
        if _LEGACY_BACKUP_NAME_RE.match(path.name):
            entry['error'] = (
                'legacy sqlite backup — the SQL engine is retired; the memory it '
                'holds was imported into the file record, and this copy is kept as bytes only'
            )
        else:
            verdict = verify_brain_archive(path)
            entry['healthy'] = bool(verdict.get('verified')) and not verdict.get('truncated')
            entry['appliedVersion'] = 1 if entry['healthy'] else 0
            if not entry['healthy']:
                entry['error'] = str(verdict.get('error') or 'verification failed')
        out.append(entry)
    return out


def _validate_restore_target(name: str) -> tuple[Path | None, str | None]:
    """Return (path, error). Rejects anything that is not a well-named backup in
    the backups directory, and any copy that is unhealthy."""
    if not _BACKUP_NAME_RE.match(name or ''):
        if _LEGACY_BACKUP_NAME_RE.match(name or ''):
            return None, (
                'legacy sqlite backups cannot be restored — the SQL engine is retired; '
                'the memory they held was imported into the file record'
            )
        return None, 'not a valid backup name'
    path = backups_dir() / name
    if not path.is_file():
        return None, f'no backup named {name}'
    verdict = verify_brain_archive(path)
    if not verdict.get('verified'):
        return None, f"backup is not healthy: {verdict.get('error') or 'verification failed'}"
    if verdict.get('truncated'):
        return None, 'backup is not healthy: archive is incomplete'
    return path, None


def schedule_restore(name: str) -> dict[str, object]:
    """Stage a verified restore for the next launch."""
    path, error = _validate_restore_target(name)
    if error or path is None:
        return {'ok': False, 'error': error or 'invalid backup'}
    pending = backups_dir() / PENDING_FILE
    try:
        pending.write_text(path.name, encoding='utf-8')
    except OSError as exc:
        return {'ok': False, 'error': str(exc)}
    return {
        'ok': True,
        'name': path.name,
        'appliesOn': 'next-launch',
        'note': 'The running app holds the record files open, so the swap happens at '
        'startup. Restart August to apply it; until then this can be cancelled.',
    }


def cancel_restore() -> dict[str, object]:
    pending = backups_dir() / PENDING_FILE
    if not pending.exists():
        return {'ok': True, 'cancelled': False}
    try:
        pending.unlink()
    except OSError as exc:
        return {'ok': False, 'error': str(exc)}
    return {'ok': True, 'cancelled': True}


def pending_restore() -> str | None:
    pending = backups_dir() / PENDING_FILE
    try:
        text = pending.read_text(encoding='utf-8').strip()
    except OSError:
        return None
    return text if _BACKUP_NAME_RE.match(text) else None


def apply_pending_restore() -> dict[str, object]:
    """Swap a staged backup into place. Call before any store opens.

    The record only. The skills archive beside a backup is an offline copy
    this function does not extract (see the module docstring): a restore puts
    the memory back and leaves the live skills tree alone.

    Keeps one ``.pre-restore`` copy of everything it replaced, so a restore
    that turns out to be the wrong backup is itself undoable. Any failure
    *before the first swap* leaves the marker in place and the current record
    untouched — a failed restore must never cost the memory that still exists.
    Once the first piece has swapped in, the restore is reported as applied
    whatever cleanup does, because re-running it on the next boot would copy
    the restored record over the ``.pre-restore`` copies and destroy the only
    copy of what was replaced.
    """
    name = pending_restore()
    if not name:
        return {'ok': True, 'applied': False}
    data = _data_dir()
    target, error = _validate_restore_target(name)
    if error or target is None:
        logger.error('Staged brain restore "%s" refused: %s — keeping the current record', name, error)
        return {'ok': False, 'applied': False, 'error': error}
    staging = data / '.brain-restoring'
    swapped: list[str] = []
    warnings: list[str] = []
    try:
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        with tarfile.open(str(target), 'r:gz') as tar:
            tar.extractall(staging, filter='data')
    except (tarfile.TarError, OSError) as exc:
        logger.error('Brain restore failed for %s: %s', name, exc)
        shutil.rmtree(staging, ignore_errors=True)
        return {'ok': False, 'applied': False, 'error': str(exc)}

    try:
        for source_name, _src in _record_sources():
            incoming = staging / source_name
            if not incoming.exists():
                continue
            live = data / source_name
            aside = data / f'{source_name}{PRE_RESTORE_SUFFIX}'
            if live.exists():
                # Keep the pre-restore copy of what is being replaced. The
                # rename is atomic per entry; a failure here rolls the asides
                # of the entries that already moved back into place.
                if aside.exists():
                    shutil.rmtree(aside, ignore_errors=True) if aside.is_dir() else aside.unlink(missing_ok=True)
                os.replace(live, aside)
            os.replace(incoming, live)
            swapped.append(source_name)
        if not swapped:
            raise OSError('archive held none of the record sources')
    except OSError as exc:
        # Nothing or part of it landed. Roll every swapped entry back from its
        # aside — a half-applied restore that reports failure is recoverable;
        # one that reports failure while holding half the new record is not.
        for source_name in swapped:
            live = data / source_name
            aside = data / f'{source_name}{PRE_RESTORE_SUFFIX}'
            try:
                if live.exists():
                    shutil.rmtree(live, ignore_errors=True) if live.is_dir() else live.unlink(missing_ok=True)
                if aside.exists():
                    os.replace(aside, live)
            except OSError as cleanup_exc:
                logger.error(
                    'Brain restore of %s failed and the rollback of %s also failed: %s — '
                    'the pre-restore copy is at %s',
                    name, source_name, cleanup_exc, aside,
                )
                warnings.append(f'{source_name} not rolled back: {cleanup_exc}')
        logger.error('Brain restore failed for %s: %s', name, exc)
        shutil.rmtree(staging, ignore_errors=True)
        if warnings:
            return {'ok': False, 'applied': False, 'error': str(exc), 'warning': '; '.join(warnings)}
        return {'ok': False, 'applied': False, 'error': str(exc)}

    # The swap has landed — from here every step is cleanup, and none of it may
    # turn the result back into a failure (see the docstring).
    try:
        (backups_dir() / PENDING_FILE).unlink(missing_ok=True)
    except OSError as cleanup_exc:
        # The staged restore is already in place; a marker that survives means
        # the next boot repeats a restore that already happened.
        logger.warning('Brain restore applied but marker for %s could not be cleared: %s', name, cleanup_exc)
        warnings.append(f'marker not cleared: {cleanup_exc}')
    shutil.rmtree(staging, ignore_errors=True)
    logger.info('Brain record restored from %s (previous copy at <record>.pre-restore)', name)
    result: dict[str, object] = {'ok': True, 'applied': True, 'name': name}
    if warnings:
        result['warning'] = '; '.join(warnings)
    return result


def ensure_current_backup(max_age_hours: float = 12.0, reason: str = 'startup') -> dict[str, object]:
    """Take a backup only when the newest one has gone stale.

    Called once at startup, after the one-time import, so an upgrade always
    leaves a verified copy behind.
    """
    existing = list_backups()
    if existing:
        newest = str(existing[0].get('createdAt') or '')
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(newest)).total_seconds()
            if age < max_age_hours * 3600:
                return {'ok': True, 'skipped': True, 'ageSeconds': int(age)}
        except ValueError:
            pass  # unparseable stamp — treat as stale and back up
    return create_backup(reason=reason)


def quick_check() -> dict[str, object]:
    """Health of the live record, for the settings/health surface."""
    sources = _record_sources()
    if not sources:
        return {'ok': True, 'exists': False, 'detail': 'no record yet'}
    try:
        from app.services.memory_store.mirror import record_stats

        detail = f"record: {record_stats()['rows']} fact file(s), {len(sources)} source tree(s)"
    except Exception as exc:  # noqa: BLE001 — the health read must never raise
        detail = f'record unreadable: {exc}'
    backups = list_backups()
    return {
        'ok': detail.startswith('record:'),
        'exists': True,
        'detail': detail,
        'path': str(_data_dir()),
        'backups': len([b for b in backups if b.get('healthy')]),
        'pendingRestore': pending_restore(),
    }
