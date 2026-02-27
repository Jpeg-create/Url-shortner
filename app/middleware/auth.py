# ============================================================
# Auth Middleware
#
# Every protected endpoint calls get_current_tenant() as a
# FastAPI dependency. It:
# 1. Reads the Authorization: Bearer <key> header
# 2. Hashes the key with SHA-256
# 3. Looks up the hash in the database
# 4. Returns the tenant record if valid, raises 401 if not
#
# WHY DO WE HASH THE KEY?
# We never store the raw API key in the database — only its
# SHA-256 hash. If someone dumps your database, they get hashes
# they can't reverse. Same reason passwords are hashed.
# The raw key is shown to the user ONCE at creation, then gone.
# ============================================================

import hashlib
import os
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.database import fetch_one

security = HTTPBearer()


def hash_key(raw_key: str) -> str:
    """SHA-256 hash of the API key. This is what we store in the DB."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def get_current_tenant(
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    """
    FastAPI dependency. Use it like:
        tenant = Depends(get_current_tenant)

    Returns the tenant record from the DB, or raises 401.
    """
    raw_key = credentials.credentials
    key_hash = hash_key(raw_key)

    row = await fetch_one(
        """
        SELECT t.id, t.name, t.email, t.plan, t.is_active,
               ak.id AS key_id, ak.is_active AS key_is_active
        FROM api_keys ak
        JOIN tenants t ON t.id = ak.tenant_id
        WHERE ak.key_hash = $1
        """,
        key_hash,
    )

    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="Your account has been suspended")

    if not row["key_is_active"]:
        raise HTTPException(status_code=403, detail="This API key has been revoked")

    # Update last_used timestamp (fire and forget — don't await for performance)
    import asyncio
    from app.database import execute
    asyncio.create_task(
        execute("UPDATE api_keys SET last_used = NOW() WHERE id = $1", row["key_id"])
    )

    return dict(row)
