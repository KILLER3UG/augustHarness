"""Wake-up brief routes — the "pick up where you left off" check.

The shell (frontend) calls these on app start and on window focus / return
from sleep; the service decides against the cooldown whether a brief is due.
The config pair lives with the other config sections in ``routers/config.py``
(``/api/config/wake-up``).
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.services import wake_up

router = APIRouter(prefix='/api/wake-up', tags=['wake-up'])


@router.get('/brief')
async def getWakeUpBrief(reason: str = Query('focus', max_length=40)):
    """Ask whether a wake-up brief is due, and get it when it is.

    Returns ``{shouldBrief: false, reason}`` when the switch is off, the
    cooldown has not elapsed, or a brief already ran this start; otherwise
    the composed brief with ``quiet`` mirroring the config so the shell can
    queue it instead of showing it.
    """
    return await wake_up.brief(reason)


@router.post('/dismiss')
async def postWakeUpDismiss():
    """Record that the user dismissed the queued (quiet-mode) brief."""
    return wake_up.dismiss()
