"""P4.2 — debounced commit for high-frequency per-turn writes.

RETIRED with SQLite: the whole-file stores write atomically and fsync at the
call site (filestore rules 1 and 4), so there is nothing to batch — every
write is already durable when the function returns, and the packaged desktop
quit (``taskkill /T /F``) loses nothing.

The public names stay because importers in the transcript domain still call
them: ``defer_commit(conn)`` is now a no-op and ``flush_thread_pending()``
returns 0. A caller that still holds a SQLite connection object passes it
here harmlessly while its own rewrite lands.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Kept for callers that read the old debounce window. Meaningless now: the
#: file stores do not buffer.
WINDOW_S = 2.0


def defer_commit(conn: object = None, window_s: float = WINDOW_S) -> None:
    """No-op. The SQL-era debounce funnel — file writes are durable on return.

    The ``conn`` argument is the retired connection handle; it is accepted so
    call sites keep their shape during the engine swap and ignored otherwise.
    """
    del conn, window_s
    return None


def flush_thread_pending() -> int:
    """No-op. There is no deferred queue to flush; returns 0 flushed units."""
    return 0
