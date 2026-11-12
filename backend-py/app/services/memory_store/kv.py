"""Key-value memory blob + internal machine state — file-backed.

``memory_store`` (the KV table) is ``<state>/kv.json``; ``internal_state`` is
``<state>/internal-state.json``. Both are whole-file JSON row stores whose
rows keep the SQL-era shape (``key`` / ``value`` / ``updated_at``), with the
value kept JSON-ENCODED exactly as the database column was — the
serialization-traps rule in docs/agent/memory.md applies unchanged, and the
search helper below matches the raw text the same way the old FTS index did.
"""

from __future__ import annotations

import json
import re
from typing import cast

from app.lib.paths import assertPytestDataDirIsolated
from app.services.memory_store import tables
from app.services.memory_store.timeutil import utc_now_text
from app.services.memory_store.wire import _json
from app.type_aliases import JsonValue


def init() -> None:
    """The schema step of the SQL era. Files need no schema; the homes are
    created on demand by every read/write. Kept because main.py and the
    importers call it — a deliberate no-op."""


def _load(path) -> list[dict[str, object]]:
    return tables.load_rows(path)


def _upsert(path, key: str, json_text: str) -> None:
    with tables.store_lock(str(path)):
        rows = _load(path)
        now = utc_now_text()
        for row in rows:
            if str(row.get('key') or '') == key:
                row['value'] = json_text
                row['updated_at'] = now
                break
        else:
            rows.append({'key': key, 'value': json_text, 'updated_at': now})
        tables.save_rows(path, rows)


def _get_row(path, key: str) -> dict[str, object] | None:
    for row in tables.load_rows(path):
        if str(row.get('key') or '') == key:
            return row
    return None


def save_internal(key: str, value: JsonValue) -> None:
    """Save a key-value pair to the internal KV store.

    Renamed from ``save_memory`` (plan §3.3 M2): the KV store is machine
    state / registry data, not user-visible memory. Durable memory goes
    through the facts store (``save_fact``); this name makes misuse obvious.
    """
    # Surface tests that bypass the autouse isolatedData fixture.
    assertPytestDataDirIsolated('memory_store.save_internal')
    _upsert(tables.kv_file(), key, _json(value))


def get_memory(key: str) -> JsonValue | None:
    """Get a value from memory by key."""
    row = _get_row(tables.kv_file(), key)
    if row is None:
        return None
    raw = row.get('value')
    if isinstance(raw, str):
        try:
            return cast(JsonValue, json.loads(raw))
        except (ValueError, TypeError):
            return cast(JsonValue, raw)
    return cast(JsonValue, raw)


def delete_memory(key: str) -> bool:
    """Remove one KV row. True when a row was actually removed — the manage
    door's ``deleted`` answer is a receipt, not a verdict."""
    path = tables.kv_file()
    with tables.store_lock(str(path)):
        rows = _load(path)
        kept = [row for row in rows if str(row.get('key') or '') != key]
        if len(kept) == len(rows):
            return False
        tables.save_rows(path, kept)
        return True


def purge_kv(keep_keys: tuple[str, ...] | list[str] = ()) -> int:
    """Drop every KV row except ``keep_keys`` (the privacy purge's residual
    sweep). Returns the rows removed."""
    path = tables.kv_file()
    with tables.store_lock(str(path)):
        rows = _load(path)
        keep = {str(k) for k in keep_keys}
        kept = [row for row in rows if str(row.get('key') or '') in keep]
        removed = len(rows) - len(kept)
        if removed:
            tables.save_rows(path, kept)
        return removed


def kv_rows() -> list[dict[str, object]]:
    """Every KV row (the browse/summary doors page this)."""
    return _load(tables.kv_file())


def set_internal_state(key: str, value: JsonValue) -> None:
    """Write machine state to ``internal-state.json``.

    Maintenance/cron/daemon bookkeeping lives here — never in the
    user-visible KV and never in facts. Not exposed to the Brain stores UI;
    only reachable via the Settings raw-state lookup.
    """
    _upsert(tables.internal_state_file(), key, _json(value))


def get_internal_state(key: str) -> JsonValue | None:
    """Read a row from ``internal_state`` (None when absent)."""
    row = _get_row(tables.internal_state_file(), key)
    if row is None:
        return None
    raw = row.get('value')
    if isinstance(raw, str):
        try:
            return cast(JsonValue, json.loads(raw))
        except (ValueError, TypeError):
            return cast(JsonValue, raw)
    return cast(JsonValue, raw)


def lookup_any(key: str) -> dict[str, object] | None:
    """The raw-state lookup row: internal_state first (machine state wins),
    then the KV store. ``source`` names which store answered."""
    for source, path in (
        ('internal_state', tables.internal_state_file()),
        ('memory_store', tables.kv_file()),
    ):
        row = _get_row(path, key)
        if row is not None:
            return {
                'value': row.get('value'),
                'updated_at': row.get('updated_at') or '',
                'source': source,
            }
    return None


def _fts_match_query(query: str) -> str:
    """The search-term list the old FTS5 MATCH expression built.

    The FTS index is gone; the matcher is now a plain Python term match (see
    ``fts_match``), but the tokenizer's CONTRACT is unchanged — tokens split
    on whitespace AND non-alphanumeric boundaries, so a merged query token
    like ``my-note`` still matches ``my note`` — and the model-facing session
    search calls this to decide whether a query has searchable tokens at all.
    """
    tokens = [t for t in re.split(r'[^\w]+', query or '') if t]
    if not tokens:
        return ''
    return ' OR '.join(f'"{t.replace(chr(34), "")}"*' for t in tokens if t.replace('"', ''))


def fts_match(text: str, query: str) -> bool:
    """Case-insensitive term match over a stored value's text.

    The KV FTS helper's replacement: a query term (whitespace/symbol-split,
    prefix-matched — the same vocabulary ``_fts_match_query`` tokenized)
    matches when any term appears in ``text``. A query with no searchable
    tokens matches nothing, exactly as the empty MATCH expression did.
    """
    terms = [t for t in re.split(r'[^\w]+', (query or '').lower()) if t]
    if not terms:
        return False
    haystack = (text or '').lower()
    return any(term in haystack for term in terms)


__all__ = [
    'delete_memory',
    'fts_match',
    'get_internal_state',
    'get_memory',
    'init',
    'kv_rows',
    'lookup_any',
    'purge_kv',
    'save_internal',
    'set_internal_state',
    '_fts_match_query',
    '_upsert',
]
