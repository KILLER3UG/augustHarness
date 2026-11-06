"""Expect receipts — the model verifies its own mutating calls (E4).

A tool call may carry one reserved argument, ``_expect``:

    {"path": "x.py", "content": "...", "_expect": {"measure": "written", "equals": true}}

The executor evaluates the expectation against the tool's OWN result and
appends a receipt. The receipt is prose the model reads — it never gates
the answer, never turns into an error, and never withholds the tool's
result (AGENTS.md: no verifier gate). What it buys is a machine-checkable
verdict per mutation: the turn outcome row records how many receipts ran
and how many failed, which is exactly the model-independent outcome signal
the learning pipeline was missing.

One-way import rule: nothing under loop/ imports workbench.py.

Honesty rules (inherited from circuit_test's assertions, the precedent):
  * An expectation cannot pass against a value the tool never returned —
    a measure path that resolves to nothing fails with a note that says so.
  * An expectation the model did not state produces no receipt at all:
    a fixture that asserts nothing is how a suite goes green around a
    broken tool.
  * The receipt names what was expected, what was measured, and which one
    won. No "success" without the measured value next to it.
"""

from __future__ import annotations

import contextvars
import re
from typing import Any

# Per-turn receipt verdicts. ContextVar so interleaved turns (sub-agent
# workers drive their own loops on the same loop) never share a list.
_TURN_RECEIPTS: contextvars.ContextVar[list[bool] | None] = contextvars.ContextVar(
    'turn_receipts', default=None
)

# The reserved argument name. Anything else in `_expect` that is not a
# known comparator is reported in the receipt's note, not silently dropped.
RESERVED_KEY = '_expect'
_COMPARATORS = ('equals', 'contains', 'gt', 'gte', 'lt', 'lte', 'exists')


def extract_expectation(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Pop ``_expect`` from the call args. Returns (args_without, expectation).

    Popping happens BEFORE dispatch: handlers must never see the reserved
    key, and a handler that validates its schema strictly would otherwise
    refuse the call for an argument that is none of its business.
    """
    expect = args.pop(RESERVED_KEY, None)
    if not isinstance(expect, dict) or not expect:
        return args, None
    return args, expect


def _resolve_measure(result: Any, measure: str) -> tuple[bool, Any]:
    """Walk a dotted path into the result. Returns (found, value)."""
    if not measure or not isinstance(measure, str):
        return True, result
    cur: Any = result
    for part in measure.split('.'):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return False, None
    return True, cur


def evaluate_expectation(expect: dict[str, Any], result: Any) -> dict[str, Any]:
    """Evaluate one expectation against a tool result. Never raises."""
    measure = str(expect.get('measure', '') or '')
    found, value = _resolve_measure(result, measure)
    comparator = next((k for k in _COMPARATORS if k in expect), None)

    if comparator is None:
        return {
            'expect': expect,
            'measured': None,
            'ok': False,
            'note': 'no known comparator in _expect (use one of: ' + ', '.join(_COMPARATORS) + ')',
        }
    if not found:
        return {
            'expect': expect,
            'measured': None,
            'ok': False,
            'note': (
                f'measure "{measure}" matched nothing the tool returned — an expectation '
                'cannot pass against a value the tool never returned'
            ),
        }

    ok = False
    note = ''
    try:
        if comparator == 'exists':
            ok = value is not None and value != ''
        elif comparator == 'contains':
            ok = str(expect['contains']).lower() in str(value).lower()
        elif comparator == 'equals':
            ok = value == expect['equals'] or str(value) == str(expect['equals'])
        elif comparator in ('gt', 'gte', 'lt', 'lte'):
            expected_num = float(expect[comparator])
            actual = float(value)  # type: ignore[arg-type]
            ok = (
                actual > expected_num
                if comparator == 'gt'
                else actual >= expected_num
                if comparator == 'gte'
                else actual < expected_num
                if comparator == 'lt'
                else actual <= expected_num
            )
        else:  # pragma: no cover — comparator comes from _COMPARATORS
            ok = False
    except (TypeError, ValueError):
        ok = False
        note = f'could not compare measured {value!r} with {expect[comparator]!r}'

    unknown = sorted(set(expect) - {'measure', *_COMPARATORS})
    if unknown:
        note = (note + '; ' if note else '') + f'ignored unknown key(s): {", ".join(unknown)}'

    return {'expect': expect, 'measured': value, 'ok': ok, 'note': note}


def format_receipt(receipt: dict[str, Any]) -> str:
    """One model-readable receipt block. The measured value rides along so
    the model can correct from evidence, not from the ok bit alone."""
    verdict = 'ok' if receipt.get('ok') else 'MISMATCH'
    expect = receipt.get('expect') or {}
    comparator = next((k for k in _COMPARATORS if k in expect), 'expect')
    note = receipt.get('note') or ''
    lines = [
        '[receipt] ' + verdict,
        f'expected {comparator}: {expect.get(comparator)!r}'
        + (f' on measure "{expect.get("measure")}"' if expect.get('measure') else ''),
        f'measured: {receipt.get("measured")!r}',
    ]
    if note:
        lines.append(f'note: {note}')
    return '\n'.join(lines)


def record_receipt_verdict(ok: bool) -> None:
    """Stamp one verdict into the turn's receipt tally (best-effort)."""
    try:
        current = _TURN_RECEIPTS.get()
        if current is None:
            current = []
            _TURN_RECEIPTS.set(current)
        current.append(ok)
    except Exception:  # noqa: BLE001 — telemetry must never raise into the turn
        pass


def pop_turn_receipts() -> dict[str, int]:
    """The turn's receipt tally, or {} when no receipt ran.

    {} (not zeros) keeps the NULL-means-not-recorded discipline the
    turn_outcomes row is built on: a turn that used no receipts and a turn
    that failed to count them stay distinguishable from a turn whose
    receipts all passed.
    """
    current = _TURN_RECEIPTS.get()
    _TURN_RECEIPTS.set(None)
    if not current:
        return {}
    return {'receipts_total': len(current), 'receipts_failed': sum(1 for ok in current if not ok)}


def clear_turn_receipts() -> None:
    """Drop any tally (worker/sub-agent turn boundaries must not inherit)."""
    _TURN_RECEIPTS.set(None)


def receipt_note_matches(text: str) -> bool:
    """True when the text carries a receipt block (used by callers that want
    to strip receipts before re-sending a transcript)."""
    return bool(re.search(r'^\[receipt\] (?:ok|MISMATCH)$', text, re.MULTILINE))
