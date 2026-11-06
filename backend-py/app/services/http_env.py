"""One place to build an httpx client, so a hostile environment cannot disable the app.

Why this exists: ``httpx.AsyncClient()`` reads ``NO_PROXY`` at construction and
parses every comma-separated entry as a URL pattern. A bracketed IPv6 literal —
``NO_PROXY=..., [::1], ...``, which Windows proxy-bypass settings and several
tools write — makes the parse raise ``InvalidURL`` **before any request is
made**. That is not one broken feature: it breaks every Hub search, every
model download, the llama.cpp runtime install, and the ``/health`` poll that
flips a served GGUF from ``starting`` to ``ready``. The health loop catches a
broad exception as "server not up yet", so a poisoned bypass list does not look
like a failure at all — it looks like a model that never finishes loading.

The fallback drops *environment* proxy discovery, not proxy support a caller
configured explicitly: an unusable system list must not silently take the
product offline, and the warning names what happened so it is diagnosable.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_warned = False


def client(**kwargs: Any) -> httpx.AsyncClient:
    """An ``AsyncClient`` that builds even when the env proxy config is broken."""
    global _warned
    try:
        return httpx.AsyncClient(**kwargs)
    except httpx.InvalidURL as exc:
        if kwargs.get('trust_env') is not None:
            # The caller chose; do not second-guess an explicit decision twice.
            raise
        if not _warned:
            _warned = True
            logger.warning(
                'system proxy settings are unparseable (%s) — continuing without '
                'environment proxy discovery. NO_PROXY entries like "[::1]" make '
                'httpx refuse to build any client.',
                exc,
            )
        return httpx.AsyncClient(trust_env=False, **kwargs)
