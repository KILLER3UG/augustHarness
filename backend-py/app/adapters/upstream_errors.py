"""Upstream error normalization for the /v1 proxy adapters.

Upstream failure bodies come in many shapes (OpenAI ``{"error": {...}}``,
Anthropic ``{"type": "error", "error": {...}}``, plain text, FastAPI-style
``{"detail": ...}``). Before this module, adapters returned those raw bodies
as success — the router saw no top-level ``error`` key and answered HTTP 200
with an error payload. ``normalize_upstream_error`` always produces the
router-friendly ``{error, type, status}`` shape so ``_endNonStream`` can
surface a real non-2xx response.
"""

from __future__ import annotations

from app.json_narrowing import as_dict, as_list, as_str

# The cap on a plain-text error body. The cut is COUNTED (media_tools sets the
# precedent): a silent cap turns "rate limited until 14:00" into "rate limited
# until" with nothing to say it was cut.
_MAX_TEXT_ERROR_CHARS = 500


def _detailMessage(detail: object) -> str:
    """Render a FastAPI-style ``detail`` into one line.

    ``detail`` is a string, a validation list (``[{"loc": ..., "msg": ...}]``)
    or a dict. The ``msg`` fields are what a human needs; anything else falls
    back to the item itself so the shape never collapses to nothing.
    """
    if isinstance(detail, str):
        return detail.strip()
    if isinstance(detail, dict):
        for key in ('message', 'msg', 'error'):
            rendered = as_str(detail.get(key), '').strip()
            if rendered:
                return rendered
        return str(detail).strip()
    parts: list[str] = []
    for item in as_list(detail, []):
        itemDict = as_dict(item, {})
        rendered = (
            as_str(itemDict.get('msg'), '').strip()
            or as_str(itemDict.get('message'), '').strip()
            or str(item).strip()
        )
        if rendered:
            parts.append(rendered)
    return '; '.join(parts)


class UpstreamError(Exception):
    """Raised by adapter internals when an upstream call fails.

    Carries the failed :class:`ProviderResponse` so the adapter boundary can
    normalize it into the router error shape at the right layer.
    """

    def __init__(self, resp: object) -> None:
        super().__init__(f'Upstream request failed (HTTP {getattr(resp, "status", 0)})')
        self.resp = resp


def normalize_upstream_error(resp: object) -> dict[str, object]:
    """Build a router-friendly ``{error, type, status}`` dict from a failure.

    Always returns a dict with an ``error`` key — never raises — so the
    router's ``_endNonStream`` error branch reliably fires.
    """
    status = getattr(resp, 'status', None) or 502
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = 502
    body = as_dict(getattr(resp, 'body_json', None), {})
    # Direct {error: ...} (OpenAI) or {type: error, error: {...}} (Anthropic)
    if 'error' in body:
        err = body['error']
        if isinstance(err, dict):
            message = as_str(err.get('message'), '') or as_str(err.get('type'), '')
        else:
            message = as_str(err)
        return {
            'error': message or 'Upstream request failed',
            'type': 'error',
            'status': status,
        }
    if as_str(body.get('type'), '') == 'error':
        return {'error': 'Upstream request failed', 'type': 'error', 'status': status}
    # FastAPI-style {detail: ...} — the docstring promised this shape and the
    # code never handled it: a gateway answering 422 with a validation list
    # reached the caller as the generic "Upstream request failed (HTTP 422)"
    # with the actual reason dropped.
    if 'detail' in body:
        message = _detailMessage(body['detail'])
        if message:
            return {'error': message, 'type': 'error', 'status': status}
    # Plain-text error bodies (gateways that do not speak JSON errors)
    raw = getattr(resp, 'body', None)
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        if len(text) > _MAX_TEXT_ERROR_CHARS:
            cut = len(text) - _MAX_TEXT_ERROR_CHARS
            text = f'{text[:_MAX_TEXT_ERROR_CHARS]}\n… ({cut} more chars truncated)'
        return {'error': text, 'type': 'error', 'status': status}
    return {'error': f'Upstream request failed (HTTP {status})', 'type': 'error', 'status': status}
