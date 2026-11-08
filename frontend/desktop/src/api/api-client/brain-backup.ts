/* ── Brain-database backups ──────────────────────────────────────────── */
/* Wrappers for the /api/brain backup surface (backend: app/services/
 * brain_backup.py, wired in app/routers/brain_config.py).
 *
 * One authority rule applies here: the server health-checks every copy in
 * `listBrainBackups()` and owns what is staged for restore
 * (`pendingRestore`). Callers must read both from the response, never keep a
 * local guess — a restore only applies at next launch, so the staged state
 * survives a remount and component state would be the wrong home for it. */

import { api } from '../client';

/** One offline copy of the brain DB, already health-checked server-side.
 *  `healthy` is that copy's own `PRAGMA integrity_check` verdict and
 *  `fromTheFuture` means its schema version is newer than this build knows. */
export interface BrainBackupEntry {
  name: string;
  /** Why the copy exists, parsed from its name by the server (`startup`,
   *  `manual`, …) — '' when the name is not one this module wrote. The name is
   *  an identifier; this is the field a label is built from. */
  reason?: string;
  bytes: number;
  /** ISO timestamp of the copy (file mtime). */
  createdAt: string;
  healthy: boolean;
  appliedVersion: number;
  fromTheFuture: boolean;
  /** Present only when the copy could not be opened or verified. */
  error?: string;
  /** The skills half of this backup, verified on the spot by the server.
   *  Absent when the pair has no archive (older backups, or one that failed). */
  skills?: BrainSkillsArchiveEntry;
}

/** One skills archive as the backup list shows it — verified on the spot. */
export interface BrainSkillsArchiveEntry {
  name: string;
  verified: boolean;
  bytes?: number;
  files?: number;
  roots?: number;
  skipped?: number;
  /** Verified but incomplete: a cap or an unreadable file left gaps, recorded
   *  in the archive manifest. Never read "verified" as "complete". */
  truncated?: boolean;
  error?: string;
}

export interface BrainBackupList {
  backups: BrainBackupEntry[];
  /** Retention rule: only the newest `keep` copies survive a backup. */
  keep: number;
  /** Backup name staged to apply on next launch, or null. */
  pendingRestore: string | null;
  directory: string;
}

export interface BrainBackupCreated {
  ok: boolean;
  name?: string;
  bytes?: number;
  appliedVersion?: number;
  /** Older copies the retention rule retired by this call. */
  pruned?: string[];
  /** The skills half's verdict. The DB copy can be good while this failed:
   *  `archived: false` means the memory copy is safe but the skills archive
   *  is not — `error`/`reason` (or `skillsError`) carries why. */
  skills?: BrainSkillsArchiveResult;
  /** Flattened `skills.error`, for callers that only need the reason. */
  skillsError?: string;
  error?: string;
}

/** The skills half of one backup, as reported when it was created. */
export interface BrainSkillsArchiveResult {
  archived: boolean;
  name?: string;
  bytes?: number;
  files?: number;
  roots?: number;
  skipped?: number;
  /** Verified but incomplete — see BrainSkillsArchiveEntry.truncated. */
  truncated?: boolean;
  error?: string;
  /** Present when there was nothing to archive (e.g. no skill roots). */
  reason?: string;
}

export interface BrainRestoreStaged {
  ok: boolean;
  name?: string;
  /** Currently always the string `'next-launch'` — the swap happens at startup,
   *  never in place. Typed as a plain string because the server owns the value. */
  appliesOn?: string;
  note?: string;
  error?: string;
}

export interface BrainRestoreCancelled {
  ok: boolean;
  cancelled: boolean;
  error?: string;
}

export interface BrainIntegrity {
  ok: boolean;
  /** False when there is no database file yet — `detail` explains. */
  exists: boolean;
  /** 'ok' when healthy, otherwise the reason verbatim. */
  detail: string;
  path?: string;
  /** Healthy copies on disk. Absent when the DB does not exist. */
  backups?: number;
  pendingRestore?: string | null;
}

export function getBrainIntegrity(): Promise<BrainIntegrity> {
  return api.get<BrainIntegrity>('/api/brain/integrity');
}

export function listBrainBackups(): Promise<BrainBackupList> {
  return api.get<BrainBackupList>('/api/brain/backups');
}

export function createBrainBackup(reason: string): Promise<BrainBackupCreated> {
  return api.post<BrainBackupCreated>('/api/brain/backups', { reason });
}

export function stageBrainRestore(name: string): Promise<BrainRestoreStaged> {
  return api.post<BrainRestoreStaged>('/api/brain/backups/restore', { name });
}

export function cancelBrainRestore(): Promise<BrainRestoreCancelled> {
  return api.delete<BrainRestoreCancelled>('/api/brain/backups/restore');
}


/** Open the markdown memory mirror (`<dataDir>/memory/`) in the OS file
 *  browser. The mirror is the human-readable view of the facts store; the
 *  backend owns the path so the frontend never needs the data dir. */
export function openMemoryFolder(): Promise<{ ok: boolean; path: string; opened: boolean }> {
  return api.post('/api/brain/memory/open-folder', {});
}
