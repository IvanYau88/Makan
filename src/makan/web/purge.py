"""Delete expired group sessions while the server runs.

Expiry is enforced on every read, but a row past `expires_at` still holds what people shared
(names, allergies, diets, refusals) until something deletes it. `purge_forever` is that something:
the app runs it from its lifespan, so a deployment needs no cron job or extra process.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi.concurrency import run_in_threadpool

from makan.sessions import GroupSessions

log = logging.getLogger("makan.web")


async def purge_forever(sessions: GroupSessions, interval_s: float) -> None:
    """Purge once now, then every `interval_s` seconds, until cancelled.

    A failed purge is logged and tried again on the next round, so a database that is briefly down
    never stops the loop. Several server processes may each run this, which is safe because the
    delete only touches rows that have already expired.
    """
    while True:
        try:
            removed = await run_in_threadpool(sessions.purge_expired)
        except Exception:
            log.exception("purging expired group sessions failed")
        else:
            if removed:
                log.info("purged %d expired group sessions", removed)
        await asyncio.sleep(interval_s)
