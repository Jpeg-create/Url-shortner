# ============================================================
# URL Routes
#
# POST   /shorten                — create a short URL (auth required)
# GET    /urls/list              — list your URLs   (auth required)
# GET    /urls/{code}/stats      — analytics        (auth required)
# DELETE /urls/{code}            — deactivate       (auth required)
# GET    /{code}                 — redirect         (public)
#
# ORDER MATTERS: specific paths (/urls/list, /urls/{code}/...) must be
# registered BEFORE the wildcard (/{code}) or FastAPI will match the
# wildcard first and never reach the specific routes.
# ============================================================

import os
import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator

from app.database import fetch_one, fetch_all, execute, fetch_val
from app.base62 import encode
from app.redis_client import cache_url, get_cached_url, invalidate_url, check_rate_limit
from app.middleware.auth import get_current_tenant
from app.worker.analytics import record_click

router = APIRouter()

# BASE_URL is set as an env var on Render (e.g. https://myapp.onrender.com).
# We use it to build short URLs — not the internal request host — because
# request.base_url reflects the internal container address behind the proxy.
_BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")


# ── Models ────────────────────────────────────────────────────────────────────

class ShortenRequest(BaseModel):
    url:             str
    custom_code:     Optional[str] = None
    expires_in_days: Optional[int] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        if len(v) > 2048:
            raise ValueError("URL too long (max 2048 chars)")
        return v

    @field_validator("custom_code")
    @classmethod
    def validate_custom_code(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Custom code may only contain letters, numbers, - and _")
        if not (3 <= len(v) <= 20):
            raise ValueError("Custom code must be 3–20 characters")
        return v


class ShortenResponse(BaseModel):
    short_code:   str
    short_url:    str
    original_url: str


# ── POST /shorten ─────────────────────────────────────────────────────────────

@router.post("/shorten", response_model=ShortenResponse, status_code=201)
async def shorten_url(
    body:   ShortenRequest,
    tenant: dict = Depends(get_current_tenant),
):
    # ── 1. Rate limit ─────────────────────────────────────────────────────────
    plan_limit = await fetch_one(
        "SELECT requests_per_minute, max_urls FROM plan_limits WHERE plan = $1",
        tenant["plan"],
    )
    rpm = plan_limit["requests_per_minute"] if plan_limit else 10

    if not await check_rate_limit(tenant["id"], max_requests=rpm):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded — your plan allows {rpm} requests/minute.",
        )

    # ── 2. URL quota ──────────────────────────────────────────────────────────
    if plan_limit and plan_limit["max_urls"] != -1:
        count = await fetch_val(
            "SELECT COUNT(*) FROM urls WHERE tenant_id = $1 AND is_active = TRUE",
            tenant["id"],
        )
        if count >= plan_limit["max_urls"]:
            raise HTTPException(
                status_code=403,
                detail=f"URL limit reached ({plan_limit['max_urls']} on your plan).",
            )

    # ── 3. Custom code availability ───────────────────────────────────────────
    if body.custom_code:
        taken = await fetch_one(
            "SELECT id FROM urls WHERE short_code = $1", body.custom_code
        )
        if taken:
            raise HTTPException(
                status_code=409,
                detail=f"Custom code '{body.custom_code}' is already taken.",
            )

    # ── 4. Compute expiry ─────────────────────────────────────────────────────
    expires_at = None
    if body.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    # ── 5. Insert with a unique temp placeholder ──────────────────────────────
    # We need the DB-assigned id to generate the Base62 code, so we insert
    # first with a throwaway placeholder, then UPDATE with the real code.
    #
    # The placeholder uses uuid hex so concurrent requests never collide on
    # the UNIQUE constraint (the previous bug used "__temp__" for all rows).
    # "t" prefix + 15 hex chars = 16 chars — fits VARCHAR(20).
    temp_code = f"t{uuid.uuid4().hex[:15]}"

    url_id = await fetch_val(
        """
        INSERT INTO urls (short_code, original_url, tenant_id, expires_at)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        temp_code,
        body.original_url,
        tenant["id"],
        expires_at,
    )

    # ── 6. Generate real short code and update ────────────────────────────────
    short_code = body.custom_code if body.custom_code else encode(url_id)
    await execute(
        "UPDATE urls SET short_code = $1 WHERE id = $2",
        short_code,
        url_id,
    )

    # ── 7. Warm the cache ─────────────────────────────────────────────────────
    await cache_url(short_code, body.original_url)

    return ShortenResponse(
        short_code=short_code,
        short_url=f"{_BASE_URL}/{short_code}",   
        original_url=body.original_url,
    )


# ── GET /urls/list ────────────────────────────────────────────────────────────
# Must be registered BEFORE GET /{short_code} or "urls" matches as a code.

@router.get("/urls/list")
async def list_urls(
    tenant: dict = Depends(get_current_tenant),
    limit:  int  = 20,
    offset: int  = 0,
):
    rows = await fetch_all(
        """
        SELECT
            short_code,
            original_url,
            created_at,
            expires_at,
            is_active,
            (SELECT COUNT(*) FROM clicks WHERE clicks.short_code = urls.short_code) AS click_count
        FROM  urls
        WHERE tenant_id = $1
        ORDER BY created_at DESC
        LIMIT  $2
        OFFSET $3
        """,
        tenant["id"],
        limit,
        offset,
    )
    return {"urls": [dict(r) for r in rows], "limit": limit, "offset": offset}


# ── GET /urls/{short_code}/stats ──────────────────────────────────────────────
# Must be registered BEFORE GET /{short_code}.

@router.get("/urls/{short_code}/stats")
async def url_stats(
    short_code: str,
    tenant:     dict = Depends(get_current_tenant),
):
    url = await fetch_one(
        """
        SELECT short_code, original_url, created_at
        FROM   urls
        WHERE  short_code = $1 AND tenant_id = $2
        """,
        short_code,
        tenant["id"],
    )
    if not url:
        raise HTTPException(status_code=404, detail="URL not found")

    total_clicks = await fetch_val(
        "SELECT COUNT(*) FROM clicks WHERE short_code = $1", short_code
    )
    clicks_by_day = await fetch_all(
        """
        SELECT DATE(clicked_at) AS day, COUNT(*) AS clicks
        FROM   clicks
        WHERE  short_code = $1
          AND  clicked_at > NOW() - INTERVAL '30 days'
        GROUP  BY day
        ORDER  BY day DESC
        """,
        short_code,
    )
    top_referrers = await fetch_all(
        """
        SELECT COALESCE(referrer, 'Direct') AS referrer, COUNT(*) AS clicks
        FROM   clicks
        WHERE  short_code = $1
        GROUP  BY referrer
        ORDER  BY clicks DESC
        LIMIT  5
        """,
        short_code,
    )
    return {
        "short_code":   short_code,
        "original_url": url["original_url"],
        "created_at":   url["created_at"],
        "total_clicks": total_clicks,
        "clicks_by_day":   [dict(r) for r in clicks_by_day],
        "top_referrers":   [dict(r) for r in top_referrers],
    }


# ── DELETE /urls/{short_code} ─────────────────────────────────────────────────

@router.delete("/urls/{short_code}")
async def deactivate_url(
    short_code: str,
    tenant:     dict = Depends(get_current_tenant),
):
    row = await fetch_one(
        "SELECT id FROM urls WHERE short_code = $1 AND tenant_id = $2",
        short_code,
        tenant["id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="URL not found")

    await execute(
        "UPDATE urls SET is_active = FALSE WHERE short_code = $1", short_code
    )
    await invalidate_url(short_code)
    return {"message": f"/{short_code} deactivated"}


# ── GET /{short_code} — redirect ──────────────────────────────────────────────
# Wildcard route — MUST be last.

@router.get("/{short_code}")
async def redirect_url(short_code: str, request: Request):
    """
    Hot path: runs on every click. Redis-first, DB fallback.
    User is redirected immediately; analytics written in background.
    """
    # 1. Cache check
    original_url = await get_cached_url(short_code)

    if not original_url:
        # 2. DB lookup
        row = await fetch_one(
            """
            SELECT original_url, is_active, expires_at
            FROM   urls
            WHERE  short_code = $1
            """,
            short_code,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Short URL not found")
        if not row["is_active"]:
            raise HTTPException(status_code=410, detail="Link deactivated")
        if row["expires_at"] and datetime.now(timezone.utc) > row["expires_at"]:
            raise HTTPException(status_code=410, detail="Link expired")

        original_url = row["original_url"]
        await cache_url(short_code, original_url)

    # 3. Redirect — 302 is correct for a URL shortener.
    #    301 (Permanent) would be cached by browsers forever, making
    #    link deactivation ineffective for users who already visited once.
    response = RedirectResponse(url=original_url, status_code=302)

    # 4. Record click in background — never blocks the redirect
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else None
    )
    asyncio.create_task(
        record_click(
            short_code=short_code,
            ip_address=ip or None,
            user_agent=request.headers.get("user-agent"),
            referrer=request.headers.get("referer"),
        )
    )

    return response
