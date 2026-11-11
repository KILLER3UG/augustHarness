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


def _step_2_single_local_provider() -> None:
    """v2 — serving GGUFs moved to ONE "Local Models" provider.

    Every per-GGUF provider entry the old serve flow minted ("GGUF · …")
    is disabled: its port belonged to a llama-server process that no longer
    exists, and leaving them enabled advertised dead endpoints in the model
    picker. The entries stay (user state is never silently deleted); a
    person can delete them in Model settings or just leave them parked.
    """
    import json

    p = Path(dataDir()) / 'providers.json'
    if not p.exists():
        return
    try:
        store = json.loads(p.read_text('utf-8'))
    except Exception:  # noqa: BLE001 — a hand-edited file must not block boot
        logger.warning('providers.json unreadable during migration v2', exc_info=True)
        return
    changed = False
    for entry in store.get('providers', []) or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get('name', '')).startswith('GGUF · ') and entry.get('enabled', True):
            entry['enabled'] = False
            changed = True
    if changed:
        p.write_text(json.dumps(store, indent=2), encoding='utf-8')


def _step_3_local_provider_label() -> None:
    """v3 — the dropdown group for local models is "LocalModels", one word.

    The label is what the model picker shows as the group heading, so it is a
    product string, not an identifier: the provider keeps its id
    ``local-models`` and every code path that matches on it is untouched.
    Without this step an install would keep reading "Local Models" until
    somebody served a GGUF again, because only the serve path re-writes the
    name.
    """
    import json

    p = Path(dataDir()) / 'providers.json'
    if not p.exists():
        return
    try:
        store = json.loads(p.read_text('utf-8'))
    except Exception:  # noqa: BLE001 — a hand-edited file must not block boot
        logger.warning('providers.json unreadable during migration v3', exc_info=True)
        return
    changed = False
    for entry in store.get('providers', []) or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get('id', '')) == 'local-models' and entry.get('name') != 'LocalModels':
            entry['name'] = 'LocalModels'
            changed = True
    if changed:
        p.write_text(json.dumps(store, indent=2), encoding='utf-8')


def _step_4_reclaim_invented_128k() -> None:
    """v4 — the fetched 128k stamps that were never a fact are reclaimed.

    Auto-discovery used to stamp ``contextWindow: 128000`` onto every added
    model, and the resolver honoured stored values as user intent — so a
    model August knew nothing about looked permanently 128k, whichever
    default applied. The resolver carried a three-condition carve-out for
    that shape (fetched + 128000 + no family heuristic); this step deletes
    the invented value at the DATA layer instead, and the resolver's
    permanent special case is gone with it.

    Untouched, on purpose: a manual value (source='manual'), any non-128000
    value, and a fetched 128000 on an id a family heuristic knows (gpt-4o,
    deepseek — their 128k is the family's real answer, so reclaiming it
    would change nothing and the entry stays as written).
    """
    import json
    import os
    import tempfile

    from app.services.model_service import _familyContextWindow

    p = Path(dataDir()) / 'providers.json'
    if not p.exists():
        return
    try:
        store = json.loads(p.read_text('utf-8'))
    except Exception:  # noqa: BLE001 — a hand-edited file must not block boot
        logger.warning('providers.json unreadable during migration v4', exc_info=True)
        return
    changed = False
    for entry in store.get('providers', []) or []:
        if not isinstance(entry, dict):
            continue
        for model in entry.get('models', []) or []:
            if not isinstance(model, dict):
                continue
            if str(model.get('source', '')) != 'fetched':
                continue
            try:
                window = int(str(model.get('contextWindow')))
            except (TypeError, ValueError):
                continue
            if window != 128000:
                continue
            if _familyContextWindow(str(model.get('id', ''))) is not None:
                continue
            # Drop the invented value: the resolver falls through to
            # profiles → live metadata → family → the current default.
            model.pop('contextWindow', None)
            changed = True
    if changed:
        # Atomic write — this step, unlike the shipped v2/v3, must tolerate
        # its own partial effects (ladder rule 2), and a crash mid-write
        # that truncated providers.json would be swallowed by the reader
        # and stamped as migrated.
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix='.providers-v4-', suffix='.json')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(store, handle, indent=2)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


LADDER: list[tuple[int, str, Callable[[], None]]] = [
    (1, 'layout baseline: marker + runtimes/models/shared/bots dirs', _step_1_layout_baseline),
    (2, 'disable legacy per-GGUF providers (superseded by Local Models)', _step_2_single_local_provider),
    (3, 'rename the local provider label to LocalModels', _step_3_local_provider_label),
    (4, 'reclaim invented fetched-128000 context windows (the 1M default)', _step_4_reclaim_invented_128k),
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
