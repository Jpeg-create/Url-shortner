# ============================================================
# Analytics Worker
#
# We NEVER let analytics slow down a redirect.
# The user gets sent to their destination immediately.
# Then we record the click in the background.
#
# This is called "fire and forget" — we fire off the task
# and don't wait for it to complete.
#
# Pattern:
#   Request comes in → redirect user → asyncio.create_task(record_click(...))
#                                              ↓ (runs in background)
#                                       INSERT INTO clicks ...
# ============================================================

from app.database import execute
import logging

logger = logging.getLogger(__name__)


async def record_click(
    short_code: str,
    ip_address: str | None,
    user_agent: str | None,
    referrer: str | None,
):
    """
    Insert a click record into the clicks table.
    Called as a background task — failures here don't affect the redirect.
    """
    try:
        await execute(
            """
            INSERT INTO clicks (short_code, ip_address, user_agent, referrer)
            VALUES ($1, $2::inet, $3, $4)
            """,
            short_code,
            ip_address,
            user_agent[:500] if user_agent else None,   # truncate long user agents
            referrer[:500] if referrer else None,        # truncate long referrers
        )
    except Exception as e:
        # Analytics failing must NEVER crash the main app
        logger.error(f"Failed to record click for {short_code}: {e}")
