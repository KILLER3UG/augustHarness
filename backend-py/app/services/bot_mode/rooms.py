"""Group rooms: deterministic serial rounds (no LLM router).

A room is 2-6 Bots deliberating over a shared, client-owned log. The driver
here is pure control flow: a user send runs ≤ ``MAX_ROUNDS`` serial rounds;
each round's speakers are chosen by a DETERMINISTIC mention parse (names,
display titles, collapsed forms, ``@everyone`` — never a model deciding who
talks); each speaker runs ONE turn in its persistent ``Group: <room>``
session fed only the new room messages; ``(pass)``/empty/failure is silence;
an all-pass round settles the room. Caps (3 rounds / 10 messages per send)
are enforced server-side (ruling OQ3), with a single ``max_rounds`` /
``max_messages`` seam so a later build can make them configurable without
touching the driver.

G-1 (review rounds): a member may end its turn with
``request_review(@reviewer, summary)``; the driver runs one extra round where
only the named reviewer speaks, appends the verdict, and (on ``changes:``)
gives the original member one more turn. Review rounds count against the
round cap. G-2 (escalation parity): a member that blocks twice for the same
cause in one send flips the room's ``needs_you`` badge automatically —
deterministic, no model.

The member runner is injectable (``runner=``) so the caps / pass / settle /
escalation semantics are unit-testable without a live model.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from app.json_narrowing import as_dict, as_int, as_str
from app.services.filestore import STATE_DIR, home, read_json, write_json

logger = logging.getLogger('august.bot_mode.rooms')

_MENTION_RE = re.compile(r'@([\w.-]+)')

# OQ3 ruling: fixed caps, one seam each. A later build may read these from
# brain-config; the driver only ever consults the resolved value.
MAX_ROUNDS = 3
MAX_MESSAGES = 10  # per user send
MIN_MEMBERS = 2
MAX_MEMBERS = 6

_REVIEW_MARK = 'request_review('
_PASS_TOKENS = frozenset({'', '(pass)', 'pass', '[pass]'})

_FILE = 'bot-rooms.json'
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── room store ───────────────────────────────────────────────────────────────
# ``bot_room`` / ``bot_room_message`` became one JSON state file:
# ``{'rooms': [...], 'messages': [...]}`` with the same row fields.


def _path() -> Path:
    return home(STATE_DIR) / _FILE


def _load() -> dict[str, list[dict[str, object]]]:
    raw = read_json(_path(), default={})
    if not isinstance(raw, dict):
        return {'rooms': [], 'messages': []}
    rooms = raw.get('rooms')
    messages = raw.get('messages')
    return {
        'rooms': [r for r in (rooms or []) if isinstance(r, dict)] if isinstance(rooms, list) else [],
        'messages': [m for m in (messages or []) if isinstance(m, dict)]
        if isinstance(messages, list)
        else [],
    }


def _save(data: dict[str, list[dict[str, object]]]) -> None:
    write_json(_path(), data)


def create_room(name: str, members: list[str]) -> int:
    clean = [m for m in dict.fromkeys(members) if m]
    if len(clean) < MIN_MEMBERS or len(clean) > MAX_MEMBERS:
        raise ValueError(f'a room needs {MIN_MEMBERS}-{MAX_MEMBERS} distinct members')
    try:
        with _LOCK:
            data = _load()
            rooms = list(data['rooms'])
            room_id = 1 + max((as_int(r.get('id'), 0) for r in rooms), default=0)
            rooms.append(
                {
                    'id': room_id,
                    'name': (name or 'Room').strip()[:120],
                    'members': json.dumps(clean),
                    'needs_you': 0,
                    'created_at': _now(),
                }
            )
            data['rooms'] = rooms
            _save(data)
            return room_id
    except Exception:
        logger.debug('create_room failed', exc_info=True)
        return 0


def _needs_you(row: dict[str, object]) -> bool:
    """The stored 0/1 badge → bool (as_bool only accepts real bools)."""
    try:
        return bool(int(str(row.get('needs_you') or 0)))
    except (TypeError, ValueError):
        return False


def _decoded_room(row: dict[str, object]) -> dict[str, object]:
    out = dict(row)
    try:
        out['members'] = json.loads(str(out.get('members') or '[]'))
    except (json.JSONDecodeError, TypeError):
        out['members'] = []
    out['needs_you'] = _needs_you(out)
    return out


def get_room(room_id: int) -> dict[str, object] | None:
    try:
        with _LOCK:
            rooms = _load()['rooms']
        for row in rooms:
            if as_int(row.get('id'), 0) == room_id:
                return _decoded_room(row)
        return None
    except Exception:
        return None


def list_rooms() -> list[dict[str, object]]:
    try:
        with _LOCK:
            rooms = _load()['rooms']
        rooms = sorted(rooms, key=lambda r: as_int(r.get('id'), 0), reverse=True)
        return [_decoded_room(r) for r in rooms]
    except Exception:
        return []


def delete_room(room_id: int) -> bool:
    try:
        with _LOCK:
            data = _load()
            rooms = list(data['rooms'])
            kept = [r for r in rooms if as_int(r.get('id'), 0) != room_id]
            if len(kept) == len(rooms):
                return False
            data['rooms'] = kept
            data['messages'] = [m for m in data['messages'] if as_int(m.get('room_id'), 0) != room_id]
            _save(data)
            return True
    except Exception:
        return False


def update_room(
    room_id: int,
    name: str | None = None,
    members: list[str] | None = None,
) -> dict[str, object] | None:
    """Update a room's name and/or member list."""
    room = get_room(room_id)
    if room is None:
        return None
    new_name = name.strip()[:120] if name is not None and name.strip() else str(room.get('name') or 'Room')
    if members is not None:
        clean = [m for m in dict.fromkeys(members) if m]
        if len(clean) < MIN_MEMBERS or len(clean) > MAX_MEMBERS:
            raise ValueError(f'a room needs {MIN_MEMBERS}-{MAX_MEMBERS} distinct members')
        members_json = json.dumps(clean)
    else:
        clean = list(cast(list[str], room.get('members') or []))
        members_json = json.dumps(clean)
    try:
        with _LOCK:
            data = _load()
            for row in data['rooms']:
                if as_int(row.get('id'), 0) == room_id:
                    row['name'] = new_name
                    row['members'] = members_json
            _save(data)
        return get_room(room_id)
    except Exception:
        logger.debug('update_room failed', exc_info=True)
        return None


def set_needs_you(room_id: int, value: bool) -> None:
    try:
        with _LOCK:
            data = _load()
            for row in data['rooms']:
                if as_int(row.get('id'), 0) == room_id:
                    row['needs_you'] = 1 if value else 0
            _save(data)
    except Exception:
        logger.debug('set_needs_you failed', exc_info=True)


def add_message(
    room_id: int,
    sender_agent: str,
    body: str,
    kind: str = 'message',
    thread_id: int | None = None,
) -> int:
    """Append one log row. ``thread_id=None`` makes the row its own thread
    root (its id becomes the thread_id); a given id joins that thread."""
    try:
        with _LOCK:
            data = _load()
            messages = list(data['messages'])
            mid = 1 + max((as_int(m.get('id'), 0) for m in messages), default=0)
            row: dict[str, object] = {
                'id': mid,
                'room_id': room_id,
                'sender_agent': sender_agent,
                'body': body,
                'kind': kind,
                'created_at': _now(),
                'thread_id': mid if thread_id is None else thread_id,
            }
            messages.append(row)
            data['messages'] = messages
            _save(data)
            return mid
    except Exception:
        logger.debug('add_message failed', exc_info=True)
        return 0


def room_log(room_id: int, limit: int = 200) -> list[dict[str, object]]:
    try:
        with _LOCK:
            messages = _load()['messages']
        rows = [m for m in messages if as_int(m.get('room_id'), 0) == room_id]
        rows.sort(key=lambda m: as_int(m.get('id'), 0))
        return rows[: max(1, int(limit))]
    except Exception:
        return []


def _last_message_id(room_id: int, thread_id: int | None = None) -> int:
    try:
        with _LOCK:
            messages = _load()['messages']
        best = 0
        for m in messages:
            if as_int(m.get('room_id'), 0) != room_id:
                continue
            if thread_id is not None and as_int(m.get('thread_id'), 0) != thread_id:
                continue
            mid = as_int(m.get('id'), 0)
            if mid > best:
                best = mid
        return best
    except Exception:
        return 0


def _new_messages_since(
    room_id: int, since_id: int, thread_id: int | None = None
) -> list[dict[str, object]]:
    try:
        with _LOCK:
            messages = _load()['messages']
        rows = [m for m in messages if as_int(m.get('room_id'), 0) == room_id]
        if thread_id is not None:
            rows = [m for m in rows if as_int(m.get('thread_id'), 0) == thread_id]
        rows = [m for m in rows if as_int(m.get('id'), 0) > since_id]
        rows.sort(key=lambda m: as_int(m.get('id'), 0))
        return rows
    except Exception:
        return []


def purge_agent(agent_id: str) -> None:
    """Drop a deleted Bot's room footprint (roster deletion cascade).

    Removes it from every room's member list; rooms that fall below the
    2-member minimum are deleted with their logs.
    """
    try:
        with _LOCK:
            data = _load()
            kept_rooms: list[dict[str, object]] = []
            dropped_ids: set[int] = set()
            changed = False
            for row in data['rooms']:
                try:
                    members = json.loads(str(row.get('members') or '[]'))
                except (json.JSONDecodeError, TypeError):
                    members = []
                if agent_id not in members:
                    kept_rooms.append(row)
                    continue
                changed = True
                remaining = [m for m in members if m != agent_id]
                if len(remaining) < 2:
                    dropped_ids.add(as_int(row.get('id'), 0))
                else:
                    row['members'] = json.dumps(remaining)
                    kept_rooms.append(row)
            if changed:
                data['rooms'] = kept_rooms
                data['messages'] = [
                    m for m in data['messages'] if as_int(m.get('room_id'), 0) not in dropped_ids
                ]
                _save(data)
    except Exception:
        logger.debug('bot room purge failed', exc_info=True)


# ── deterministic mention parse ──────────────────────────────────────────────


def parse_mentions(text: str, members: list[str]) -> list[str]:
    """Which members does ``text`` address? Deterministic, no model.

    Matches @handle, display title (collapsed, no spaces), and ``@everyone``.
    Returns member ids in roster order, de-duplicated. ``@everyone`` → all.
    Unknown handles are ignored (they pass through as plain text elsewhere).
    """
    from app.services.bot_mode import roster

    if not text:
        return []
    low = text.lower()
    if '@everyone' in low:
        return list(members)
    # Build handle/title → id for the member set only.
    key_to_id: dict[str, str] = {}
    for mid in members:
        bot = roster.get_bot(mid)
        if not bot:
            continue
        name = as_str(bot.get('name'))
        title = as_str(as_dict(bot.get('uiMeta'), {}).get('title'))
        if name:
            key_to_id[name.lower()] = mid
            key_to_id[name.lower().replace(' ', '')] = mid
        if title:
            key_to_id[title.lower().replace(' ', '')] = mid
    found: set[str] = set()
    for m in _MENTION_RE.finditer(text):
        tok = (m.group(1) or '').lower().replace(' ', '')
        matched = key_to_id.get(tok) or key_to_id.get((m.group(1) or '').lower())
        if matched and matched in members:
            found.add(matched)
    return [mid for mid in members if mid in found]


def parse_request_review(text: str) -> tuple[str, str] | None:
    """Detect ``request_review(@reviewer, summary)`` in a member's turn.

    Returns ``(reviewer_token, summary)`` or None. Deterministic string parse.
    """
    idx = (text or '').find(_REVIEW_MARK)
    if idx == -1:
        return None
    rest = text[idx + len(_REVIEW_MARK) :]
    close = rest.find(')')
    if close == -1:
        return None
    inner = rest[:close]
    reviewer, _, summary = inner.partition(',')
    return reviewer.strip().lstrip('@').strip(), summary.strip()


# ── member sessions (persistent Group: <room> per member) ────────────────────


def member_session(room_id: int, agent_id: str):
    """Find-or-create the member's persistent ``Group: <room>`` session.

    Stamped ``botAgentId`` (so M-2 scope + the DM/room run context resolve)
    and ``botRoom`` (the room id) so the log is attributable + isolated.
    """
    from app.services.bot_mode import roster
    from app.services.workbench import sessions as sessions_mod

    room = get_room(room_id)
    room_name = as_str((room or {}).get('name'), f'room {room_id}')
    for s in list(sessions_mod._sessions.values()):
        meta = getattr(s, 'metadata', None)
        if isinstance(meta, dict) and as_str(meta.get('botRoom')) == str(room_id) and as_str(meta.get('botAgentId')) == agent_id:
            return s
    chat = roster.ensure_canonical_bot_chat(agent_id)  # ensures the agent exists
    _ = chat
    sess = sessions_mod.create_workbench_session(agentId=agent_id, guardMode='full')
    sess.title = f'Group: {room_name}'
    meta = dict(sess.metadata or {})
    meta['botRoom'] = str(room_id)
    meta['botAgentId'] = agent_id
    sess.metadata = meta
    sessions_mod.save_sessions(immediate=True)
    return sess


def _default_runner() -> Callable[..., Awaitable[None]]:
    from app.services.workbench import workbench as wb

    return cast(Callable[..., Awaitable[None]], wb.sendWorkbenchMessageStream)


async def _member_turn(
    room_id: int, agent_id: str, feed: list[dict[str, object]], runner: object
) -> str:
    """Run one member turn over the new room messages; return its text."""
    sess = member_session(room_id, agent_id)
    lines = []
    for m in feed:
        who = 'the user' if as_str(m.get('sender_agent')) == 'user' else f'@{_handle(agent_id, m)}'
        lines.append(f'{who}: {as_str(m.get("body"))}')
    prompt = (
        f'You are in the group room "{_room_name(room_id)}" with your teammates. '
        'Respond to the new messages below with your own short contribution, or reply '
        '(pass) to stay silent. You may ask a teammate to review your work by ending '
        'with request_review(@teammate, what to check).\n\n' + '\n'.join(lines)
    )
    from app.services.workbench.emit_types import ASSISTANT_TEXT_EMIT_TYPES

    parts: list[str] = []

    def emit(ev: dict[str, object]) -> None:
        if isinstance(ev, dict) and ev.get('type') in ASSISTANT_TEXT_EMIT_TYPES and ev.get('content'):
            parts.append(as_str(ev['content']))

    run = cast(Callable[..., Awaitable[None]], runner or _default_runner())
    await run(sessionId=sess.id, message=prompt, agentId=agent_id, emit=emit)
    return ''.join(parts).strip()


def _handle(_agent_id: str, msg: dict[str, object]) -> str:
    from app.services.bot_mode import roster

    sender = as_str(msg.get('sender_agent'))
    bot = roster.get_bot(sender)
    return as_str(bot.get('name')) if bot else sender


def _room_name(room_id: int) -> str:
    room = get_room(room_id)
    return as_str((room or {}).get('name'), '')


# ── the round driver ─────────────────────────────────────────────────────────


async def run_room(
    room_id: int,
    user_text: str,
    thread_id: int | None = None,
    runner: object = None,
    max_rounds: int = 0,
    max_messages: int = 0,
) -> dict[str, object]:
    """Drive one user send through the room. Deterministic + capped.

    Round 1 speakers = the members the user @-mentioned (or all, if none).
    Each speaker runs one turn over only the new room messages. A ``(pass)``/
    empty/failed turn is silence (a ``pass`` row, not a message). A member may
    request a review → the next round is ONLY the named reviewer (a ``verdict``
    row); a ``changes:`` verdict gives the requesting member one revision turn.
    Members can @-pull each other mid-room. An all-pass round settles the room.
    Caps: ``max_rounds`` rounds, ``max_messages`` member messages per send
    (review rounds count against the round cap — G-1). Two consecutive blocks
    by one member flips ``needs_you`` (G-2). Returns a summary; the room log
    is the source of truth the UI reads afterward.
    """
    room = get_room(room_id)
    if not room:
        return {'error': 'no such room', 'rounds': 0, 'messages': 0}
    members = [str(m) for m in cast(list, room.get('members') or [])]
    if not members:
        return {'error': 'empty room', 'rounds': 0, 'messages': 0}
    rounds_cap = max_rounds or MAX_ROUNDS
    msgs_cap = max_messages or MAX_MESSAGES

    # A reply joins an existing thread; a new post roots its own thread.
    root_id = add_message(room_id, 'user', (user_text or '').strip(), 'message', thread_id=thread_id)
    active_thread = thread_id if thread_id is not None else root_id
    messages_used = 0
    last_seen = {m: 0 for m in members}
    consecutive_blocks = {m: 0 for m in members}
    escalated = False

    # Explicit round plan: `speakers` is who talks this round; `review` /
    # `revision` carry the G-1 one-shot follow-ups.
    speakers: list[str] = parse_mentions(user_text, members) or list(members)
    review: tuple[str, str] | None = None  # (reviewer_id, summary)
    review_requester = ''                  # member who asked for the review
    revision: str = ''                     # member owed a revision turn
    # 2.10: the reviewer speaks in the NEXT round, so the verdict
    # marker must survive the round boundary — `review` is cleared when we
    # schedule the round, so track the pending reviewer separately.
    pending_verdict_for = ''
    rounds_run = 0

    for _round in range(rounds_cap):
        if not speakers:
            break
        rounds_run += 1
        round_productive = False
        pulled: list[str] = []

        for agent in list(speakers):
            if messages_used >= msgs_cap:
                break
            feed = _new_messages_since(room_id, last_seen.get(agent, 0), active_thread)
            last_seen[agent] = _last_message_id(room_id, active_thread)
            try:
                out = await _member_turn(room_id, agent, feed, runner)
            except Exception:
                logger.debug('room member turn failed', exc_info=True)
                out = ''
            review_req = parse_request_review(out)
            if out.strip().lower() in _PASS_TOKENS:
                consecutive_blocks[agent] += 1
                add_message(room_id, agent, '(pass)', 'pass', thread_id=active_thread)
                if consecutive_blocks[agent] >= 2 and not escalated:
                    set_needs_you(room_id, True)
                    add_message(
                        room_id, agent, 'needs the user (blocked twice)', 'escalation',
                        thread_id=active_thread,
                    )
                    escalated = True
                continue
            consecutive_blocks[agent] = 0
            round_productive = True
            messages_used += 1
            is_verdict = bool(pending_verdict_for) and agent == pending_verdict_for
            add_message(
                room_id, agent, out, 'verdict' if is_verdict else 'message',
                thread_id=active_thread,
            )
            if is_verdict:
                pending_verdict_for = ''
            verdict_wants_changes = is_verdict and 'changes' in out.lower()
            pulled += [m for m in parse_mentions(out, members) if m != agent]
            if review_req and review is None:
                rid = next((m for m in members if _matches_handle(m, review_req[0])), '')
                if rid and rid != agent:
                    review = (rid, review_req[1])
                    review_requester = agent
            if verdict_wants_changes:
                revision = review_requester

        # Decide next round's speakers (priority: review > revision > pulls).
        if review is not None:
            reviewer = review[0]
            review = None
            pending_verdict_for = reviewer  # 2.10: mark the review round's turn
            speakers = [reviewer]
        elif revision:
            who = revision
            revision = ''
            speakers = [who]
        elif not round_productive:
            break  # all-pass round → settle
        else:
            speakers = [m for m in dict.fromkeys(pulled) if m in members]

    return {
        'rounds': rounds_run,
        'messages': messages_used,
        'settled': True,
        'escalated': escalated,
        'needsYou': escalated,
    }


def _matches_handle(agent_id: str, token: str) -> bool:
    from app.services.bot_mode import roster

    tok = (token or '').lower().replace(' ', '')
    bot = roster.get_bot(agent_id)
    if not bot:
        return False
    name = as_str(bot.get('name')).lower()
    title = as_str(as_dict(bot.get('uiMeta'), {}).get('title')).lower().replace(' ', '')
    return tok in (name, title)
