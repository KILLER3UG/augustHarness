"""Pre-written compaction summaries — OpenCode's precompute, for August.

A compaction summary is a model call on the turn's critical path. When an LLM
compactor is enabled and the session is close to the trigger, that summary can
be written ahead of time and swapped in when the compressor asks for it.

The honesty rule is a fingerprint of the EXACT head chunk the compressor will
summarise: precompute learns that chunk by running the real compaction policy
with a capture summarizer (no model call), writes the summary, and stores it
against the chunk's fingerprint. At compaction time a matching fingerprint
returns the stored text; any change to that chunk — a new turn, an edited tool
result, a model switch — misses the cache and the real summarizer runs. A stale
summary can therefore never replace history.

Only engaged when a real summarizer exists (`llm_compactor`); the default
heuristic summarizer costs no model call, so there is nothing to precompute.
"""

from __future__ import annotations

import hashlib
import inspect
from typing import Any, Awaitable, Callable


def chunkFingerprint(chunk: list[dict[str, object]]) -> str:
    """Stable digest of the head chunk's roles and content."""
    h = hashlib.sha256()
    for message in chunk:
        h.update(str(message.get('role', '')).encode('utf-8'))
        h.update(b'\x00')
        h.update(str(message.get('content', '')).encode('utf-8'))
        h.update(b'\x00')
    return h.hexdigest()


class CachedSummarizer:
    """Delegates to the real summarizer unless a precomputed match exists."""

    def __init__(self, session: object, inner: Callable[..., Any]) -> None:
        self._session = session
        self._inner = inner
        self.usedCache = False

    def __call__(self, chunk: list[dict[str, object]]) -> Any:
        checkpoint = getattr(self._session, '_compactionCheckpoint', None)
        if isinstance(checkpoint, dict):
            text = checkpoint.get('text')
            if text and checkpoint.get('fingerprint') == chunkFingerprint(chunk):
                self.usedCache = True
                return str(text)
        return self._inner(chunk)


async def precomputeSummary(
    session: object,
    *,
    compactionCall: Callable[..., Awaitable[Any]],
    summarizer: Callable[..., Any],
    **kwargs: Any,
) -> None:
    """Learn the exact head chunk, summarise it, store the checkpoint.

    Never raises: a precompute that fails is a missed optimisation, not a turn
    failure. The compaction path falls back to the real summarizer.
    """
    captured: list[list[dict[str, object]]] = []

    def capture(chunk: list[dict[str, object]]) -> str:
        captured.append(list(chunk))
        return ''

    try:
        await compactionCall(list(session.messages), summarizer=capture, **kwargs)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 -- a missed optimisation, never a turn failure
        return
    if not captured:
        return
    chunk = captured[0]
    try:
        text = summarizer(chunk)
        if inspect.isawaitable(text):
            text = await text
    except Exception:  # noqa: BLE001 -- the real summarizer will run at compaction
        return
    text = str(text or '').strip()
    if not text:
        return
    try:
        session._compactionCheckpoint = {  # type: ignore[attr-defined]
            'fingerprint': chunkFingerprint(chunk),
            'text': text,
        }
    except Exception:  # noqa: BLE001 -- a checkpoint that cannot be stored is a miss
        pass
