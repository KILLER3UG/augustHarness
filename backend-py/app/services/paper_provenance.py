"""Provenance sidecar — automatic attribution for research-paper drafts.

The research-paper-studio skill teaches the model to keep a per-chapter
provenance sidecar (``drafts/provenance/<chapter>.md``) recording what the
agent drafted versus what the user wrote. Discipline is not a mechanism: the
drafting tool writes the entry ITSELF now, so a chapter cannot be saved
without its provenance record — including on a small local model that never
reads that paragraph of the skill.

Best-effort by contract: a sidecar failure is logged and never fails the
write that triggered it.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DRAFT_DIR = 'drafts'
_SIDECAR_DIR = 'provenance'
_PAPER_STATE = 'paper-state.md'
# The draft files the skill defines. Chapter numbers 1-5 per the school
# structure; the fixed companions ride along.
_DRAFT_NAMES = frozenset(
    {
        'chapter1',
        'chapter2',
        'chapter3',
        'chapter4',
        'chapter5',
        'abstract',
        'references',
        'appendices',
    }
)


def _classify(file_path: Path) -> tuple[Path, str] | None:
    """``(workspace_root, draft_name)`` for a paper-chapter draft, else None.

    A paper workspace is a directory holding ``paper-state.md``; a draft is a
    write directly under its ``drafts/`` directory with a known chapter name.
    """
    try:
        parent = file_path.parent
        if parent.name != _DRAFT_DIR:
            return None
        stem = file_path.stem.lower()
        if stem not in _DRAFT_NAMES:
            return None
        workspace = parent.parent
        if not (workspace / _PAPER_STATE).is_file():
            return None
        return workspace, stem
    except Exception:  # noqa: BLE001 — detection must never raise
        return None


def record_draft_write(file_path: Path, content: str, *, tool: str = 'write_file') -> None:
    """Append the provenance sidecar entry for a drafted chapter. Best-effort."""
    try:
        classified = _classify(Path(file_path))
        if classified is None:
            return
        workspace, name = classified
        sidecar_dir = workspace / _DRAFT_DIR / _SIDECAR_DIR
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        sidecar = sidecar_dir / f'{name}.md'
        model, provider = _current_model()
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
        entry = (
            f"\n## {_utc_now()} — drafted by August\n"
            f'- tool: {tool}\n'
            f'- file: {_DRAFT_DIR}/{file_path.name}\n'
            f'- model: {model or "unknown"}\n'
            f'- provider: {provider or "unknown"}\n'
            f'- bytes: {len(content.encode("utf-8"))}\n'
            f'- sha256: {digest}\n'
            f'- attribution: agent-drafted — verify against your school\'s AI-use policy; '
            f'user-written passages are recorded here by the user, never inferred\n'
        )
        with open(sidecar, 'a', encoding='utf-8', newline='') as handle:
            handle.write(entry)
        logger.info('provenance sidecar appended: %s', sidecar)
    except Exception:  # noqa: BLE001 — the sidecar never fails the write
        logger.debug('provenance sidecar write failed for %s', file_path, exc_info=True)


def _current_model() -> tuple[str, str]:
    """The (model, provider) answering this turn, for the attribution line."""
    try:
        from app.services.workbench.context import currentChatModel

        model, provider = currentChatModel.get()
        return str(model or ''), str(provider or '')
    except Exception:  # noqa: BLE001
        return '', ''


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
