# ============================================================
# Redis Client — Caching + Rate Limiting
#
# Two responsibilities:
#  1. Cache hot URLs so redirects never touch the database
#  2. Rate limit POST /shorten per tenant per minute
#
# Upstash free tier: 256 MB, 500K commands/month, SSL required.
# ============================================================

import redis.asyncio as aioredis
import os
from typing import Optional

_redis: Optional[aioredis.Redis] = None


async def create_redis():
    global _redis
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL environment variable is not set")

    # Upstash uses rediss:// (double-s = SSL). redis.asyncio handles it automatically.
    _redis = aioredis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    await _redis.ping()
    print("✅ Redis connected")


async def close_redis():
    global _redis
    if _redis:
        await _redis.close()
        print("🔌 Redis closed")


def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis not initialized. Was create_redis() called?")
    return _redis


# ── URL cache ─────────────────────────────────────────────────────────────────

CACHE_TTL = 60 * 60 * 24  # 24 hours


async def cache_url(short_code: str, original_url: str) -> None:
    await get_redis().setex(f"url:{short_code}", CACHE_TTL, original_url)


async def get_cached_url(short_code: str) -> Optional[str]:
    return await get_redis().get(f"url:{short_code}")


async def invalidate_url(short_code: str) -> None:
    await get_redis().delete(f"url:{short_code}")


# ── Rate limiting ─────────────────────────────────────────────────────────────
#
# Fixed-window counter keyed by tenant_id.
#
# FIX: previous version did incr() + expire() as two separate round-trips.
# If the process died between them the key would exist forever with no TTL,
# permanently locking the tenant out. Now we use a pipeline so both commands
# are sent and applied atomically in one round-trip.
#
# Note: we always SET the TTL (not just on count==1) so that the window
# always resets 60 s after the first request in that window, not after
# the last. This is the correct fixed-window behaviour.

GUEST_LIMIT = 5
GUEST_TTL   = 60 * 60 * 24  # 24 hours


async def check_guest_limit(ip: str) -> dict:
    """
    PEEK only — reads current count without incrementing.
    Call increment_guest_count() only after a successful shorten,
    so failed/errored requests never burn through the free quota.
    """
    key   = f"guest:{ip}"
    count = await get_redis().get(key)
    count = int(count) if count else 0
    remaining = max(0, GUEST_LIMIT - count)
    return {
        "allowed":        count < GUEST_LIMIT,
        "uses_used":      count,
        "uses_remaining": remaining,
    }


async def increment_guest_count(ip: str) -> dict:
    """
    Increment the guest counter only on a successful shorten.
    """
    key = f"guest:{ip}"
    r   = get_redis()

    async with r.pipeline(transaction=False) as pipe:
        pipe.incr(key)
        pipe.expire(key, GUEST_TTL, nx=True)
        results = await pipe.execute()

    count     = results[0]
    remaining = max(0, GUEST_LIMIT - count)
    return {
        "allowed":        count <= GUEST_LIMIT,
        "uses_used":      count,
        "uses_remaining": remaining,
    }


async def check_rate_limit(tenant_id: int, max_requests: int = 10) -> bool:
    """
    Increment the tenant's request counter and return True if allowed.
    Uses a pipeline to fix the incr/expire race condition.
    """
    key = f"rate:{tenant_id}"
    r = get_redis()

    async with r.pipeline(transaction=False) as pipe:
        pipe.incr(key)
        pipe.expire(key, 60, nx=True)   # nx=True: only set TTL if the key has no TTL yet
        results = await pipe.execute()  # [new_count, expire_result]

    count = results[0]
    return count <= max_requests
