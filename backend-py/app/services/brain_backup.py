"""Offline copies of the brain database and the skills tree: verify, back up,
and restore the DATABASE safely.

The skills archive is a verified offline COPY, not a second restore target:
it is written and hash-checked on every backup, but ``apply_pending_restore``
swaps only the database. Putting a skill tree back is extracting the
``skills-*.tar.gz`` beside the copy by hand — the archive carries its manifest,
so that extraction is self-describing.

The brain SQLite file is the user's memory. Before this module the only copy
the app ever made was ``<db>.pre-migration`` — one rolling file that nothing
reads back, taken only when a migration was pending. A corrupted or
accidentally-deleted database therefore had no recovery path at all.

The skills tree is the user's *procedure* memory, and it had no offline copy at
all: every skill write is auto-applied (``skillAutonomy`` defaults on), so a
bad bulk edit lands with no human step and the only undo is the per-skill
``.versions`` snapshot taken at write time. Since 2026-11 the same verified
rolling copy includes a ``tar.gz`` of the durable skill roots, recorded in an
archive manifest beside the database copy's name.

Three constraints shape the design:

* **A restore cannot happen while the app is running.** ``memory_store.close()``
  closes only the *thread-local* connection; other threads keep handles on the
  live file, and replacing it underneath them yields half-old, half-new reads
  (and on Windows, a failed rename). So a restore is *staged* and applied at the
  next startup, before any connection exists — and the response says so.
* **The pending marker is a file, not a row.** The situation where a restore is
  needed is precisely the one where the database may not open.
* **Both halves are verified before either is trusted.** The database copy is
  opened and ``PRAGMA integrity_check``-ed; the skills archive is re-read and
  every file's SHA-256 is checked against the manifest written inside it. An
  unverified artifact is deleted, never listed as good.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
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
# stamp of the database copy beside it. The pair is one backup — prune and list
# treat the stamp as the unit.
SKILLS_ARCHIVE_PREFIX: Final = 'skills-'
SKILLS_ARCHIVE_SUFFIX: Final = '.tar.gz'
_ARCHIVE_NAME_RE: Final = re.compile(
    r'^skills-(\d{8}T\d{6}Z)-([a-z0-9][a-z0-9-]{0,20})\.tar\.gz$'
)
# The manifest is the archive's own record of what it holds: the database copy
# it belongs to, the roots it came from, and one SHA-256 per archived file.
_MANIFEST_NAME: Final = '__august_manifest__.json'
_MANIFEST_FORMAT: Final = 'august-skills-archive/1'
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
_BACKUP_NAME_RE: Final = re.compile(r'^brain-\d{8}T\d{6}Z-[a-z0-9][a-z0-9-]{0,20}\.sqlite$')
# The same name split into its two parts, so a reader gets WHEN and WHY without
# parsing a filename. `create_backup` is the only writer, and this accepts every
# slug it can produce.
_BACKUP_NAME_PARTS: Final = re.compile(
    r'^brain-(\d{8})T(\d{6})Z-([a-z0-9][a-z0-9-]{0,20})\.sqlite$'
)


def reason_of(name: str) -> str:
    """Why a copy was taken, as the slug ``create_backup`` wrote into its name.

    The filename is the only durable record of the reason, so this is the door
    the settings UI reads through: showing the raw name asked the user to
    decode ``brain-20260924T063947Z-startup.sqlite`` when "Auto-copy at startup,
    Sep 24 6:39 AM" is the same fact in words. '' when the name is not ours.
    """
    match = _BACKUP_NAME_PARTS.match(name or '')
    return match.group(3) if match else ''


def _db_path() -> Path:
    from app.services.memory_conn import db_path

    return db_path()


def backups_dir() -> Path:
    return _db_path().parent / BACKUP_SUBDIR


def _open_read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _check(conn: sqlite3.Connection) -> str:
    """``integrity_check`` result, or an error string — never an exception."""
    try:
        row = conn.execute('PRAGMA integrity_check').fetchone()
        return str(row[0]) if row else 'no result'
    except sqlite3.Error as exc:
        return f'check failed: {exc}'


def _content_reason(conn: sqlite3.Connection) -> str | None:
    """Why this copy cannot hold memory, or None when it provably can.

    ``PRAGMA integrity_check`` is not proof of content: a zero-byte file is a
    well-formed SQLite image (it reports ``ok`` with ``page_count`` 0 and no
    tables), so without this gate an empty copy is written by a failed backup,
    listed as healthy, offered in Settings, and restores as an amnesic
    database. One predicate, shared by create/list/restore so they cannot drift.
    """
    try:
        row = conn.execute('PRAGMA page_count').fetchone()
        pages = int(row[0]) if row and row[0] is not None else 0
    except sqlite3.Error:
        return 'copy is unreadable'
    if pages < 1:
        return 'copy holds no pages'
    try:
        tables = {
            str(t[0])
            for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    except sqlite3.Error:
        return 'copy has no readable schema'
    if not tables:
        return 'copy has no tables'
    if 'schema_migrations' not in tables and 'facts' not in tables:
        return 'copy has no brain tables'
    return None


def _applied_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute('SELECT MAX(version) FROM schema_migrations').fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row and row[0] is not None else 0


def _code_version() -> int:
    """Highest migration this build knows how to apply.

    Read from the migration runner's own discovery so the two can never drift:
    a backup from a NEWER app is refused rather than booted and silently
    reported "up to date".
    """
    from app.lib.migrations import _discover_migrations

    found = _discover_migrations()
    return found[-1][0] if found else 0


def _backup_key(name: str) -> str:
    """The ``<stamp>-<slug>`` key that pairs both halves of one backup.

    The stamp alone is NOT the unit: ``create_backup`` can run twice inside one
    second (manual + startup, a bulk caller), and two copies that share a stamp
    but differ in slug are two backups, not one. The key sorts in the same
    order as the filename, so retention keeps exactly the files the old
    filename sort kept.
    """
    match = _BACKUP_NAME_PARTS.match(name)
    if match:
        return f'{match.group(1)}T{match.group(2)}Z-{match.group(3)}'
    match = _ARCHIVE_NAME_RE.match(name)
    if match:
        return f'{match.group(1)}-{match.group(2)}'
    return ''


def _prune(directory: Path) -> list[str]:
    """Delete every artifact of all but the newest ``KEEP_BACKUPS`` backups.

    Both halves of a backup — the database copy and its skills archive — are
    pruned by the SAME key, so a kept database never loses its archive and a
    pruned one never leaves an orphan behind. Returns removed names.
    """
    pairs: list[tuple[str, Path]] = []
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

    A failure here is reported, not propagated: the database copy this runs
    beside is already verified and must survive a skills-tree problem. An
    archive that fails its own verification is deleted, so a later list can
    never show it as good.
    """
    try:
        from app.services.skill_service import all_skill_roots

        roots = all_skill_roots()
    except Exception as exc:  # noqa: BLE001 — a discovery failure must not cost the verified DB copy
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


def create_backup(reason: str = 'manual') -> dict[str, object]:
    """Take a verified online copy of the brain DB and the durable skills tree.

    Uses SQLite's backup API against a read-only handle, so a WAL-mode database
    is copied consistently without a checkpoint or a write lock on the live
    file. The copy is checked before it is reported as good. The skills archive
    is built and verified the same way, and shares this backup's stamp and
    retention slot.
    """
    source = _db_path()
    slug = re.sub(r'[^a-z0-9-]+', '-', str(reason or 'manual').lower()).strip('-')[:20] or 'manual'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    name = f'brain-{stamp}-{slug}.sqlite'
    directory = backups_dir()
    if not source.exists():
        return {'ok': False, 'error': f'no brain database at {source}'}
    verified = False
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name
        src = _open_read_only(source)
        try:
            dst = sqlite3.connect(str(target))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        check_conn = _open_read_only(target)
        try:
            check = _check(check_conn)
            empty = None if check != 'ok' else _content_reason(check_conn)
        finally:
            check_conn.close()
        if check != 'ok':
            target.unlink(missing_ok=True)
            return {'ok': False, 'error': f'backup failed verification: {check}'}
        if empty is not None:
            # Refusing here also stops the next prune/ensure cycle mistaking a
            # husk for the newest good copy.
            target.unlink(missing_ok=True)
            return {'ok': False, 'error': f'backup failed verification: {empty}'}
        # Past this line the copy is proven good, so the cleanup in the except
        # must leave it alone: `_prune` or the version read failing afterwards
        # is not a reason to delete a backup that verification already passed.
        verified = True
        removed = _prune(directory)
        verify_conn = _open_read_only(target)
        try:
            version = _applied_version(verify_conn)
        finally:
            verify_conn.close()
        db_bytes = target.stat().st_size
        # The skills archive is the second half of this backup. It is built
        # only after the database copy is proven good, so a failure here can
        # never cost the memory copy — it is reported in `skills`/`skillsError`
        # instead of failing a backup that actually succeeded.
        skills = _archive_skills(
            directory / f'{SKILLS_ARCHIVE_PREFIX}{stamp}-{slug}{SKILLS_ARCHIVE_SUFFIX}',
            db_name=name,
            db_bytes=db_bytes,
            db_version=version,
        )
        result: dict[str, object] = {
            'ok': True,
            'name': name,
            'bytes': db_bytes,
            'appliedVersion': version,
            'pruned': removed,
            'skills': skills,
        }
        if skills.get('error'):
            result['skillsError'] = str(skills['error'])
            logger.warning('Skills archive for %s failed: %s', name, skills['error'])
        return result
    except (sqlite3.Error, OSError, MemoryError) as exc:
        logger.warning('Brain backup failed: %s', exc)
        # ``sqlite3.connect`` creates the file before anything is written to it,
        # so a failure here leaves a zero-byte husk that integrity_check calls
        # "ok". Only an unverified copy is removed — never a good backup.
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
    verified the same way on the spot; ``healthy`` stays the database's own
    verdict so an archive problem can never make a restorable database look
    unrestorable.
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
    for path in sorted(directory.glob('brain-*.sqlite'), key=lambda p: p.name, reverse=True):
        entry: dict[str, object] = {
            'name': path.name,
            'reason': reason_of(path.name),
            'bytes': path.stat().st_size,
            'createdAt': datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            'healthy': False,
            'appliedVersion': 0,
            'fromTheFuture': False,
        }
        key = _backup_key(path.name)
        archive = archives.get(key) if key else None
        if archive is not None:
            entry['skills'] = _archive_entry(archive)
        try:
            conn = _open_read_only(path)
            try:
                check = _check(conn)
                empty = None if check != 'ok' else _content_reason(conn)
                entry['healthy'] = check == 'ok' and empty is None
                if check != 'ok':
                    entry['error'] = check
                elif empty is not None:
                    entry['error'] = empty
                version = _applied_version(conn)
                entry['appliedVersion'] = version
                entry['fromTheFuture'] = version > _code_version()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            entry['error'] = str(exc)
        out.append(entry)
    return out


def _validate_restore_target(name: str) -> tuple[Path | None, str | None]:
    """Return (path, error). Rejects anything that is not a well-named backup in
    the backups directory, and any copy that is unhealthy or newer than this
    build."""
    if not _BACKUP_NAME_RE.match(name or ''):
        return None, 'not a valid backup name'
    path = backups_dir() / name
    if not path.is_file():
        return None, f'no backup named {name}'
    try:
        conn = _open_read_only(path)
    except sqlite3.Error as exc:
        return None, f'cannot open backup: {exc}'
    try:
        check = _check(conn)
        if check != 'ok':
            return None, f'backup is not healthy: {check}'
        empty = _content_reason(conn)
        if empty is not None:
            return None, f'backup is not healthy: {empty}'
        version = _applied_version(conn)
        limit = _code_version()
        if version > limit:
            return None, (
                f'backup is at schema {version:03d}, this build knows up to {limit:03d}; '
                'restore it with the app version that made it'
            )
    finally:
        conn.close()
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
        'note': 'The running app holds the database open, so the swap happens at '
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
    """Swap a staged backup into place. Call before any brain connection exists.

    The database only. The skills archive beside a backup is an offline copy
    this function does not extract (see the module docstring): a restore puts
    the memory back and leaves the live skills tree alone.

    Keeps one ``.pre-restore`` copy of the database it replaced, so a restore
    that turns out to be the wrong backup is itself undoable. Any failure *up
    to the swap* leaves the marker in place and the current database untouched
    — a failed restore must never cost the memory that still exists. Once the
    swap has landed the restore is reported as applied whatever cleanup does,
    because re-running it on the next boot would copy the restored database
    over ``.pre-restore`` and destroy the only copy of what was replaced.
    """
    name = pending_restore()
    if not name:
        return {'ok': True, 'applied': False}
    source = _db_path()
    target, error = _validate_restore_target(name)
    if error or target is None:
        logger.error('Staged brain restore "%s" refused: %s — keeping the current database', name, error)
        return {'ok': False, 'applied': False, 'error': error}
    staging = source.with_name(f'{source.name}.restoring')
    try:
        if source.exists():
            # Keep the pre-restore database *with* its WAL. Uncheckpointed
            # commits live only in -wal, so a main-only .pre-restore cannot put
            # that memory back — and the sidecars used to be deleted here,
            # before the new main file had even landed.
            shutil.copy2(source, f'{source}{PRE_RESTORE_SUFFIX}')
            for suffix in ('-wal', '-shm'):
                side = Path(f'{source}{suffix}')
                if side.exists():
                    shutil.copy2(side, f'{source}{PRE_RESTORE_SUFFIX}{suffix}')
        # Copy beside the live file and publish it with os.replace. A failure
        # part-way through then leaves the current database untouched, where
        # writing it in place could truncate the only copy of the user's memory.
        shutil.copy2(target, staging)
        os.replace(staging, source)
    except OSError as exc:
        logger.error('Brain restore failed for %s: %s', name, exc)
        try:
            staging.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            logger.warning('Could not remove staging file for %s: %s', name, cleanup_exc)
        return {'ok': False, 'applied': False, 'error': str(exc)}

    # The swap has landed — from here every step is cleanup, and none of it may
    # turn the result back into a failure (see the docstring).
    warnings: list[str] = []
    try:
        (backups_dir() / PENDING_FILE).unlink(missing_ok=True)
    except OSError as cleanup_exc:
        # The staged restore is already in place; a marker that survives means
        # the next boot repeats a restore that already happened.
        logger.warning('Brain restore applied but marker for %s could not be cleared: %s', name, cleanup_exc)
        warnings.append(f'marker not cleared: {cleanup_exc}')
    for suffix in ('-wal', '-shm'):
        # A swapped-in main must not be replayed against the *previous*
        # database's WAL, so the old sidecars go only now that it landed.
        try:
            Path(f'{source}{suffix}').unlink(missing_ok=True)
        except OSError as cleanup_exc:
            logger.error(
                'Brain restore applied but stale %s%s survived: %s — remove it '
                'before the next open or the restored database can be corrupted',
                source.name, suffix, cleanup_exc,
            )
            warnings.append(f'stale {suffix} not removed: {cleanup_exc}')
    logger.info('Brain database restored from %s (previous copy at %s%s)', name, source.name, PRE_RESTORE_SUFFIX)
    result: dict[str, object] = {'ok': True, 'applied': True, 'name': name}
    if warnings:
        result['warning'] = '; '.join(warnings)
    return result


def ensure_current_backup(max_age_hours: float = 12.0, reason: str = 'startup') -> dict[str, object]:
    """Take a backup only when the newest one has gone stale.

    Called once at startup, after migrations, so an upgrade always leaves a
    verified copy behind. Deliberately not wired into the migration snapshot
    itself: that runs inside an open write transaction, and SQLite's backup API
    needs a read lock — the same collision that made the old snapshot hang a
    boot on a busy database.
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
    """Health of the live database, for the settings/health surface."""
    source = _db_path()
    if not source.exists():
        return {'ok': True, 'exists': False, 'detail': 'no database yet'}
    try:
        conn = _open_read_only(source)
        try:
            detail = _check(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        detail = f'cannot open: {exc}'
    backups = list_backups()
    return {
        'ok': detail == 'ok',
        'exists': True,
        'detail': detail,
        'path': str(source),
        'backups': len([b for b in backups if b.get('healthy')]),
        'pendingRestore': pending_restore(),
    }
