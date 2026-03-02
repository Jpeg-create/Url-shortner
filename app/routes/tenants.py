# ============================================================
# Tenant Routes
#
# POST   /tenants/register   — create account, receive API key
# GET    /tenants/me         — account info + usage stats
# POST   /tenants/keys       — generate a new API key
# DELETE /tenants/keys/{id}  — revoke a key
# ============================================================

import secrets
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from app.database import fetch_one, fetch_all, execute, fetch_val
from app.middleware.auth import get_current_tenant, hash_key

router = APIRouter(prefix="/tenants", tags=["tenants"])


class RegisterRequest(BaseModel):
    name:  str
    email: EmailStr


class NewKeyRequest(BaseModel):
    label: str = "New key"


# ── POST /tenants/register ────────────────────────────────────────────────────

@router.post("/register", status_code=201)
async def register_tenant(body: RegisterRequest):
    """
    Creates a tenant and returns a raw API key shown ONCE.
    We store only the SHA-256 hash — same pattern as Stripe / Twilio.
    """
    existing = await fetch_one(
        "SELECT id FROM tenants WHERE email = $1", body.email
    )
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    tenant_id = await fetch_val(
        "INSERT INTO tenants (name, email) VALUES ($1, $2) RETURNING id",
        body.name,
        body.email,
    )

    raw_key    = f"sk_live_{secrets.token_hex(24)}"   # 56-char key
    key_prefix = raw_key[:10]                          # "sk_live_ab" — 10 chars, fits VARCHAR(12)

    await execute(
        """
        INSERT INTO api_keys (tenant_id, key_hash, key_prefix, label)
        VALUES ($1, $2, $3, $4)
        """,
        tenant_id,
        hash_key(raw_key),
        key_prefix,
        "Default key",
    )

    return {
        "message":   "Account created",
        "tenant_id": tenant_id,
        "name":      body.name,
        "email":     body.email,
        "plan":      "free",
        "api_key":   raw_key,
        "warning":   "Save this key — it will not be shown again.",
    }


# ── GET /tenants/me ───────────────────────────────────────────────────────────

@router.get("/me")
async def get_tenant_profile(tenant: dict = Depends(get_current_tenant)):
    plan_limits = await fetch_one(
        "SELECT * FROM plan_limits WHERE plan = $1", tenant["plan"]
    )
    url_count = await fetch_val(
        "SELECT COUNT(*) FROM urls WHERE tenant_id = $1 AND is_active = TRUE",
        tenant["id"],
    )
    total_clicks = await fetch_val(
        """
        SELECT COUNT(*)
        FROM   clicks c
        JOIN   urls   u ON u.short_code = c.short_code
        WHERE  u.tenant_id = $1
        """,
        tenant["id"],
    )
    keys = await fetch_all(
        """
        SELECT id, key_prefix, label, last_used, is_active, created_at
        FROM   api_keys
        WHERE  tenant_id = $1
        ORDER  BY created_at DESC
        """,
        tenant["id"],
    )
    return {
        "id":    tenant["id"],
        "name":  tenant["name"],
        "email": tenant["email"],
        "plan":  tenant["plan"],
        "usage": {
            "urls_created": url_count,
            "total_clicks": total_clicks,
            "urls_limit":   plan_limits["max_urls"] if plan_limits else 100,
        },
        "api_keys": [dict(k) for k in keys],
    }


# ── POST /tenants/keys ────────────────────────────────────────────────────────

@router.post("/keys", status_code=201)
async def create_api_key(
    body:   NewKeyRequest,
    tenant: dict = Depends(get_current_tenant),
):
    raw_key    = f"sk_live_{secrets.token_hex(24)}"
    key_prefix = raw_key[:10]

    await execute(
        """
        INSERT INTO api_keys (tenant_id, key_hash, key_prefix, label)
        VALUES ($1, $2, $3, $4)
        """,
        tenant["id"],
        hash_key(raw_key),
        key_prefix,
        body.label,
    )
    return {
        "api_key": raw_key,
        "prefix":  key_prefix,
        "label":   body.label,
        "warning": "Save this key — it will not be shown again.",
    }


# ── DELETE /tenants/keys/{key_id} ─────────────────────────────────────────────

@router.delete("/keys/{key_id}")
async def revoke_api_key(
    key_id: int,
    tenant: dict = Depends(get_current_tenant),
):
    row = await fetch_one(
        "SELECT id FROM api_keys WHERE id = $1 AND tenant_id = $2",
        key_id,
        tenant["id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Key not found")

    await execute(
        "UPDATE api_keys SET is_active = FALSE WHERE id = $1", key_id
    )
    return {"message": f"Key {key_id} revoked"}
