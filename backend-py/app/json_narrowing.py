"""Safe narrowing helpers for ``JsonValue``-typed provider payloads.

Provider payloads (Anthropic / OpenAI / etc.) are represented as
``JsonValue`` (a broad recursive union: ``str | int | float | bool | None
| list[JsonValue] | dict[str, object]``). Because the union is broad,
operating on a value directly makes mypy reject it (e.g. ``JsonValue +
str`` or ``.get`` on a non-dict member).

These helpers narrow a ``JsonValue`` to a concrete type at runtime. They
are the single, shared convention for touching dynamic payloads across the
codebase, which keeps mypy satisfied while staying flexible for new or
optional fields: a missing or oddly-typed value degrades gracefully to the
provided default instead of raising. Prefer them over ad-hoc ``isinstance``
checks so the behavior is consistent everywhere.
"""

from __future__ import annotations


def as_str(value: object, default: str = '') -> str:
    """Return ``value`` as a ``str``, or ``default`` if it is not a str/None."""
    return value if isinstance(value, str) else default


def as_dict(value: object, default: dict[str, object] | None = None) -> dict[str, object]:
    """Return ``value`` as a ``dict``, or ``default``/``{}`` if it is not a dict."""
    if isinstance(value, dict):
        return value
    return default if default is not None else {}


def as_list(value: object, default: list[object] | None = None) -> list[object]:
    """Return ``value`` as a ``list``, or ``default``/``[]`` if it is not a list."""
    if isinstance(value, list):
        return value
    return default if default is not None else []


def as_int(value: object, default: int = 0) -> int:
    """Return ``value`` as an ``int`` (excluding ``bool``), or ``default``."""
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def as_float(value: object, default: float = 0.0) -> float:
    """Return ``value`` as a ``float``/``int`` (excluding ``bool``), or ``default``."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def as_bool(value: object, default: bool = False) -> bool:
    """Return ``value`` as a ``bool``, or ``default`` if it is not a bool."""
    return value if isinstance(value, bool) else default


def coerce_json_list(value: object) -> list[object] | str:
    """Narrow a tool argument that must be a JSON array.

    Models frequently stringify array arguments (``changes='[{...}]'``) or
    pass a single object where a one-item list is meant. This accepts:

      * a real ``list`` → returned as-is;
      * a ``dict``      → wrapped into ``[dict]``;
      * a ``str``       → ``json.loads``; a parsed list is returned, a
        parsed dict is wrapped; anything else is a parse failure.

    On failure returns a short, actionable ``str`` error the handler can
    hand straight back to the model (never a raw Python exception).
    """
    import json

    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return (
                'Error: expected a JSON array of objects but received a plain string. '
                'Pass the parameter as a real JSON array, not a stringified one.'
            )
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
        return (
            'Error: expected a JSON array of objects but the string parsed to a '
            f'{type(parsed).__name__}. Pass the parameter as a real JSON array.'
        )
    return 'Error: expected a JSON array of objects for this parameter.'


_ANSWER_KEYS = ('reasoning', 'reasoning_content')


def extract_reasoning_text(message: object) -> str:
    """The reasoning channel of an assistant message, either spelling.

    The counterpart to :func:`extract_answer_text`: a consumer that emits the
    reasoning as its OWN block (an Anthropic `thinking` block, a `reasoning`
    output item) needs the channel alone, not "the answer whichever channel it
    landed in". ``stream_state`` writes BOTH keys, so a reader that checks one
    is wrong for the other model family — DeepSeek/Kimi read
    ``reasoning_content``, other stacks read ``reasoning``.
    """
    record = as_dict(message, {})
    for key in _ANSWER_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ''


def extract_reasoning_delta(delta: object) -> str:
    """The reasoning carried by one STREAMING delta, either spelling.

    A delta carries a fragment, not a whole message, so the `.strip()` truthiness
    of :func:`extract_reasoning_text` is wrong here — a chunk of `" "` is real
    content. The key order is the same and for the same reason: both spellings
    exist on the wire and a reader that checks one is wrong for the other model
    family.
    """
    record = as_dict(delta, {})
    for key in _ANSWER_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return ''


def extract_answer_text(message: object) -> str:
    """The text a model put in an assistant message, whichever channel it used.

    The answer channel is ``content``, and a reasoning model may have moved the
    answer into ``reasoning`` / ``reasoning_content``: it spends completion
    tokens on the chain first, and a cap that runs out mid-chain leaves
    ``content`` null while the answer is already in the reasoning channel.
    Measured on a real vision call — ``content: null``, the description sitting
    in ``reasoning`` — which a consumer that read ``content`` alone reported as
    "(empty response)".

    ``content`` may be a plain string or a list of blocks; the text blocks are
    joined. The reasoning keys are a fallback, in the order the adapters write
    them: ``stream_state`` sets BOTH spellings (DeepSeek/Kimi read
    ``reasoning_content``, other stacks read ``reasoning``), so a consumer that
    checks one is wrong for the other model.

    Returns '' when there is no text anywhere. That is a real answer — "the
    provider returned no text" is a refusal the caller must surface — not a
    reason to fall back to a guess.
    """
    record = as_dict(message, {})
    content = record.get('content')
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            blockDict = as_dict(block, {})
            if blockDict.get('type') in (None, 'text'):
                text = as_str(blockDict.get('text'), '')
                if text.strip():
                    parts.append(text)
        if parts:
            return '\n'.join(parts)
    for key in _ANSWER_KEYS:
        reasoning = record.get(key)
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning
    return ''


def coerce_json_dict(value: object) -> dict[str, object] | str:
    """Narrow a tool argument that must be a JSON object.

    Accepts a real ``dict`` or a stringified object (``json.loads``).
    On failure returns a short actionable ``str`` error, never raises.
    """
    import json

    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return (
                'Error: expected a JSON object but received a plain string. '
                'Pass the parameter as a real JSON object, not a stringified one.'
            )
        if isinstance(parsed, dict):
            return parsed
        return 'Error: expected a JSON object but the string parsed to a non-object.'
    return 'Error: expected a JSON object for this parameter.'