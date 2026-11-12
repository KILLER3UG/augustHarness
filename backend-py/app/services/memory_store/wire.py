"""Row/wire helpers for the file-backed memory store.

``_row_as_wire`` camelCases a snake_case store row; it accepts the plain
dicts the file stores load (the SQL era also fed it sqlite3.Row, which
``dict(row)`` already handled). ``_conn``/``_db_path``/``close`` are
compatibility shims for the retired connection helpers — importers that only
need the path (the backup tooling) still resolve, and anything still asking
for a connection is pointed at the file stores.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from app.adapters.case_converters import snakeToCamel
from app.lib.paths import dataPath
from app.type_aliases import JsonValue, SessionRecord

_RETIRED_DB_FILE = 'august_brain.sqlite'


def _json(value: object) -> str:
    """Serialize a value to JSON for storage."""
    return json.dumps(value)


def _row_as_wire(row: dict[str, object] | None) -> dict[str, object]:
    """Convert a store row (snake_case columns) to a camelCase wire dict."""
    if row is None:
        return {}
    raw = dict(row)
    converted = snakeToCamel(cast(JsonValue, raw))
    return cast(dict[str, object], converted) if isinstance(converted, dict) else raw


def _session_field(session: SessionRecord | dict[str, object], camel: str, default: object = None) -> object:
    """Read a session field accepting camelCase wire keys or snake_case."""
    snake = ''.join((('_' + c.lower()) if c.isupper() else c) for c in camel)
    # Prefer explicit dual-get for common keys without full dict convert
    if camel in session and session.get(camel) is not None:
        return session.get(camel)
    if snake in session and session.get(snake) is not None:  # type: ignore[arg-type]
        return session.get(snake)  # type: ignore[arg-type]
    return default


def _db_path() -> Path:
    """The RETIRED brain database path (compat shim).

    The store of record is the file homes under ``dataDir()``; this path only
    ever names the imported-aside ``august_brain.sqlite`` (or its
    ``.retired`` rename) for tooling that still wants to know where the old
    bytes live. Nothing opens it read-write.
    """
    return dataPath(_RETIRED_DB_FILE)


def _conn() -> object:
    """Compatibility shim for the retired connection helper.

    There is no database to connect to. A caller that still reaches for a
    connection is one rewrite behind the store: the file homes are read
    through :mod:`app.services.memory_store.tables` and the markdown record
    in :mod:`app.services.memory_store.mirror`.
    """
    raise RuntimeError(
        'memory_store._conn() is retired — the brain record is file-backed '
        '(app.services.filestore + memory_store.tables/mirror); no SQLite '
        'connection exists anymore'
    )


def close() -> None:
    """Compatibility shim: there is no connection to close. File writes are
    atomic + fsync at the call site, so nothing is buffered here."""
    return None
