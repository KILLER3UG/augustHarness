"""The data-dir migration ladder — rerun's discipline, adopted.

``{dataDir}`` is user state that MUST survive updates (the two install
guards), and it now spans more than the brain DB: providers.json,
secrets.json, ``runtimes/``, ``models/``, ``shared/``, bot skills. Changes
to that layout are numbered, re-runnable steps in ONE ladder, applied at
boot before any store opens. The version reached is written to
``{dataDir}/data-version.json``; a folder without it is v0.

The rules (rerun's, unchanged — they are what makes the ladder safe):

1. Any change to the data-dir layout is a NEW step at the END of the
   ladder. Never edit or remove a shipped step.
2. A step must be RE-RUNNABLE: a crash between the step and the version
   write replays it on the next boot, so every step must tolerate its own
   partial effects.
3. A step must not break an engine still running beside it (the desktop
   update path migrates before the new backend starts; the OLD process may
   still be finishing its last turn against the same dir).
4. Add first, remove later: cleanup is a later step, never an edit.

A failed step refuses the boot — running with half-migrated user state is
the one outcome worse than not starting.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from app.lib.paths import dataDir

logger = logging.getLogger(__name__)

_VERSION_FILE = 'data-version.json'


def _read_version() -> int:
    p = Path(dataDir()) / _VERSION_FILE
    if not p.exists():
        return 0
    try:
        data = json.loads(p.read_text('utf-8'))
        return int(data.get('version', 0))
    except Exception:  # noqa: BLE001 — unreadable marker = treat as v0 and re-run
        logger.warning('unreadable %s — re-running the ladder', _VERSION_FILE, exc_info=True)
        return 0


def _write_version(version: int) -> None:
    import datetime

    p = Path(dataDir()) / _VERSION_FILE
    p.write_text(
        json.dumps(
            {
                'version': version,
                'updatedAt': datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds'),
            }
        ),
        encoding='utf-8',
    )


def _step_1_layout_baseline() -> None:
    """v1 — the layout baseline.

    Establishes the marker. The layout it names is the one this backend
    ships with: brain sqlite + providers/secrets/config JSON at the root,
    runtimes/ (llama.cpp), models/ (HF downloads), shared/ (the shared
    space), bots/ (per-bot skills). Nothing to move — every step after
    this one owns a real change.
    """
    for sub in ('runtimes', 'models', 'shared', 'bots'):
        (Path(dataDir()) / sub).mkdir(parents=True, exist_ok=True)


LADDER: list[tuple[int, str, Callable[[], None]]] = [
    (1, 'layout baseline: marker + runtimes/models/shared/bots dirs', _step_1_layout_baseline),
]


def apply_migrations() -> int:
    """Bring the data dir to the ladder's top; returns the version reached."""
    current = _read_version()
    if current >= len(LADDER):
        return current
    for version, name, step in LADDER:
        if version <= current:
            continue
        logger.info('data-dir migration v%d: %s', version, name)
        step()
        _write_version(version)
        logger.info('data-dir migration v%d applied', version)
    return _read_version()
