"""The markdown record — the facts store itself, one file per fact.

SQLite is retired: ``<dataDir>/memory/`` IS the record now, not a derived
view. One ``.md`` file per fact (any status, any scope) plus a ``MEMORY.md``
index. ``rest.py``'s fact CRUD writes through this module and every read door
(recall, BM25, the boot index, brain_query) reads the files back — so there is
one store and the old two-way-drift question no longer exists.

File shape (stable, parser-readable by eye):

    ---
    name: <title>
    description: <one-line recall hook>
    key: <fact_key>
    type: <zcode vocabulary type>
    subject: <canonical subject_type>
    kind: <fact|lesson|preference|skill-note|person|profile>
    category: general
    source: user
    confidence: 1.0
    status: active
    scope: global
    tag: stated
    sensitive: 0
    version: 3
    created: 2026-11-12 09:15:00.000000
    updated: 2026-11-12 09:15:00.000000
    expires: ''
    useCount: 0
    lastUsed: ''
    aliases: []
    supersedes: []
    supersededBy: ''
    contradicts: ''
    derivedFrom: []
    previousValue: ''
    verified: ''
    sourceSession: ''
    ---

    <the fact value, JSON-encoded — exactly the serialization the brain
    database kept in ``facts.fact_value`` (see the serialization-traps rule
    in docs/agent/memory.md)>

The value stays JSON-encoded in the body so a round-trip through the file is
byte-exact: a JSON string keeps its quotes, a ``{fact, details}`` object keeps
its members. Decode one layer on display (``brain._readable_value``), never on
storage.

Frontmatter is one flat ``key: value`` line per field — no YAML dependency.
Line values are single-line by construction: writers whitespace-collapse the
two prose fields (``title``, ``description``) and JSON-encode every field that
could carry a newline. A hand edit is a legal write: the reader accepts any
missing field and falls back to the record defaults, and the next write from
the CRUD door normalizes the file.

Fences that keep the record honest:

* The directory is in ``hardline._AGENT_STATE_DIRS`` — a model that could write
  the record could install instruction-shaped content beside the facts a human
  reads to audit it. The sanctioned writers are the memory_store doors.
* Sensitive rows and other people's details are excluded from the MEMORY.md
  INDEX and from recall, exactly as before — but they stay in the record
  (that is what "stored" means), and explicit reads still return them when the
  user raises them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from pathlib import Path

from app.json_narrowing import as_str
from app.services import filestore
from app.services.memory_store.tables import record_dir, store_lock

# A record row is the storage shape: snake_case columns, JSON-text list fields.
# ``FactDict`` (the camelCase wire TypedDict) is what the read doors CAST these
# rows into, never what the files hold.
RecordRow = dict[str, object]

logger = logging.getLogger('august.memory_store.record')

_INDEX_NAME = 'MEMORY.md'
_MAX_FILE_STEM = 72
_SANITIZE_RE = re.compile(r'[^A-Za-z0-9._-]+')
_FRONTMATTER_RE = re.compile(r'\A---\s*\n(.*?)\n---\s*\n?', re.DOTALL)

# Frontmatter field order — one authority so a diff between two writes of the
# same fact shows content, not churn.
_FIELDS: tuple[tuple[str, str], ...] = (
    ('id', 'id'),
    ('key', 'fact_key'),
    ('name', 'title'),
    ('description', 'description'),
    ('type', '_zcode_type'),
    ('subject', 'subject_type'),
    ('kind', 'kind'),
    ('category', 'category'),
    ('source', 'source'),
    ('confidence', 'confidence'),
    ('status', 'status'),
    ('scope', 'scope'),
    ('tag', 'tag'),
    ('sensitive', 'sensitive'),
    ('version', 'version'),
    ('created', 'created_at'),
    ('updated', 'updated_at'),
    ('expires', 'expires_at'),
    ('useCount', 'use_count'),
    ('lastUsed', 'last_used_at'),
    ('injectedCount', 'injected_count'),
    ('lastInjected', 'last_injected_at'),
    ('aliases', 'aliases'),
    ('supersedes', 'supersedes'),
    ('supersededBy', 'superseded_by'),
    ('contradicts', 'contradicts'),
    ('derivedFrom', 'derived_from'),
    ('previousValue', 'previous_value'),
    ('verified', 'verified_at'),
    ('sourceSession', 'source_session'),
)

# Snake-case columns the record carries besides the frontmatter fields. The
# ``id`` is the stable row identifier the SQL era minted and the Memory UI
# still addresses rows by — minted on insert, stored in the file.
_ROW_DEFAULTS: dict[str, object] = {
    'id': 0,
    'fact_key': '',
    'fact_value': '',
    'title': '',
    'description': '',
    'kind': 'fact',
    'category': 'general',
    'source': '',
    'confidence': 1.0,
    'status': 'active',
    'scope': 'global',
    'subject_type': '',
    'tag': '',
    'sensitive': 0,
    'version': 1,
    'created_at': '',
    'updated_at': '',
    'expires_at': '',
    'use_count': 0,
    'last_used_at': '',
    'injected_count': 0,
    'last_injected_at': '',
    'aliases': '[]',
    'supersedes': '[]',
    'superseded_by': '',
    'contradicts': '',
    'derived_from': '[]',
    'previous_value': '',
    'verified_at': '',
    'source_session': '',
}

# The ZCode memory vocabulary, accepted as INPUT aliases and mapped onto the
# record's canonical subject types. `user` covers who the user is (profile)
# and how they want the assistant to behave (preference) in ZCode's single
# bucket, so it maps to the closest canonical; the file frontmatter still
# prints the canonical word.
_SUBJECT_ALIASES = {
    'user': 'profile',
    'feedback': 'preference',
    'project': 'area',
    'reference': 'topic',
}

_write_lock = threading.RLock()


def normalize_subject_type(subject_type: str) -> str:
    """Map a ZCode-vocabulary type onto the canonical subject type.

    Unknown words are returned unchanged — the caller's own validation
    decides whether they are legal.
    """
    return _SUBJECT_ALIASES.get((subject_type or '').strip().lower(), (subject_type or '').strip().lower())


def mirror_root() -> Path:
    """The record directory (the historical name is kept for callers)."""
    return record_dir()


def _safe_stem(fact_key: str) -> str:
    """A stable, readable filename for a key, with a hash suffix when the
    sanitization or the length cap actually changed something — so two keys
    that sanitize alike cannot share a file."""
    stem = _SANITIZE_RE.sub('_', fact_key).strip('.')[:_MAX_FILE_STEM].strip('.')
    if stem != fact_key or len(fact_key) > _MAX_FILE_STEM:
        digest = hashlib.sha1(fact_key.encode('utf-8')).hexdigest()[:8]
        stem = f'{stem}.{digest}'
    return stem or 'fact'


def fact_path(fact_key: str, scope: str = 'global') -> Path:
    """One fact's file. Identity is (scope, key) — the same identity the
    store's upsert targets — so a non-global row gets a scope-prefixed stem
    and can never collide with the global fact of the same key."""
    scoped_key = fact_key if (scope or 'global') == 'global' else f'{scope}:{fact_key}'
    return mirror_root() / f'{_safe_stem(scoped_key)}.md'


def _zcode_type(subject_type: str, kind: str) -> str:
    """The frontmatter `type:` — ZCode's vocabulary, best-effort."""
    st = (subject_type or '').strip().lower()
    kind_l = (kind or '').strip().lower()
    if st == 'profile' or kind_l == 'profile':
        return 'user'
    if st == 'preference' or kind_l == 'preference':
        return 'feedback'
    if st == 'area':
        return 'project'
    return 'reference' if kind_l in ('skill-note', 'reference') else 'project' if kind_l == 'project' else 'user'


def _fm_value(row_key: str, row: RecordRow) -> object:
    """The row value for one frontmatter field, with per-field shaping."""
    if row_key == '_zcode_type':
        return _zcode_type(str(row.get('subject_type') or ''), str(row.get('kind') or ''))
    if row_key == 'sensitive':
        return 1 if row.get('sensitive') else 0
    return row.get(row_key, '')


def _fm_line(field: str, value: object) -> str:
    if isinstance(value, str):
        if not value:
            return f'{field}: \'\''
        if any(c in value for c in '\n\r'):
            # A newline would forge a frontmatter line; JSON-encode the text
            # instead (the parser json-decodes any quoted value it reads).
            return f'{field}: {json.dumps(value, ensure_ascii=False)}'
        return f'{field}: {value}'
    if isinstance(value, (list, dict)):
        return f'{field}: {json.dumps(value, ensure_ascii=False)}'
    return f'{field}: {value!r}' if isinstance(value, bool) else f'{field}: {value}'


def _render_fact_file(row: RecordRow) -> str:
    lines = ['---']
    for field, row_key in _FIELDS:
        lines.append(_fm_line(field, _fm_value(row_key, row)))
    lines.append('---')
    lines.append('')
    # The body IS the JSON-encoded value — one layer, exact round-trip.
    lines.append(str(row.get('fact_value') or ''))
    return '\n'.join(lines) + '\n'


def _parse_scalar(text: str) -> object:
    """One frontmatter value, tolerantly: JSON first, then the raw text."""
    raw = text.strip()
    if not raw:
        return ''
    if raw.startswith(('"', '{', '[', "'")) or raw in ('null', 'true', 'false'):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    if re.fullmatch(r'-?\d+', raw):
        try:
            return int(raw)
        except ValueError:
            pass
    if re.fullmatch(r'-?\d+\.\d+', raw):
        try:
            return float(raw)
        except ValueError:
            pass
    if raw == "''":
        return ''
    return raw


def read_fact_file(path: Path) -> RecordRow | None:
    """Parse one record file back into a row dict (snake_case columns).

    Returns None when the file has no frontmatter — a stray .md in the
    directory is not a fact, and deleting it is the human's call, not ours.
    A fact file whose frontmatter is present but whose value is unparseable
    still reads (the value text passes through verbatim, the same way a
    non-JSON cell did in the SQL era).
    """
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return None
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    body = text[match.end():]
    # The writer emits exactly one separating blank line and one trailing
    # newline; a hand edit may have added more. Strip at most those.
    if body.startswith('\n'):
        body = body[1:]
    if body.endswith('\n'):
        body = body[:-1]
    row: dict[str, object] = dict(_ROW_DEFAULTS)
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        field, sep, value = line.partition(':')
        if not sep:
            continue
        field = field.strip()
        parsed = _parse_scalar(value)
        for fname, row_key in _FIELDS:
            if fname == field:
                row[row_key] = parsed
                break
    # The body is the value exactly as stored (JSON-encoded).
    row['fact_value'] = body
    # Numeric-ish fields that a hand edit may have left as text.
    for numeric in ('confidence', 'sensitive', 'version', 'use_count', 'injected_count'):
        raw = row.get(numeric)
        if isinstance(raw, str):
            try:
                row[numeric] = float(raw) if numeric == 'confidence' else int(float(str(raw)))
            except (TypeError, ValueError):
                row[numeric] = 1.0 if numeric == 'confidence' else 0
    if isinstance(row.get('sensitive'), float):
        row['sensitive'] = int(row['sensitive'])  # type: ignore[call-overload]
    if not str(row.get('fact_key') or ''):
        return None
    return _row_from_file(row)  # type: ignore[arg-type]


def _row_from_file(row: dict[str, object]) -> RecordRow:
    """Normalize the parsed file row: list fields back to their JSON-text
    storage shape and sensitive to an int flag (the exact shapes the rest of
    the store has always handled)."""
    out = dict(_ROW_DEFAULTS)
    out.update({k: v for k, v in row.items() if v is not None})
    for field in ('aliases', 'supersedes', 'derived_from'):
        raw = out.get(field)
        if isinstance(raw, list):
            out[field] = json.dumps(raw, ensure_ascii=False)
        elif not isinstance(raw, str) or not raw.strip():
            out[field] = '[]'
    if out.get('sensitive') in (True, 'true', '1'):
        out['sensitive'] = 1
    else:
        out['sensitive'] = 0
    try:
        out['confidence'] = float(str(out.get('confidence') or 1.0))
    except (TypeError, ValueError):
        out['confidence'] = 1.0
    for counter in ('use_count', 'injected_count', 'version'):
        try:
            out[counter] = int(float(str(out.get(counter) or 0)))
        except (TypeError, ValueError):
            out[counter] = 0
    if not out.get('status'):
        out['status'] = 'active'
    if not out.get('scope'):
        out['scope'] = 'global'
    if not out.get('kind'):
        out['kind'] = 'fact'
    if not out.get('category'):
        out['category'] = 'general'
    return out


def write_fact_file(row: RecordRow) -> Path:
    """Write one fact's file atomically (filestore rule 1). Returns its path."""
    key = str(row.get('fact_key') or '')
    scope = str(row.get('scope') or 'global')
    target = fact_path(key, scope)
    filestore.write_text(target, _render_fact_file(row))
    return target


def delete_fact_file(fact_key: str, scope: str = 'global') -> bool:
    try:
        fact_path(fact_key, scope).unlink(missing_ok=True)
        return True
    except OSError:
        return False


def load_facts() -> list[RecordRow]:
    """Every row in the record, one file each. Order: file scan order (the
    callers sort — the store's read doors apply their own ordering)."""
    rows: list[RecordRow] = []
    root = mirror_root()
    if not root.is_dir():
        return rows
    with _write_lock:
        for child in sorted(root.glob('*.md')):
            if child.name == _INDEX_NAME:
                continue
            row = read_fact_file(child)
            if row is not None:
                rows.append(row)
    return rows


def load_fact(fact_key: str) -> RecordRow | None:
    """The row for a key across scopes. A key has one home PER SCOPE, so two
    rows may share a key; the global one is the answer a bare key lookup has
    always meant (its stem is the un-prefixed one), and a scoped row wins
    only when no global row exists."""
    exact = fact_path(fact_key, 'global')
    row = read_fact_file(exact) if exact.exists() else None
    if row is not None:
        return row
    for candidate in load_facts():
        if str(candidate.get('fact_key') or '') == fact_key:
            return candidate
    return None


def indexable(row: RecordRow) -> bool:
    """The row-set the MEMORY.md index lists — the same exclusions the boot
    index has always applied (active, unexpired, non-sensitive, not a
    person). The RECORD holds every row; the index is the human's scan."""
    if str(row.get('status') or 'active') != 'active':
        return False
    if row.get('sensitive'):
        return False
    if str(row.get('subject_type') or '') == 'person':
        return False
    expires = str(row.get('expires_at') or '')
    return not expires


def _body_text(row: RecordRow) -> str:
    """The fact value (JSON-encoded on the row) rendered as readable text."""
    raw = row.get('factValue') if 'factValue' in row else row.get('fact_value')
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw  # type: ignore[arg-type]
    except (json.JSONDecodeError, TypeError):
        value = raw
    if isinstance(value, dict):
        parts = [str(value.get('fact') or '').strip()]
        details = str(value.get('details') or '').strip()
        if details:
            parts.append(details)
        return '\n\n'.join(p for p in parts if p)
    return str(value or '').strip()


def _write_index(root: Path, rows: list[RecordRow]) -> None:
    lines = [
        '# Memory index',
        '',
        'One line per fact; the full content is in the file beside it. These',
        'files ARE the memory record — reads and writes both go through them.',
        'A file you delete here deletes that memory; an edit here is the edit.',
        '',
    ]
    ordered = sorted(
        rows,
        key=lambda r: str(r.get('updated_at') or ''),
        reverse=True,
    )
    for row in ordered:
        if not indexable(row):
            continue
        key = as_str(row.get('fact_key'))
        if not key:
            continue
        title = as_str(row.get('title')) or key
        description = as_str(row.get('description'))
        if not description:
            description = _body_text(row)[:120]
        scope = as_str(row.get('scope')) or 'global'
        target = fact_path(key, scope).name
        lines.append(f'- [{title}]({target}) — {description}')
    filestore.write_text(root / _INDEX_NAME, '\n'.join(lines) + '\n')


def rewrite_index() -> int:
    """Regenerate MEMORY.md from the record. Returns the listed file count."""
    rows = load_facts()
    with _write_lock:
        _write_index(mirror_root(), rows)
    return sum(1 for r in rows if indexable(r))


def note_fact_changed(fact_key: str) -> None:
    """Rewrite MEMORY.md after a write or delete of one key.

    The fact's own file is written by the CRUD door (which holds the row);
    this is the index half. Cheap by contract: one scan + one write, and a
    failure is logged, never raised into the store write that triggered it.
    """
    try:
        rewrite_index()
    except OSError:
        logger.debug('record index rewrite failed for %s', fact_key, exc_info=True)


def rebuild_mirror() -> int:
    """Boot-time index regeneration (the historical name is kept).

    There is no derived copy to repair anymore — the record IS the files —
    so this only rebuilds the index and sweeps a fact file whose key has
    been renamed away from its stem (the hash-suffix stems make that a
    hand-edit-only state). Returns the listed count.
    """
    try:
        rows = load_facts()
        roots = mirror_root()
        wanted = {fact_path(str(r.get('fact_key')), str(r.get('scope') or 'global')).name for r in rows}
        for child in roots.glob('*.md'):
            if child.name == _INDEX_NAME:
                continue
            if child.name not in wanted:
                # A file the record cannot parse back to a key (or that a key
                # rename left behind) is not memory anyone can read — remove
                # it so the directory stays exactly the record.
                if read_fact_file(child) is None:
                    child.unlink(missing_ok=True)
        return rewrite_index()
    except OSError:
        logger.debug('record index rebuild failed', exc_info=True)
        return 0


def record_stats() -> dict[str, int]:
    """Counts for the health/stat surfaces: total rows and listed entries."""
    rows = load_facts()
    return {'rows': len(rows), 'indexed': sum(1 for r in rows if indexable(r))}


__all__ = [
    '_INDEX_NAME',
    'delete_fact_file',
    'fact_path',
    'indexable',
    'load_fact',
    'load_facts',
    'mirror_root',
    'normalize_subject_type',
    'note_fact_changed',
    'read_fact_file',
    'rebuild_mirror',
    'record_stats',
    'rewrite_index',
    'store_lock',
    'write_fact_file',
    '_body_text',
    '_safe_stem',
]
