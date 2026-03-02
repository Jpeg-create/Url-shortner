# ============================================================
# Auth Middleware
#
# FastAPI dependency used by every protected endpoint.
# Validates the "Authorization: Bearer <key>" header,
# looks up the hashed key in the DB, and returns the tenant.
#
# WHY HASH THE KEY?
# We store SHA-256(key), never the key itself.
# If the database leaks, attackers get irreversible hashes.
# The raw key is shown once at creation, then discarded.
# ============================================================

import hashlib
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.database import fetch_one, execute
import asyncio

security = HTTPBearer()


def hash_key(raw_key: str) -> str:
    """SHA-256 hex digest of the raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def get_current_tenant(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> dict:
    """
    FastAPI dependency — resolves the caller's tenant from their API key.

    Usage:
        tenant: dict = Depends(get_current_tenant)
    """
    raw_key  = credentials.credentials
    key_hash = hash_key(raw_key)

    row = await fetch_one(
        """
        SELECT
            t.id, t.name, t.email, t.plan, t.is_active,
            ak.id        AS key_id,
            ak.is_active AS key_is_active
        FROM api_keys ak
        JOIN tenants  t  ON t.id = ak.tenant_id
        WHERE ak.key_hash = $1
          AND ak.expires_at IS NULL OR ak.expires_at > NOW()
        """,
        key_hash,
    )

    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="Account suspended")

    if not row["key_is_active"]:
        raise HTTPException(status_code=403, detail="API key revoked")

    # Stamp last_used in background — don't block the request for a bookkeeping write.
    asyncio.create_task(
        execute(
            "UPDATE api_keys SET last_used = NOW() WHERE id = $1",
            row["key_id"],
        )
    )

    return dict(row)
