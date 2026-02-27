# ============================================================
# URL Routes
#
# POST /shorten  — create a short URL (requires API key)
# GET  /:code    — redirect to original URL (public)
# GET  /urls     — list your URLs (requires API key)
# DELETE /urls/:code — deactivate a URL (requires API key)
# ============================================================

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator
from typing import Optional
import asyncio
import uuid

from app.database import fetch_one, fetch_all, execute, fetch_val
from app.base62 import encode
from app.redis_client import cache_url, get_cached_url, invalidate_url, check_rate_limit
from app.middleware.auth import get_current_tenant
from app.worker.analytics import record_click

router = APIRouter()


# ── Request / Response models ─────────────────────────────────────────────────

class ShortenRequest(BaseModel):
    url: str
    custom_code: Optional[str] = None   # e.g. "sale2025" for branded links
    expires_in_days: Optional[int] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v):
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        if len(v) > 2048:
            raise ValueError("URL is too long (max 2048 characters)")
        return v

    @field_validator("custom_code")
    @classmethod
    def validate_custom_code(cls, v):
        if v is None:
            return v
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Custom code can only contain letters, numbers, - and _")
        if len(v) < 3 or len(v) > 20:
            raise ValueError("Custom code must be 3-20 characters")
        return v


class ShortenResponse(BaseModel):
    short_code: str
    short_url: str
    original_url: str


# ── POST /shorten ─────────────────────────────────────────────────────────────

@router.post("/shorten", response_model=ShortenResponse)
async def shorten_url(
    body: ShortenRequest,
    request: Request,
    tenant: dict = Depends(get_current_tenant),
):
    """
    Create a short URL. Requires Authorization: Bearer <api_key>

    FLOW:
    1. Rate limit check (per tenant, per their plan limit)
    2. URL limit check (has this tenant hit their plan's max_urls?)
    3. Check if custom code is taken (if provided)
    4. INSERT into urls table → DB returns the auto-increment ID
    5. Base62 encode the ID → short code
    6. UPDATE the row with the short code
    7. Cache the mapping in Redis
    8. Return the short URL
    """

    # Step 1 — Rate limit
    # Get the plan's requests_per_minute limit
    plan_limit = await fetch_one(
        "SELECT requests_per_minute, max_urls FROM plan_limits WHERE plan = $1",
        tenant["plan"],
    )
    rpm = plan_limit["requests_per_minute"] if plan_limit else 10

    allowed = await check_rate_limit(tenant["id"], max_requests=rpm)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Your plan allows {rpm} requests/minute."
        )

    # Step 2 — URL count limit
    if plan_limit and plan_limit["max_urls"] != -1:
        url_count = await fetch_val(
            "SELECT COUNT(*) FROM urls WHERE tenant_id = $1 AND is_active = TRUE",
            tenant["id"],
        )
        if url_count >= plan_limit["max_urls"]:
            raise HTTPException(
                status_code=403,
                detail=f"URL limit reached ({plan_limit['max_urls']} on your plan). Upgrade to create more."
            )

    # Step 3 — Custom code collision check
    if body.custom_code:
        existing = await fetch_one(
            "SELECT id FROM urls WHERE short_code = $1", body.custom_code
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"The custom code '{body.custom_code}' is already taken."
            )

    # Step 4 — Insert the URL (with a placeholder short_code for now)
    # We need the DB-generated ID to create the code, so we insert first
    expires_at = None
    if body.expires_in_days:
        from datetime import datetime, timedelta
        expires_at = datetime.utcnow() + timedelta(days=body.expires_in_days)

    # Unique temp placeholder — avoids UNIQUE constraint collision under concurrent requests
    temp_code = f"_t_{uuid.uuid4().hex[:12]}"

    url_id = await fetch_val(
        """
        INSERT INTO urls (short_code, original_url, tenant_id, expires_at)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        temp_code,          # unique per-request placeholder to avoid UNIQUE constraint collisions
        body.original_url,
        tenant["id"],
        expires_at,
    )

    # Step 5 — Generate the short code from the DB ID
    short_code = body.custom_code if body.custom_code else encode(url_id)

    # Step 6 — Update the row with the real short code
    await execute(
        "UPDATE urls SET short_code = $1 WHERE id = $2",
        short_code,
        url_id,
    )

    # Step 7 — Cache it immediately (so the first redirect is fast)
    await cache_url(short_code, body.original_url)

    # Step 8 — Return the response
    base_url = request.base_url
    return ShortenResponse(
        short_code=short_code,
        short_url=f"{base_url}{short_code}",
        original_url=body.original_url,
    )


# ── GET /urls (list your URLs) ────────────────────────────────────────────────

@router.get("/urls/list")
async def list_urls(
    tenant: dict = Depends(get_current_tenant),
    limit: int = 20,
    offset: int = 0,
):
    """List all URLs for this tenant, newest first."""
    rows = await fetch_all(
        """
        SELECT
            short_code,
            original_url,
            created_at,
            expires_at,
            is_active,
            (SELECT COUNT(*) FROM clicks WHERE clicks.short_code = urls.short_code) AS click_count
        FROM urls
        WHERE tenant_id = $1
        ORDER BY created_at DESC
        LIMIT $2 OFFSET $3
        """,
        tenant["id"],
        limit,
        offset,
    )
    return {"urls": [dict(r) for r in rows], "limit": limit, "offset": offset}


# ── GET /urls/:code/stats ─────────────────────────────────────────────────────

@router.get("/urls/{short_code}/stats")
async def url_stats(
    short_code: str,
    tenant: dict = Depends(get_current_tenant),
):
    """Analytics for a specific short URL."""
    # Verify ownership
    url = await fetch_one(
        "SELECT short_code, original_url, created_at FROM urls WHERE short_code = $1 AND tenant_id = $2",
        short_code,
        tenant["id"],
    )
    if not url:
        raise HTTPException(status_code=404, detail="URL not found or not yours")

    total_clicks = await fetch_val(
        "SELECT COUNT(*) FROM clicks WHERE short_code = $1", short_code
    )

    # Clicks by day (last 30 days)
    clicks_by_day = await fetch_all(
        """
        SELECT DATE(clicked_at) AS day, COUNT(*) AS clicks
        FROM clicks
        WHERE short_code = $1
          AND clicked_at > NOW() - INTERVAL '30 days'
        GROUP BY day
        ORDER BY day DESC
        """,
        short_code,
    )

    # Top referrers
    top_referrers = await fetch_all(
        """
        SELECT COALESCE(referrer, 'Direct') AS referrer, COUNT(*) AS clicks
        FROM clicks
        WHERE short_code = $1
        GROUP BY referrer
        ORDER BY clicks DESC
        LIMIT 5
        """,
        short_code,
    )

    return {
        "short_code": short_code,
        "original_url": url["original_url"],
        "created_at": url["created_at"],
        "total_clicks": total_clicks,
        "clicks_by_day": [dict(r) for r in clicks_by_day],
        "top_referrers": [dict(r) for r in top_referrers],
    }
# ── DELETE /urls/:code ────────────────────────────────────────────────────────

@router.delete("/urls/{short_code}")
async def deactivate_url(
    short_code: str,
    tenant: dict = Depends(get_current_tenant),
):
    """Deactivate a URL (soft delete — keeps analytics data)."""
    row = await fetch_one(
        "SELECT id FROM urls WHERE short_code = $1 AND tenant_id = $2",
        short_code,
        tenant["id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="URL not found or not yours")

    await execute(
        "UPDATE urls SET is_active = FALSE WHERE short_code = $1",
        short_code,
    )
    await invalidate_url(short_code)  # Remove from cache

    return {"message": f"/{short_code} has been deactivated"}


# ── GET /:code ────────────────────────────────────────────────────────────────

@router.get("/{short_code}")
async def redirect_url(short_code: str, request: Request):
    """
    Redirect to the original URL. This is the hot path — it runs on
    EVERY click and must be as fast as possible.

    FLOW:
    1. Check Redis cache first (microseconds)
    2. Cache miss → query the database
    3. Check if URL is active / not expired
    4. Redirect the user immediately (307 Temporary Redirect)
    5. Fire off analytics recording in the background (non-blocking)
    """

    # Step 1 — Redis cache check
    original_url = await get_cached_url(short_code)

    if not original_url:
        # Step 2 — Cache miss, query the database
        row = await fetch_one(
            """
            SELECT original_url, is_active, expires_at
            FROM urls
            WHERE short_code = $1
            """,
            short_code,
        )

        if not row:
            raise HTTPException(status_code=404, detail="Short URL not found")

        if not row["is_active"]:
            raise HTTPException(status_code=410, detail="This link has been deactivated")

        # Step 3 — Check expiry
        if row["expires_at"]:
            from datetime import datetime, timezone
            if datetime.now(timezone.utc) > row["expires_at"]:
                raise HTTPException(status_code=410, detail="This link has expired")

        original_url = row["original_url"]

        # Warm the cache so the next request is fast
        await cache_url(short_code, original_url)

    # Step 4 — Redirect immediately (don't wait for analytics)
    # 307 preserves the HTTP method; use 302 for standard redirect
    response = RedirectResponse(url=original_url, status_code=307)

    # Step 5 — Record the click in the background (fire and forget)
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else None)
    asyncio.create_task(
        record_click(
            short_code=short_code,
            ip_address=ip,
            user_agent=request.headers.get("user-agent"),
            referrer=request.headers.get("referer"),
        )
    )

    return response


