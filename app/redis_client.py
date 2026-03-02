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


# ── Guest rate limiting ───────────────────────────────────────────────────────
#
# PREVIOUS BUG: keyed by IP address.
# Problem: incognito windows share the same IP — fresh browser session still
# hit the same counter. Worse: users behind shared IPs (offices, mobile NAT)
# would all share one 5-use bucket, effectively locking everyone out after
# the first person used their quota.
#
# FIX: keyed by a UUID the frontend generates and stores in localStorage.
# - Incognito = empty localStorage = new UUID = fresh 5 uses ✓
# - Different devices = different UUIDs ✓
# - Abuse-resistant enough for a portfolio project ✓
#
# The frontend sends the token as the X-Guest-Token header.
# The backend uses it as the Redis key (sanitised to prevent injection).

GUEST_LIMIT = 5
GUEST_TTL   = 60 * 60 * 24  # 24 hours — resets after 24h of first use


def _guest_key(token: str) -> str:
    """
    Sanitise the token and build the Redis key.
    We only allow alphanumeric + hyphens (UUID format) — nothing else.
    Max 64 chars to prevent oversized keys.
    """
    safe = "".join(c for c in token if c.isalnum() or c == "-")[:64]
    if not safe:
        safe = "anonymous"
    return f"guest_token:{safe}"


async def check_guest_limit(token: str) -> dict:
    """
    PEEK only — reads current count without incrementing.
    Call increment_guest_count() only after a confirmed successful shorten.
    """
    key   = _guest_key(token)
    count = await get_redis().get(key)
    count = int(count) if count else 0
    remaining = max(0, GUEST_LIMIT - count)
    return {
        "allowed":        count < GUEST_LIMIT,
        "uses_used":      count,
        "uses_remaining": remaining,
    }


async def increment_guest_count(token: str) -> dict:
    """
    Increment the guest counter only on a successful shorten.
    Uses pipeline so incr + expire are sent atomically.
    """
    key = _guest_key(token)
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


# ── Tenant rate limiting ──────────────────────────────────────────────────────

async def check_rate_limit(tenant_id: int, max_requests: int = 10) -> bool:
    """
    Increment the tenant's request counter and return True if allowed.
    Uses a pipeline to prevent the incr/expire race condition.
    """
    key = f"rate:{tenant_id}"
    r   = get_redis()

    async with r.pipeline(transaction=False) as pipe:
        pipe.incr(key)
        pipe.expire(key, 60, nx=True)
        results = await pipe.execute()

    count = results[0]
    return count <= max_requests
