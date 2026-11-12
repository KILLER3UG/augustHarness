"""Part 16 Phases A+B — episode extraction + tier-1 scoring (2026-08-30).

The self-improvement loop's deterministic half. Units are EPISODES, not
conversations: failure→recovery, correction→accepted, and abandoned-approach
windows mined from what is ALREADY stored — the ``transcripts/`` jsonl files
(through the public ``get_messages`` door), the turn-outcomes telemetry, and
the guardrail log. No runtime change to the chat loop; the whole pass is
asynchronous and post-hoc.

Phase B layers tier-1 on top: a fingerprint per episode (turn_outcomes'
signature discipline generalized to ``cause-class:token`` shape), a fixed
six-criterion rubric scored with no model calls, and a cost gate — only the
top slice (≤ flagRateCap, default 5%) within a daily escalation budget is
flagged to tier 2, where the distiller judge (skill_distiller.py) runs.

Stores: ``<state>/episodes.json`` + ``<telemetry>/failure-fingerprints.json``
(the SQL-era episodes + failure_fingerprints tables). Fingerprints are
brain_query-searchable via the shared store helpers below. Retention: 90-day
prune, called from the consolidation sweep (OQ 6 default).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from app.json_narrowing import as_int
from app.lib.paths import assertPytestDataDirIsolated
from app.services.memory_store import tables
from app.services.message_sources import MACHINE_SOURCES as _MACHINE_SOURCES

logger = logging.getLogger(__name__)

# OQ 6 recommended default: 90-day episode retention (turn_outcomes keeps 30).
EPISODE_RETENTION_DAYS = 90
# Cost gate: at most this fraction of scored episodes is flagged to tier 2.
DEFAULT_FLAG_RATE_CAP = 0.05
# Daily budget on tier-2 escalations (config escalationBudgetPerDay).
DEFAULT_ESCALATION_BUDGET_PER_DAY = 2
# Paraphrase-level dedupe threshold — the consolidation merge constant.
_FINGERPRINT_SIMILARITY = 0.85
_MAX_EXCERPT = 240

_STOPWORDS = frozenset(
    'a an and are as at be but by for from had has have i in is it its of on '
    'or that the this to was were will with you your me my we our not do does '
    'did can could should would error failed failure'.split()
)

# ── typed event detection (deterministic regexes over stored text) ─────

# A straight or typographic apostrophe. Phones, Word and most markdown
# editors emit U+2019, so a pattern written with ASCII ' alone silently drops
# the turns of the users most likely to be correcting the model. Found by the
# item-4 recall sweep: "Don’t rebuild, just restart" mined nothing.
_APOS = r"['\u2019]"

_CORRECTION_RE = re.compile(
    r"\b(?:actually|correction)\b|\bthat(?:%ss| is) (?:wrong|incorrect|not right)\b"
    r"|\bI meant\b|\bno,? (?:not|use|try|do)\b|\bdon%s?t\b|\bnever mind\b" % (_APOS, _APOS),
    re.IGNORECASE,
)
_RESCUE_RE = re.compile(
    r"\bI (?:fixed|did|installed|added|created) it\b|\bmy bad\b|\bturns out I\b"
    r'|\bI did it myself\b|\bsorry,? (?:that|it) was (?:my|me)\b',
    re.IGNORECASE,
)
_ABANDON_RE = re.compile(
    r"\blet%s?s (?:try|go) (?:a )?different\b|\bforget (?:that|this) approach\b"
    r'|\bscrap that\b|\bdifferent approach\b|\bstart over\b|\bstep back\b'
    r"|\bthat approach (?:isn.t|is not) working\b" % (_APOS,),
    re.IGNORECASE,
)
# There is deliberately NO tool-error text matcher here. Mining failures from
# prose was measured at 41/41 false positives on the dev database: a tool
# result that merely *quoted* the error vocabulary read as a failure — and the
# string it quoted lives in skills/august-harness/SKILL.md, so the skill that
# documents the receipts manufactured fake episodes about itself. Tool errors
# are read from the durable receipt instead (see _errorReceipts).


def _messageText(content: object) -> str:
    """Flatten a stored message content to text.

    §12 F-1: the workbench persistence path stores assistant/tool messages
    as JSON DICTS (``{"content": ..., "tool_calls": [...]}`` —
    memory_store/sessions.py) and tool results as role='tool' dicts. The
    old flatten handled str + block-list only, so every dict-shaped message
    read as empty and the miner mined nothing from real transcripts.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get('text'), str):
                parts.append(str(block['text']))
        return '\n'.join(parts)
    if isinstance(content, dict):
        parts = []
        inner = content.get('content')
        if isinstance(inner, str):
            parts.append(inner)
        elif inner:
            parts.append(_messageText(inner))
        for tc in content.get('tool_calls') or []:
            if not isinstance(tc, dict):
                continue
            fnRaw = tc.get('function')
            fn = fnRaw if isinstance(fnRaw, dict) else {}
            name = str(fn.get('name') or tc.get('name') or '')
            args = str(fn.get('arguments') or tc.get('input') or '')
            if name:
                parts.append(f'{name} {args[:200]}'.strip())
        return '\n'.join(p for p in parts if p)
    return ''


def _loadContent(raw: object) -> object:
    """Parse a stored content cell; raw text passes through (§12 F-2 —
    the app itself writes non-JSON rows, e.g. [Proxy Self-Heal] nudges)."""
    s = str(raw if raw is not None else '')
    if not s:
        return ''
    try:
        return json.loads(s)
    except Exception:
        return s


# Machine-injected user-role blocks (harness plumbing, not the
# human talking). Mining them as corrections/rescues/abandons is noise.
_INJECTION_PREFIXES = (
    '[SUBAGENT RESULTS',
    '[SUBAGENT_COMPLETE',
    '[Proxy Self-Heal]',
    '[SYSTEM:',
    '[SYSTEM_INJECTION',
    # Written by workbench/sessions.py when a turn never closed (crash/restart),
    # so provider replay stays balanced. It arrives with source = NULL — the
    # provenance column cannot see it — and it is an instruction to the model,
    # not something the user said.
    '[interrupted]',
    '<memory_nudge',
)


def _isMachineInjected(text: str) -> bool:
    stripped = text.lstrip()
    return any(stripped.startswith(p) for p in _INJECTION_PREFIXES)


def _isMachineRow(source: str, text: str) -> bool:
    """Provenance-first machine filter (audit C3/D7, migration 049).

    The ``source`` field is the authority: the loop tags its
    plumbing once at write time (``harness_nudge``, ``queued_user``,
    ``subagent_results``), so no prefix list has to chase new block shapes.
    The legacy prefix filter remains ONLY for pre-049 rows whose source is
    NULL — messages have no retention, so old sessions keep their injected
    rows indefinitely and dropping the fallback would re-admit them.
    """
    if source:
        return source in _MACHINE_SOURCES
    return _isMachineInjected(text)


# ── window extraction ─────────────────────────────────────────


def _errorReceipts(blocks: object) -> list[str]:
    """The failures this message's structured blocks record, as excerpts.

    The structured payload (migration 047) carries the tool cards the harness
    itself decided were errors — ``tool.status == 'error'``, written from
    ``normalize_tool_result``'s ``is_error`` verdict. That is the difference
    between "this call failed" and "this text contains the word traceback".

    Accepts the stored ``blocksJson`` string, a decoded block list, or a full
    decoded payload dict — whatever the caller's read door handed over.
    """
    from app.services.memory_store.transcript_blocks import decode_blocks

    out: list[str] = []
    decoded: object = blocks
    if isinstance(blocks, str):
        decoded = decode_blocks(blocks).get('blocks')
    elif isinstance(blocks, dict):
        decoded = blocks.get('blocks', blocks)
    if not isinstance(decoded, list):
        return out
    for block in decoded:
        if not isinstance(block, dict) or block.get('type') != 'toolCall':
            continue
        tool = block.get('tool')
        if not isinstance(tool, dict) or tool.get('status') != 'error':
            continue
        name = str(tool.get('name') or '')
        body = str(block.get('content') or tool.get('result') or '')
        out.append(f'{name}: {body}'.strip(': '))
    return out


def _extractEvents(
    role: str,
    text: str,
    source: str = '',
    error_receipts: tuple[str, ...] = (),
) -> list[dict[str, str]]:
    """Typed events observable in one stored message.

    Tool errors come from the durable receipt, never from the message's text;
    the user-role events below stay text-detected because a correction is
    something the user *said*.
    """
    events: list[dict[str, str]] = []
    stripped = text.strip()
    if role in ('assistant', 'tool') and error_receipts:
        for excerpt in error_receipts:
            events.append({'type': 'tool_error', 'excerpt': excerpt[:_MAX_EXCERPT]})
    if not stripped:
        return events
    if role != 'user' or _isMachineRow(source, stripped):
        # Harness-injected user-role blocks are not human speech.
        return events
    if _CORRECTION_RE.search(stripped):
        events.append({'type': 'user_correction', 'excerpt': stripped[:_MAX_EXCERPT]})
    if _RESCUE_RE.search(stripped):
        events.append({'type': 'user_rescue', 'excerpt': stripped[:_MAX_EXCERPT]})
    if _ABANDON_RE.search(stripped):
        events.append({'type': 'abandoned_approach', 'excerpt': stripped[:_MAX_EXCERPT]})
    return events


def _innerText(content: object) -> str:
    """The assistant's own prose only (tool-call payloads excluded) — used
    to decide whether a message is a CLEAN continuation of the turn."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        inner = content.get('content')
        return inner.strip() if isinstance(inner, str) else ''
    if isinstance(content, list):
        return '\n'.join(
            str(b.get('text')) for b in content if isinstance(b, dict) and isinstance(b.get('text'), str)
        ).strip()
    return ''


def _session_messages(sessionId: str) -> list[dict[str, Any]]:
    """One session's transcript rows through the public read door.

    ``get_messages`` decodes the content cell and merges the structured
    payload, so the miner sees the same shapes the live loop wrote — including
    the ``source`` provenance and the ``blocks`` list the error receipts come
    from.
    """
    from app.services.memory_store import get_messages

    rows: list[dict[str, Any]] = []
    try:
        for m in get_messages(sessionId):
            rows.append(dict(m))
    except Exception:
        logger.debug('episode miner: transcript read failed for %s', sessionId, exc_info=True)
        return []
    rows.sort(key=lambda r: as_int(r.get('id'), 0))
    return rows


def extract_episodes(sessionId: str) -> list[dict[str, Any]]:
    """Mine one session's stored transcript into episode windows.

    Three window shapes (plan §3.1):
      * failure_recovery  — tool/assistant error → later clean continuation
        (resolved), user rescue (rescued), or session end (unresolved)
      * correction_accepted — user correction → assistant reply with no
        immediate re-correction
      * abandoned_approach — user abandon marker → continuation
    """
    rows = _session_messages(sessionId)
    # Content is parsed defensively — raw-text rows are real
    # (sessions.py stores str payloads verbatim) and must never abort mining.
    parsed = [_loadContent(r.get('content')) for r in rows]
    receipts: list[list[str]] = [_errorReceipts(r.get('blocksJson', r.get('blocks'))) for r in rows]
    msgs: list[tuple[int, str, str, str]] = [
        (as_int(r.get('id'), 0), str(r.get('role') or ''), _messageText(p), str(r.get('source') or ''))
        for r, p in zip(rows, parsed)
    ]
    clean: list[bool] = [
        bool(_innerText(p)) and not receipts[i] for i, p in enumerate(parsed)
    ]
    episodes: list[dict[str, Any]] = []
    n = len(msgs)
    for i, (mid, role, text, source) in enumerate(msgs):
        events = _extractEvents(role, text, source, tuple(receipts[i]))
        if not events:
            continue
        kinds = {e['type'] for e in events}
        if role in ('assistant', 'tool') and 'tool_error' in kinds:
            # failure_recovery: scan forward to the first assistant message
            # carrying real prose (a tool-call-only retry is NOT recovery),
            # or a rescue marker.
            outcome = 'unresolved'
            end = mid
            for j in range(i + 1, n):
                _, r2, t2, s2 = msgs[j]
                if r2 == 'user' and _RESCUE_RE.search(t2) and not _isMachineRow(s2, t2):
                    outcome = 'rescued'
                    end = msgs[j][0]
                    break
                if r2 == 'assistant' and clean[j]:
                    outcome = 'resolved'
                    end = msgs[j][0]
                    break
                if r2 == 'user' and _ABANDON_RE.search(t2) and not _isMachineRow(s2, t2):
                    outcome = 'unresolved'
                    end = msgs[j][0]
                    break
            episodes.append(
                {
                    'kind': 'failure_recovery',
                    'start_message_id': mid,
                    'end_message_id': end,
                    'events': events,
                    'outcome': outcome,
                }
            )
        elif role == 'user' and 'user_correction' in kinds:
            # correction_accepted: an assistant reply follows and the user
            # does not immediately re-correct (within the next 2 user turns).
            assistantReplied = any(r2 == 'assistant' for _, r2, _, _ in msgs[i + 1 : i + 3])
            reCorrections = sum(
                1
                for _, r2, t2, s2 in msgs[i + 1 : i + 6]
                if r2 == 'user' and not _isMachineRow(s2, t2) and _CORRECTION_RE.search(t2)
            )
            episodes.append(
                {
                    'kind': 'correction_accepted',
                    'start_message_id': mid,
                    'end_message_id': next(
                        (m for m, r2, _, _ in msgs[i + 1 :] if r2 == 'assistant'), mid
                    ),
                    'events': events,
                    'outcome': 'resolved' if (assistantReplied and reCorrections == 0) else 'unresolved',
                }
            )
        elif role == 'user' and 'abandoned_approach' in kinds:
            # abandoned_approach: the pivot itself is the window; whether the
            # session continued cleanly after decides the outcome.
            continued = any(r2 == 'assistant' for _, r2, _, _ in msgs[i + 1 :])
            episodes.append(
                {
                    'kind': 'abandoned_approach',
                    'start_message_id': mid,
                    'end_message_id': next(
                        (m for m, r2, _, _ in msgs[i + 1 :] if r2 == 'assistant'), mid
                    ),
                    'events': events,
                    'outcome': 'resolved' if continued else 'unresolved',
                }
            )
    return episodes


# ── fingerprints ───────────────────────────────────────────────

_SLUG_RE = re.compile('[a-z0-9]+')

_FINGERPRINTS_FILE = tables.failure_fingerprints_file


def _fingerprint_rows() -> list[dict[str, Any]]:
    return [dict(r) for r in tables.load_rows(_FINGERPRINTS_FILE())]


def _save_fingerprint_rows(rows: list[dict[str, Any]]) -> None:
    tables.save_rows(_FINGERPRINTS_FILE(), rows)


def _keyTokens(text: str, cap: int = 3) -> list[str]:
    """Normalized key tokens: slug-cased, stopword-stripped, by frequency —
    ties broken by FIRST occurrence (the subject words come first, and
    first-occurrence order keeps signatures stable across rewordings)."""
    counts: dict[str, int] = {}
    order: dict[str, int] = {}
    for idx, tok in enumerate(_SLUG_RE.findall((text or '').lower())):
        if len(tok) < 3 or tok in _STOPWORDS:
            continue
        if tok not in order:
            order[tok] = idx
        counts[tok] = counts.get(tok, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], order[kv[0]]))
    return [t for t, _ in ordered[:cap]]


def _causeClass(events: list[dict[str, Any]]) -> str:
    """Coarse cause class for a window (error-class discipline, generalized)."""
    kinds = [str(e.get('type')) for e in events]
    if 'user_rescue' in kinds:
        return 'user-rescue'
    if any(str(e.get('type')) == 'tool_error' for e in events):
        return 'tool-error'
    if 'abandoned_approach' in kinds:
        return 'abandoned-approach'
    if 'user_correction' in kinds:
        return 'user-correction'
    return 'other'


def fingerprint_for(episode: dict[str, Any]) -> str:
    """``cause-class:token`` signature — the canonical shape is
    ``missing-binary:ngspice``; here e.g. ``tool-error:ngspice-install``."""
    cause = _causeClass(episode.get('events') or [])
    excerpt = ' '.join(
        str(e.get('excerpt', '')) for e in episode.get('events') or []
    )
    tokens = _keyTokens(excerpt)
    return f"{cause}:{'-'.join(tokens) if tokens else 'general'}"


def upsert_fingerprint(fp: str) -> dict[str, Any]:
    """Insert or increment one fingerprint; returns its row."""
    now = datetime.now(timezone.utc).isoformat()
    with tables.store_lock(str(_FINGERPRINTS_FILE())):
        rows = _fingerprint_rows()
        for row in rows:
            if str(row.get('fingerprint') or '') == fp:
                row['episode_count'] = int(row.get('episode_count') or 0) + 1
                row['last_seen'] = now
                _save_fingerprint_rows(rows)
                return row
        row = {
            'id': tables.next_id(rows),
            'fingerprint': fp,
            'episode_count': 1,
            'first_seen': now,
            'last_seen': now,
            'status': 'open',
            'flagged': 0,
        }
        rows.append(row)
        _save_fingerprint_rows(rows)
        return row


def paraphrase_dedupe(fp: str, text: str, existing: list[tuple[str, str]]) -> str:
    """Collapse a near-duplicate fingerprint onto an existing signature.

    ``existing`` is (fingerprint, excerpt-text) pairs for the workspace's
    recent fingerprints. Two paraphrases converge when (a) the raw texts
    hit the consolidation merge threshold (≥0.85 BM25 — near-verbatim
    repeats) OR (b) the new signature's key tokens are CONTAINED in the
    existing signature's tokens — the fingerprint-space shape of "same
    failure, reworded".
    """
    cause, _, tokens = fp.partition(':')
    newTokens = set(tokens.split('-'))
    for otherFp, otherText in existing:
        otherCause, _, otherTokens = otherFp.partition(':')
        if otherCause != cause:
            continue  # different cause classes never merge
        if otherTokens and newTokens and set(otherTokens.split('-')).issubset(newTokens):
            return otherFp
        if otherText:
            from app.services.text_similarity import similarity

            if similarity(text, otherText) >= _FINGERPRINT_SIMILARITY:
                return otherFp
    return fp


# ── storage helpers ──────────────────────────────────────────────────────

_EPISODES_FILE = tables.episodes_file


def _episode_rows() -> list[dict[str, Any]]:
    return [dict(r) for r in tables.load_rows(_EPISODES_FILE())]


def _save_episode_rows(rows: list[dict[str, Any]]) -> None:
    tables.save_rows(_EPISODES_FILE(), rows)


def _encodedEvents(episode: dict[str, Any]) -> str:
    return json.dumps(episode.get('events') or [], ensure_ascii=False)


def _existingEpisodeId(episode: dict[str, Any]) -> int | None:
    """The stored row for this window, or None.

    Identity is the window's CONTENT — (session, kind, events, outcome) — not
    ``start_message_id``. The workbench save rewrites the whole transcript as
    DELETE-all + re-INSERT (``memory_store/sessions.py``), so the id of a
    window's first message changes at every durability barrier; keying dedupe
    on it made every re-mine look like a new episode. Four real windows became
    41 rows, and each pass re-incremented the fingerprint's ``episode_count``
    — faking the recurrence that escalates a fingerprint to the distiller.

    ``start_message_id`` is still stored (nothing reads it: it is provenance
    for a human looking up where a lesson came from). The one behavior change
    is honest and deliberate: two byte-identical failure windows in the SAME
    session collapse into one episode. Cross-session recurrence — the signal
    that actually matters — is counted by ``_sameCauseSessions``.
    """
    for row in _episode_rows():
        if (
            str(row.get('session_id') or '') == str(episode.get('session_id', ''))
            and str(row.get('kind') or '') == str(episode.get('kind', ''))
            and str(row.get('outcome') or 'unresolved') == str(episode.get('outcome', 'unresolved'))
            and str(row.get('events') or '') == _encodedEvents(episode)
        ):
            return as_int(row.get('id'), 0)
    return None


def save_episode(episode: dict[str, Any]) -> int:
    """Persist one mined episode (deduped on the window's content)."""
    # Surface tests that bypass the autouse isolatedData fixture.
    assertPytestDataDirIsolated('episode_miner.save_episode')
    existing = _existingEpisodeId(episode)
    if existing is not None:
        return existing
    now = datetime.now(timezone.utc).isoformat()
    path = _EPISODES_FILE()
    with tables.store_lock(str(path)):
        rows = _episode_rows()
        row_id = tables.next_id(rows)
        rows.append(
            {
                'id': row_id,
                'session_id': str(episode.get('session_id', '')),
                'kind': str(episode.get('kind', '')),
                'start_message_id': as_int(episode.get('start_message_id', 0), 0),
                'end_message_id': as_int(episode.get('end_message_id', 0), 0),
                'events': _encodedEvents(episode),
                'outcome': str(episode.get('outcome', 'unresolved')),
                'fingerprint_id': str(episode.get('fingerprint_id', '')),
                'tier': 1,
                'scope': str(episode.get('scope', '') or ''),
                'source': str(episode.get('source', '') or ''),
                'tier1_result': None,
                'judge_verdict': None,
                'applied_label': None,
                'quarantined': 0,
                'created_at': now,
            }
        )
        _save_episode_rows(rows)
    return row_id


def _episodeExists(episode: dict[str, Any]) -> bool:
    """True when the window is already stored — §12 F-6: re-mining the same
    window must not re-upsert its fingerprint (every 24h pass was inflating
    episode_count and bumping last_seen, faking recurrence and churning
    resolution state)."""
    return _existingEpisodeId(episode) is not None


def _existingFingerprintTexts(limit: int = 25) -> list[tuple[str, str]]:
    """(fingerprint, excerpt-text) pairs for paraphrase dedupe, rebuilt from
    the episodes store. §12 F-6 (related): record_episode used to read a
    nonexistent ``last_excerpt`` column, so only the token-containment
    dedupe lane ever ran."""
    out: list[tuple[str, str]] = []
    rows = [r for r in _episode_rows() if str(r.get('fingerprint_id') or '')]
    rows.sort(key=lambda r: as_int(r.get('id'), 0), reverse=True)
    seen: set[str] = set()
    for r in rows:
        fp = str(r.get('fingerprint_id') or '')
        if fp in seen:
            continue
        seen.add(fp)
        if len(seen) > int(limit):
            break
        try:
            events = json.loads(str(r.get('events') or '[]'))
        except Exception:
            events = []
        out.append((fp, _episodeText({'events': events})))
    return out


def record_episode(episode: dict[str, Any], existingFingerprints: list[tuple[str, str]] | None = None) -> int:
    """Fingerprint + dedupe + persist one episode. Returns the episode id."""
    fp = fingerprint_for(episode)
    if existingFingerprints is None:
        existingFingerprints = _existingFingerprintTexts()
    fp = paraphrase_dedupe(fp, _episodeText(episode), existingFingerprints)
    episode = {**episode, 'fingerprint_id': fp}
    isNew = not _episodeExists(episode)
    episodeId = save_episode(episode)
    if isNew:
        upsert_fingerprint(fp)
    return episodeId


def _episodeText(episode: dict[str, Any]) -> str:
    return ' '.join(str(e.get('excerpt', '')) for e in episode.get('events') or [])


def recent_fingerprints(limit: int = 25) -> list[dict[str, Any]]:
    rows = _fingerprint_rows()
    rows.sort(key=lambda r: str(r.get('last_seen') or ''), reverse=True)
    return rows[: int(limit)]


def _parseEpisodeRow(row: dict[str, Any]) -> dict[str, Any]:
    """Store row → dict with ``events`` parsed from its JSON field."""
    ep = dict(row)
    try:
        ep['events'] = json.loads(str(ep.get('events') or '[]'))
    except Exception:
        ep['events'] = []
    return ep


def unscored_episodes(limit: int = 200) -> list[dict[str, Any]]:
    """Tier-1 episodes that have not been scored yet (no tier-1 result).

    §12 F-3: the rubric result lives in ``tier1_result`` — the tier-2
    ``judge_verdict`` field must stay empty until the distiller judges, or
    the distiller's unjudged selector is empty by construction."""
    rows = [
        r for r in _episode_rows()
        if int(r.get('tier') or 1) == 1
        and r.get('tier1_result') is None
        and int(r.get('quarantined') or 0) == 0
    ]
    rows.sort(key=lambda r: as_int(r.get('id'), 0), reverse=True)
    return [_parseEpisodeRow(r) for r in rows[: int(limit)]]


def flagged_episodes(limit: int = 50) -> list[dict[str, Any]]:
    rows = [
        r for r in _episode_rows()
        if int(r.get('tier') or 1) == 2 and int(r.get('quarantined') or 0) == 0
    ]
    rows.sort(key=lambda r: as_int(r.get('id'), 0), reverse=True)
    return rows[: int(limit)]


def set_flagged(episodeId: int, flagged: bool) -> None:
    path = _EPISODES_FILE()
    with tables.store_lock(str(path)):
        rows = _episode_rows()
        for row in rows:
            if as_int(row.get('id'), 0) == int(episodeId):
                row['tier'] = 2 if flagged else 1
                break
        _save_episode_rows(rows)


def set_judge_verdict(episodeId: int, verdict: str) -> None:
    path = _EPISODES_FILE()
    with tables.store_lock(str(path)):
        rows = _episode_rows()
        for row in rows:
            if as_int(row.get('id'), 0) == int(episodeId):
                row['judge_verdict'] = verdict
                row['tier'] = 2
                break
        _save_episode_rows(rows)


def set_applied_label(episodeId: int, label: str) -> None:
    """Stamp which skill (or promotion verdict) the episode produced."""
    path = _EPISODES_FILE()
    with tables.store_lock(str(path)):
        rows = _episode_rows()
        for row in rows:
            if as_int(row.get('id'), 0) == int(episodeId):
                row['applied_label'] = label
                break
        _save_episode_rows(rows)


# ── tier-1 rubric ───────────────────────────────

# Six fixed criteria. Weights sum to 1.0; each subscore ∈ [0, 1].
_RUBRIC_WEIGHTS = {
    'completion': 0.25,
    'recurrence': 0.25,
    'correctionCount': 0.15,
    'recoveryQuality': 0.15,
    'causeStability': 0.10,
    'generalizability': 0.10,
}

_PROJECT_SPECIFIC_RE = re.compile(
    r'\b(?:C:\\|/home/|/Users/|C:/)[^\s]+|\b[a-z]+_[a-z]+\b', re.IGNORECASE
)


def score_episode(episode: dict[str, Any], fingerprintCount: int, sameCauseSessions: int) -> dict[str, Any]:
    """The six fixed rubric criteria — no LLM.

    Returns per-criterion subscores plus the weighted total. Higher =
    more worth escalating: resolved windows with recurring stable causes
    that generalize beyond one project.
    """
    outcome = str(episode.get('outcome', 'unresolved'))
    events = list(episode.get('events') or [])
    text = _episodeText(episode)

    completion = {'resolved': 1.0, 'rescued': 0.5}.get(outcome, 0.0)
    # Window length is the recovery-cost proxy: fewer messages to get back
    # to a clean state = better recovery quality.
    span = max(1, int(episode.get('end_message_id', 0)) - int(episode.get('start_message_id', 0)) + 1)
    recoveryQuality = max(0.0, 1.0 - (span - 1) / 10.0)
    correctionCount = max(0.0, 1.0 - sum(1 for e in events if e.get('type') == 'user_correction') / 3.0)
    recurrence = min(1.0, max(0, fingerprintCount - 1) / 3.0)
    causeStability = min(1.0, max(0, sameCauseSessions - 1) / 3.0)
    generalizability = 0.0 if _PROJECT_SPECIFIC_RE.search(text) else 1.0

    subscores = {
        'completion': completion,
        'correctionCount': correctionCount,
        'recurrence': recurrence,
        'recoveryQuality': recoveryQuality,
        'causeStability': causeStability,
        'generalizability': generalizability,
    }
    total = sum(_RUBRIC_WEIGHTS[k] * v for k, v in subscores.items())
    return {'score': round(total, 4), 'subscores': subscores, 'weights': dict(_RUBRIC_WEIGHTS)}


def _sameCauseSessions(fp: str) -> int:
    return len(
        {
            str(r.get('session_id') or '')
            for r in _episode_rows()
            if str(r.get('fingerprint_id') or '') == fp and int(r.get('quarantined') or 0) == 0
        }
    )


def flag_top_slice(
    flagRateCap: float = DEFAULT_FLAG_RATE_CAP,
    budgetPerDay: int = DEFAULT_ESCALATION_BUDGET_PER_DAY,
) -> dict[str, int]:
    """Score unscored tier-1 episodes, then flag the top slice to tier 2.

    Cost gate (plan §3.2): at most ``flagRateCap`` of scored episodes and
    at most ``budgetPerDay`` per pass, recurrence-ranked. Flagged
    fingerprints flip ``flagged = 1`` so the distiller can find them.
    """
    episodes = unscored_episodes()
    if not episodes:
        return {'scored': 0, 'flagged': 0}
    fingerprints = {str(r.get('fingerprint') or ''): r for r in _fingerprint_rows()}
    by_id = {as_int(r.get('id'), 0): r for r in _episode_rows()}
    scored: list[tuple[float, dict[str, Any]]] = []
    for ep in episodes:
        fp = str(ep.get('fingerprint_id') or '')
        fpRow = fingerprints.get(fp)
        fpCount = int(fpRow.get('episode_count') or 0) if fpRow else 1
        result = score_episode(ep, fpCount, _sameCauseSessions(fp))
        # The tier-1 rubric is NOT a judge verdict — writing it into
        # judge_verdict made the distiller's unjudged set always empty.
        target = by_id.get(as_int(ep.get('id'), 0))
        if target is not None:
            target['tier1_result'] = json.dumps({'tier1': result}, ensure_ascii=False)
        scored.append((result['score'], ep))
    _save_episode_rows(list(by_id.values()))

    scored.sort(key=lambda pair: (-pair[0], -int(pair[1].get('id', 0))))
    # 2.14: floor at 1 when there are candidates — int(len*cap)
    # rounded to 0 for any pass with <20 unscored episodes, so typical desktop
    # installs NEVER escalated anything to tier-2 review.
    capCount = int(len(scored) * max(0.0, min(1.0, flagRateCap)))
    if scored and capCount < 1:
        capCount = 1
    flaggedCount = 0
    for score, ep in scored:
        if flaggedCount >= budgetPerDay or flaggedCount >= capCount:
            break
        if score <= 0:
            break
        set_flagged(int(ep['id']), True)
        fp = str(ep.get('fingerprint_id') or '')
        if fp:
            fpath = _FINGERPRINTS_FILE()
            with tables.store_lock(str(fpath)):
                fp_rows = _fingerprint_rows()
                for row in fp_rows:
                    if str(row.get('fingerprint') or '') == fp:
                        row['flagged'] = 1
                        break
                _save_fingerprint_rows(fp_rows)
        flaggedCount += 1
    return {'scored': len(scored), 'flagged': flaggedCount}


# ── scheduled pass + retention ───────────────────────────────────────────


def _sessionReceipts(sessionId: str) -> list[str]:
    """Every error receipt this session's transcript still carries."""
    out: list[str] = []
    for r in _session_messages(sessionId):
        out.extend(_errorReceipts(r.get('blocksJson', r.get('blocks'))))
    return out


def quarantine_unverified_tool_errors() -> dict[str, int]:
    """Mark, never delete, episodes no receipt can stand behind (item 5).

    Until `2c8f6ac7` a ``tool_error`` event came from prose matching, so a tool
    result that merely *quoted* the error vocabulary mined as a failure. The dev
    database measured 41 such episodes and zero messages carrying a receipt —
    every one invented. Deleting them would destroy the only record of how the
    loop escalated, so they stay and every consumer that ACTS on an episode
    stops seeing them.

    Runs at the head of every mining pass and is safe to re-run: an episode is
    only marked when NO receipt in its session matches its excerpt, and a
    post-receipt episode is backed by one by construction, so nothing newer is
    ever caught. If any one of a window's failures is real the window is left
    whole — quarantining a mixed episode would discard true evidence to tidy a
    counter.
    """
    path = _EPISODES_FILE()
    with tables.store_lock(str(path)):
        rows = _episode_rows()
        candidates = [
            r for r in rows
            if int(r.get('quarantined') or 0) == 0
            and '"tool_error"' in str(r.get('events') or '')
        ]
        marked = 0
        touched: set[str] = set()
        receipt_cache: dict[str, list[str]] = {}
        for row in candidates:
            try:
                events = json.loads(str(row.get('events') or '[]'))
            except Exception:
                continue
            excerpts = [
                str(e.get('excerpt') or '')
                for e in events
                if isinstance(e, dict) and e.get('type') == 'tool_error'
            ]
            excerpts = [x for x in excerpts if x]
            if not excerpts:
                continue
            sid = str(row.get('session_id') or '')
            if sid not in receipt_cache:
                receipt_cache[sid] = _sessionReceipts(sid)
            receipts = receipt_cache[sid]
            # The excerpt was capped when it was written, so a shared prefix is the
            # honest comparison — equality would mark real episodes whose tool
            # output merely grew a truncation marker afterwards.
            if any(r.startswith(x[:60]) for x in excerpts for r in receipts):
                continue
            row['quarantined'] = 1
            marked += 1
            if str(row.get('fingerprint_id') or ''):
                touched.add(str(row.get('fingerprint_id')))
        _save_episode_rows(rows)
    # episode_count is the recurrence score that escalates a fingerprint to the
    # distiller. Leaving it counting invented windows would keep proposing the
    # same non-problem after the rows themselves stopped being visible.
    if touched:
        fpath = _FINGERPRINTS_FILE()
        with tables.store_lock(str(fpath)):
            fp_rows = _fingerprint_rows()
            for fp in touched:
                n = sum(
                    1
                    for r in _episode_rows()
                    if str(r.get('fingerprint_id') or '') == fp and int(r.get('quarantined') or 0) == 0
                )
                for row in fp_rows:
                    if str(row.get('fingerprint') or '') != fp:
                        continue
                    if n == 0:
                        # Clearing the flag matters as much as the count: the distiller
                        # selects by ``flagged = 1``, so an invented fingerprint with no
                        # live episode behind it would keep being proposed forever.
                        row['episode_count'] = 0
                        row['flagged'] = 0
                    else:
                        row['episode_count'] = n
            _save_fingerprint_rows(fp_rows)
    total = sum(1 for r in _episode_rows() if int(r.get('quarantined') or 0) == 1)
    return {
        'candidates': len(candidates),
        'quarantined': marked,
        'quarantinedTotal': total,
        'fingerprintsRecounted': len(touched),
    }


def _recent_sessions(since: datetime, session_cap: int = 400) -> list[str]:
    """Sessions whose transcripts carry a message newer than ``since``.

    The transcript files live as ``transcripts/<sessionId>.jsonl``; files
    modified after the cutoff are read, newest-modified first, and a session
    qualifies when any line's stamp parses past the cutoff. The 2.17 lesson
    (created_at stored space-separated while `since` is ISO with a 'T') is
    handled by parsing both spellings instead of comparing strings.
    """
    from app.lib.paths import dataPath

    directory = dataPath('transcripts')
    if not directory.is_dir():
        return []
    files = sorted(
        directory.glob('*.jsonl'),
        key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
        reverse=True,
    )[:session_cap]
    out: list[str] = []
    for path in files:
        sid = path.stem
        hit = False
        for row in tables.ledger_rows(path):
            raw = str(row.get('created_at') or row.get('createdAt') or '')
            stamp = _parse_stamp(raw)
            if stamp is not None and stamp >= since:
                hit = True
                break
        if hit:
            out.append(sid)
    return out


def _parse_stamp(raw: object) -> datetime | None:
    text = str(raw or '').strip()
    if not text:
        return None
    try:
        normalized = text[:-1] + '+00:00' if text.endswith('Z') else text
        stamp = datetime.fromisoformat(normalized)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp
    except (ValueError, TypeError):
        return None


def mine_sessions(sinceDays: int = 30) -> dict[str, int]:
    """Extract episodes from recent sessions."""
    quarantine_unverified_tool_errors()
    since = datetime.now(timezone.utc) - timedelta(days=sinceDays)
    sids = _recent_sessions(since)
    existing = _existingFingerprintTexts(limit=1000)
    extracted = 0
    for sid in sids:
        # Stamp each episode with the source session's M-2 scope
        # ('' = global). A Bot's private home-chat episodes must not later
        # surface as globally injected <memory> lessons in every session —
        # the leak class the remember/forget doors closed, via the side door.
        scope = ''
        try:
            from app.services import session_scope as _ss
            from app.services.workbench.sessions import get_workbench_session

            sess = get_workbench_session(str(sid))
            if sess is not None:
                scope = str(_ss.resolve_scope(sess) or '')
        except Exception:
            scope = ''
        for episode in extract_episodes(str(sid)):
            episode['session_id'] = str(sid)
            episode['scope'] = scope
            isNew = not _episodeExists(episode)
            record_episode(episode, existingFingerprints=existing)
            if isNew:
                extracted += 1
    return {'sessions': len(sids), 'episodes': extracted}


def prune_old_episodes(days: int = EPISODE_RETENTION_DAYS) -> int:
    """OQ 6 default: 90-day prune, called from the consolidation sweep."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    path = _EPISODES_FILE()
    with tables.store_lock(str(path)):
        rows = _episode_rows()
        kept = [r for r in rows if (_parse_stamp(r.get('created_at')) or datetime.now(timezone.utc)) >= cutoff]
        removed = len(rows) - len(kept)
        if removed:
            _save_episode_rows(kept)
        return removed


def guardrail_block_hotspots(limit: int = 10) -> list[dict[str, Any]]:
    """Top blocked (tool, reason-prefix) pairs in the recent log window.

    The refine pass evidence builder reads this: a guardrail that keeps
    firing is a harness-shape signal (missing tool, wrong policy), not just
    a blocked call. Aggregated server-side so the evidence stays small.
    """
    from app.lib.paths import dataPath

    path = dataPath('telemetry') / 'tool-guardrail-log.jsonl'
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    counts: dict[tuple[str, str], int] = {}
    try:
        for row in tables.ledger_rows(path):
            created = str(row.get('created_at') or row.get('createdAt') or '')
            if created and created < cutoff:
                continue
            tool = str(row.get('tool_name') or row.get('toolName') or '')
            reason = str(row.get('reason') or '')[:80]
            key = (tool, reason)
            counts[key] = counts.get(key, 0) + 1
    except Exception:
        logger.debug('guardrail hotspot read failed', exc_info=True)
        return []
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[: int(limit)]
    return [{'tool_name': t, 'reason_head': r, 'n': n} for (t, r), n in ranked]


def _judgeHealth() -> dict[str, Any]:
    """Last success and last failure of the distiller judge, with its cause.

    Read from the lifecycle ledger rather than a counter column: the failure
    detail already carries the named cause from the recorder, and a separate
    counter would be a second thing that can drift from the log.
    """
    rows = [
        r for r in tables.ledger_rows(tables.lifecycle_file())
        if str(r.get('event_type') or r.get('eventType') or '')
        in ('distiller_judge_succeeded', 'distiller_judge_failed', 'distiller_judge_unavailable')
    ]
    rows.sort(key=lambda r: as_int(r.get('id'), 0))

    def _last(event_type: str) -> tuple[str, dict[str, Any]] | tuple[None, None]:
        for row in reversed(rows):
            if str(row.get('event_type') or row.get('eventType') or '') != event_type:
                continue
            try:
                detail = json.loads(str(row.get('detail') or '{}'))
            except Exception:
                detail = {}
            return str(row.get('created_at') or row.get('createdAt') or ''), (
                detail if isinstance(detail, dict) else {}
            )
        return None, None

    successes = [r for r in rows if str(r.get('event_type') or r.get('eventType') or '') == 'distiller_judge_succeeded']
    failures = [r for r in rows if str(r.get('event_type') or r.get('eventType') or '') == 'distiller_judge_failed']
    lastSuccess, successDetail = _last('distiller_judge_succeeded')
    lastFailure, failureDetail = _last('distiller_judge_failed')
    unavailable, _ = _last('distiller_judge_unavailable')
    return {
        'successes': len(successes),
        'failures': len(failures),
        'lastSuccess': lastSuccess,
        'lastSuccessDetail': successDetail or {},
        'lastFailure': lastFailure,
        'lastFailureDetail': failureDetail or {},
        # A config fault never arms a cooldown, so without this the report
        # could look healthy while the judge could not run at all.
        'lastUnavailable': unavailable,
    }


def learning_report() -> dict[str, Any]:
    """Counters for the Phase E skillLearningReport blob."""
    episodes = _episode_rows()
    fingerprints = _fingerprint_rows()
    counts = {
        # Quarantined windows are reported, never counted as evidence: they are
        # the record of a detector that has since been fixed, not a lesson.
        'episodes': sum(1 for r in episodes if int(r.get('quarantined') or 0) == 0),
        'quarantined': sum(1 for r in episodes if int(r.get('quarantined') or 0) == 1),
        'tier2': sum(
            1 for r in episodes
            if int(r.get('tier') or 1) == 2 and int(r.get('quarantined') or 0) == 0
        ),
        'judged': sum(
            1 for r in episodes
            if int(r.get('tier') or 1) == 2
            and r.get('judge_verdict') is not None
            and int(r.get('quarantined') or 0) == 0
        ),
        'fingerprints': len(fingerprints),
        'flaggedFingerprints': sum(1 for r in fingerprints if int(r.get('flagged') or 0) == 1),
        'judge': _judgeHealth(),
        'resolvedFingerprints': sum(
            1 for r in fingerprints if str(r.get('status') or '') == 'resolved'
        ),
    }
    return cast(dict[str, Any], counts)


# ── Phase E: resolution check + demotion ─────────

_RESOLUTION_WINDOW_DAYS = 30


def _fingerprintSkillMap() -> dict[str, str]:
    """fingerprint → skill name, from APPLIED skill proposals that carry
    payload.fingerprint (the distiller stamps it at propose time)."""
    out: dict[str, str] = {}
    try:
        from app.services.harness_self_improve import list_proposals

        for p in list_proposals():
            if p.get('status') != 'applied':
                continue
            payload = p.get('payload')
            if not isinstance(payload, dict):
                continue
            fp = str(payload.get('fingerprint', '')).strip()
            name = str(payload.get('name', '')).strip()
            if fp and name and str(p.get('kind', '')).startswith('skill_'):
                out.setdefault(fp, name)
    except Exception:
        pass
    return out


def _skillLoadCount(skillName: str) -> int:
    """Loads recorded for a skill — read through the ONE sidecar resolver
    (``skill_service.usage_sidecar_path``). Building the path here is what
    made bundled-skill counters unwritable-from-the-data-dir and unreadable
    from the install tree at the same time; proposal logic and thresholds are
    unchanged."""
    try:
        from app.services.skill_service import read_skill_usage

        return as_int(read_skill_usage(skillName).get('count'), 0)
    except Exception:
        return 0


def run_resolution_check(windowDays: int = _RESOLUTION_WINDOW_DAYS) -> dict[str, Any]:
    """Phase E measurement.

    A shipped skill's fingerprint is monitored: 0 recurrences for the
    window → ``resolved``; a RESOLVED fingerprint that recurs re-flags and
    drafts a revision-or-retire suggestion; a shipped skill with ZERO loads
    and no recurrence drafts a demotion (skill_delete) proposal — all
    suggestion-only, human-approved, never auto-deleted (OQ 5 default)."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=windowDays)).isoformat()
    skillByFp = _fingerprintSkillMap()
    resolvedCount = 0
    recurredCount = 0
    demotionCount = 0

    for fpRow in recent_fingerprints(limit=200):
        fp = str(fpRow.get('fingerprint') or '')
        status = str(fpRow.get('status') or 'open')
        lastSeen = str(fpRow.get('last_seen') or '')
        skill = skillByFp.get(fp, '')
        if not skill:
            continue
        stale = bool(lastSeen) and lastSeen < cutoff
        if status in ('open', 'skill_drafted') and stale:
            _set_fingerprint_status_row(fpRow, 'resolved')
            status = 'resolved'  # downstream demotion check sees the update
            resolvedCount += 1
        elif status == 'resolved' and not stale:
            # Recurrence after resolution: re-flag + revision-or-retire
            # suggestion (observation — a human decides revise vs retire).
            _set_fingerprint_status_row(fpRow, 'open', flag=1)
            recurredCount += 1
            _file_suggestion(
                kind='observation',
                problem=f'Recurrence after resolution for {skill!r} (fingerprint {fp})',
                evidence=f'fingerprint {fp} recurred at {lastSeen} after being resolved',
                proposal=(
                    f'The fingerprint {skill!r} was distilled from recurred. Revise the skill '
                    '(skill_patch proposal) or retire it — human decision.'
                ),
                payload={'fingerprint': fp, 'action': 'revise_or_retire', 'target': skill, 'origin': 'distilled'},
            )
        if status == 'resolved' and stale and _skillLoadCount(skill) == 0:
            # Zero loads + no recurrence in the window → demotion SUGGESTION.
            filed = _file_suggestion(
                kind='skill_delete',
                problem=f'Demotion suggestion for {skill!r}: zero loads, no recurrence in {windowDays}d',
                evidence=f'fingerprint {fp} resolved; usage sidecar count = 0',
                proposal=f'Disable/delete skill {skill!r} (human-approved; nothing auto-deletes).',
                payload={'name': skill, 'fingerprint': fp, 'action': 'demote', 'target': skill, 'origin': 'distilled'},
            )
            if filed:
                demotionCount += 1
    return {'resolved': resolvedCount, 'recurred': recurredCount, 'demotionSuggestions': demotionCount}


def _set_fingerprint_status_row(row: dict[str, Any], status: str, flag: int | None = None) -> None:
    """Write one fingerprint row's status (and optionally its flag)."""
    fpath = _FINGERPRINTS_FILE()
    with tables.store_lock(str(fpath)):
        rows = _fingerprint_rows()
        for r in rows:
            if str(r.get('fingerprint') or '') == str(row.get('fingerprint') or ''):
                r['status'] = status
                if flag is not None:
                    r['flagged'] = flag
                break
        _save_fingerprint_rows(rows)


def set_fingerprint_status(fp: str, status: str) -> None:
    """The advertised statuses (open | skill_drafted | resolved |
    retired) must actually be written. ``skill_drafted`` is set when a
    distilled draft files; ``retired`` when the fingerprint's skill_delete
    applies (from ANY prior state — demotions flow from resolved). A draft
    never resurrects a resolved fingerprint to drafted."""
    if not fp:
        return
    fpath = _FINGERPRINTS_FILE()
    with tables.store_lock(str(fpath)):
        rows = _fingerprint_rows()
        for row in rows:
            if str(row.get('fingerprint') or '') != str(fp):
                continue
            if status == 'retired':
                row['status'] = str(status)
            elif str(row.get('status') or '') == 'open':
                row['status'] = str(status)
            break
        _save_fingerprint_rows(rows)


def _file_suggestion(**kw: Any) -> bool:
    """File a suggestion proposal, deduped on (fingerprint, action, target).
    Returns True only when a proposal was actually filed — dedupe (including
    §12 F-8 rejected-draft suppression) reports False so callers must not
    count a skipped suggestion."""
    try:
        from app.services.harness_self_improve import save_proposal

        payload = kw.get('payload') or {}
        from app.services.skill_distiller import _draftExists

        if _draftExists(
            str(payload.get('fingerprint', '')),
            str(payload.get('action', '')),
            str(payload.get('target', '')),
        ):
            return False
        save_proposal(
            problem=str(kw.get('problem', '')),
            evidence=str(kw.get('evidence', '')),
            proposal=str(kw.get('proposal', '')),
            rollback='reject the proposal — nothing has changed yet (suggestion-only).',
            kind=str(kw.get('kind', 'observation')),
            payload=payload,
        )
        return True
    except Exception as exc:
        logger.debug('suggestion filing failed: %s', exc)
        return False
