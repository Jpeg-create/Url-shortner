# ============================================================
# Redis Client (Caching + Rate Limiting)
#
# Two jobs:
# 1. Cache hot URLs so we don't hit the DB on every redirect
# 2. Rate limit the /shorten endpoint per tenant per minute
#
# Upstash gives you a REDIS_URL. It uses rediss:// (with SSL).
# ============================================================

import redis.asyncio as aioredis
import os
import json
from typing import Optional

_redis: Optional[aioredis.Redis] = None


async def create_redis():
    global _redis
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL environment variable is not set")

    # Upstash uses rediss:// (SSL). redis.asyncio handles this automatically.
    _redis = aioredis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    # Test the connection
    await _redis.ping()
    print("✅ Redis connected")


async def close_redis():
    global _redis
    if _redis:
        await _redis.close()
        print("🔌 Redis connection closed")


def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis not initialized. Was create_redis() called?")
    return _redis


# ============================================================
# URL Cache helpers
# ============================================================

CACHE_TTL = 60 * 60 * 24  # 24 hours in seconds


async def cache_url(short_code: str, original_url: str):
    """Store a URL in cache after it's created or first fetched."""
    await get_redis().setex(f"url:{short_code}", CACHE_TTL, original_url)


async def get_cached_url(short_code: str) -> Optional[str]:
    """Check cache before hitting the database."""
    return await get_redis().get(f"url:{short_code}")


async def invalidate_url(short_code: str):
    """Remove a URL from cache (e.g. when it's deactivated)."""
    await get_redis().delete(f"url:{short_code}")


# ============================================================
# Rate Limiting (sliding window counter)
#
# HOW IT WORKS:
# Each tenant gets a key like rate:{tenant_id}
# We increment it on every request. The key has a TTL of 60 seconds.
# If the count exceeds the limit, we reject the request.
#
# This is a "fixed window" approach — simple and good enough.
# A "sliding window" is more accurate but more complex to implement.
# ============================================================

async def check_rate_limit(tenant_id: int, max_requests: int = 10) -> bool:
    """
    Returns True if the request is allowed, False if rate limit exceeded.
    max_requests is per 60-second window.
    """
    key = f"rate:{tenant_id}"
    r = get_redis()

    # Atomically increment and get the new count
    count = await r.incr(key)

    if count == 1:
        # First request in this window — set the 60-second expiry
        await r.expire(key, 60)

    return count <= max_requests


async def get_remaining_requests(tenant_id: int, max_requests: int = 10) -> dict:
    """Return rate limit info for response headers."""
    key = f"rate:{tenant_id}"
    r = get_redis()

    count = int(await r.get(key) or 0)
    ttl = await r.ttl(key)

    return {
        "limit": max_requests,
        "remaining": max(0, max_requests - count),
        "reset_in_seconds": ttl if ttl > 0 else 60,
    }
