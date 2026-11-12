"""Usage analytics and statistics endpoints backed by the usage-events ledger.

The ``usage_events`` table became ``<dataDir>/telemetry/usage-events.jsonl``
(one JSON line per event, the row the table carried); ``memory_store.rest``
owns the writer, this router owns the aggregates.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query

from app.services import memory_store
from app.services.memory_store import tables

router = APIRouter(prefix="/api/usage", tags=["usage"])


def _parse_range_cutoff(range_str: str) -> tuple[datetime, str]:
    now = datetime.now(timezone.utc)
    days = 7 if range_str == "7d" else 30
    cutoff = now - timedelta(days=days)
    return cutoff, cutoff.strftime("%Y-%m-%d %H:%M:%S")


def _window_rows(cutoff_str: str) -> list[dict[str, Any]]:
    """Usage rows with created_at in the window (newest last, file order)."""
    return [
        row
        for row in tables.ledger_rows(tables.usage_events_file())
        if str(row.get("created_at") or "") >= cutoff_str
    ]


def _tokens(row: dict[str, Any]) -> int:
    return int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0)


@router.get("")
def list_usage_events(limit: int = Query(200, ge=1, le=1000)) -> list[dict[str, Any]]:
    """List recent raw usage events (newest first)."""
    return memory_store.list_usage(limit=limit)


@router.get("/session")
def get_session_usage(id: str = Query(..., description="Session ID")) -> dict[str, Any]:
    """Get aggregated usage for a specific session."""
    return memory_store.get_usage(id)


@router.get("/sessions")
def get_sessions_usage(
    ids: str = Query(
        ...,
        description="Comma-separated session IDs",
        max_length=8000,
    ),
) -> dict[str, Any]:
    """Get input/output token totals for a SET of sessions, in one query.

    The sidebar renders a token delta on every visible chat row. This is the
    batch behind it: `get_session_usage` fetches up to 500 per-event rows and
    sweeps the pricing table per session, so calling it once per row turned a
    list render into N round trips. Only the four aggregate fields the delta
    needs are returned — cost and the event list stay on `/session`.
    """
    requested = [part for part in ids.split(",") if part.strip()]
    return {"results": memory_store.get_usage_batch(requested)}


@router.get("/stats")
def get_usage_stats(range: str = Query("30d")) -> dict[str, Any]:  # noqa: A002 — public query name
    """Get real summary statistics for the specified time range."""
    _, cutoff_str = _parse_range_cutoff(range)
    rows = _window_rows(cutoff_str)

    # Total tokens, sessions, messages in the range
    total_tokens = sum(_tokens(r) for r in rows)
    cache_hit = sum(int(r.get("cache_hit_tokens") or 0) for r in rows)
    cache_miss = sum(int(r.get("cache_miss_tokens") or 0) for r in rows)
    session_count = len({str(r.get("session_id") or "") for r in rows if r.get("session_id")})
    message_count = len(rows)
    active_dates_set = {str(r.get("created_at") or "")[:10] for r in rows}
    active_days = len({d for d in active_dates_set if d})
    # The cache split, in the SAME key names `memory_store.get_usage` already
    # uses. These columns were written and never read here, so a session that
    # re-read 60k cached tokens looked identical to one that re-sent them: the
    # page could not explain why its own cost was low. `totalTokens` stays
    # BILLED tokens (input + output) — folding cache reads into it would move
    # every historical number on the page and double-count the prompt.
    cache_total = cache_hit + cache_miss

    # Peak tokens in a single day
    by_day: dict[str, int] = {}
    for r in rows:
        day = str(r.get("created_at") or "")[:10]
        if day:
            by_day[day] = by_day.get(day, 0) + _tokens(r)
    peak_tokens = max(by_day.values()) if by_day else 0

    # Current streak & longest streak (within the selected range — the
    # card is labeled by range, so an unbounded streak would contradict
    # the range-limited numbers next to it).
    active_dates = {d for d in by_day}

    today = datetime.now(timezone.utc).date()
    current_streak = 0
    check_day = today
    while check_day.strftime("%Y-%m-%d") in active_dates:
        current_streak += 1
        check_day -= timedelta(days=1)
    if current_streak == 0:
        # Check if yesterday was active
        check_day = today - timedelta(days=1)
        while check_day.strftime("%Y-%m-%d") in active_dates:
            current_streak += 1
            check_day -= timedelta(days=1)

    # Longest consecutive run of active days within the range — not the
    # distinct-day count (active_days = N distinct days, longest_streak =
    # the longest back-to-back run among them).
    longest_streak = 0
    run = 0
    prev_day: date | None = None
    for day_str in sorted(active_dates):
        try:
            d = datetime.strptime(day_str, '%Y-%m-%d').date()
        except ValueError:
            continue
        if prev_day is not None and (d - prev_day).days == 1:
            run += 1
        else:
            run = 1
        longest_streak = max(longest_streak, run)
        prev_day = d

    # Favorite model in this range
    by_model_tokens: dict[str, int] = {}
    for r in rows:
        model = str(r.get("model") or "")
        if not model:
            continue
        by_model_tokens[model] = by_model_tokens.get(model, 0) + _tokens(r)
    fav_model: str | None = None
    fav_tokens = 0
    for model, tokens in sorted(by_model_tokens.items(), key=lambda kv: -kv[1]):
        fav_model = model
        fav_tokens = tokens
        break

    fav_share = (fav_tokens / total_tokens * 100.0) if total_tokens > 0 else 0.0

    return {
        "range": range,
        "totalTokens": total_tokens,
        "cacheHitTokens": cache_hit,
        "cacheMissTokens": cache_miss,
        "cacheHitRate": round(cache_hit / cache_total, 3) if cache_total else 0.0,
        "peakTokens": peak_tokens,
        "sessions": session_count,
        "messages": message_count,
        "activeDays": active_days,
        "currentStreak": current_streak,
        "longestStreak": longest_streak,
        "favoriteModel": fav_model,
        "favoriteModelShare": fav_share,
        "at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/heatmap")
def get_usage_heatmap(range: str = Query("30d")) -> dict[str, Any]:  # noqa: A002 — public query name
    """Get daily token activity for the heatmap grid.

    The 52-week grid always shows a full year, so ``range`` is accepted
    for API symmetry (the frontend sends the active selector) but the
    window stays 365 days regardless.
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")

    rows = _window_rows(cutoff)
    by_day: dict[str, int] = {}
    for r in rows:
        day = str(r.get("created_at") or "")[:10]
        if day:
            by_day[day] = by_day.get(day, 0) + _tokens(r)
    results = [{"date": day, "count": tokens} for day, tokens in sorted(by_day.items())]
    return {"results": results}


@router.get("/by-model")
def get_usage_by_model(range: str = Query("30d")) -> dict[str, Any]:  # noqa: A002 — public query name
    """Get per-model token breakdown and percentage share."""
    _, cutoff_str = _parse_range_cutoff(range)
    rows = _window_rows(cutoff_str)
    grand_total = sum(_tokens(r) for r in rows)

    agg: dict[str, dict[str, int]] = {}
    for r in rows:
        model_name = str(r.get("model") or "") or "unknown"
        bucket = agg.setdefault(
            model_name, {"tokens": 0, "cache_hit": 0, "cache_miss": 0}
        )
        bucket["tokens"] += _tokens(r)
        bucket["cache_hit"] += int(r.get("cache_hit_tokens") or 0)
        bucket["cache_miss"] += int(r.get("cache_miss_tokens") or 0)

    results = []
    for model_name, bucket in sorted(agg.items(), key=lambda kv: -kv[1]["tokens"]):
        tokens = bucket["tokens"]
        pct = (tokens / grand_total * 100.0) if grand_total > 0 else 0.0
        results.append({
            "model": model_name,
            "tokens": tokens,
            "percent": pct,
            "cacheHitTokens": bucket["cache_hit"],
            "cacheMissTokens": bucket["cache_miss"],
        })

    return {"results": results}


@router.get("/by-day")
def get_usage_by_day(range: str = Query("30d")) -> dict[str, Any]:  # noqa: A002 — public query name
    """Get daily token trend grouped by day and model."""
    num_days = 7 if range == "7d" else 30
    now = datetime.now(timezone.utc).date()
    start_date = now - timedelta(days=num_days - 1)
    cutoff_str = start_date.strftime("%Y-%m-%d 00:00:00")

    rows = _window_rows(cutoff_str)

    # The parameter name shadows the builtin range() — walk the calendar
    # instead of calling it.
    days_map: dict[str, dict[str, int]] = {}
    d = start_date
    while d <= now:
        days_map[d.strftime("%Y-%m-%d")] = {}
        d += timedelta(days=1)

    for r in rows:
        day = str(r.get("created_at") or "")[:10]
        if day in days_map:
            model_name = str(r.get("model") or "") or "unknown"
            days_map[day][model_name] = days_map[day].get(model_name, 0) + _tokens(r)

    results = []
    for d_str in sorted(days_map.keys()):
        m_dict = days_map[d_str]
        total_d = sum(m_dict.values())
        models_list = [{"model": k, "tokens": v} for k, v in sorted(m_dict.items(), key=lambda x: x[1], reverse=True)]
        results.append({
            "date": d_str,
            "tokens": total_d,
            "models": models_list,
        })

    return {"results": results}
