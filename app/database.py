# ============================================================
# Database Connection (Python / FastAPI)
#
# Uses asyncpg — the fastest async PostgreSQL driver for Python.
# We create a connection POOL once at startup, then reuse
# connections from it. Opening a new DB connection for every
# request would be extremely slow.
#
# Neon gives you a DATABASE_URL. asyncpg accepts it directly.
# ============================================================

import asyncpg
import os
from typing import Optional

_pool: Optional[asyncpg.Pool] = None

# Detect production environment — Render sets RENDER=true automatically
is_production = os.getenv("RENDER") == "true" or os.getenv("ENV") == "production"


async def create_pool():
    global _pool
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    # asyncpg needs postgresql:// not postgres:// (Neon uses postgres://)
    database_url = database_url.replace("postgres://", "postgresql://", 1)

    # Neon requires SSL in production
    ssl = "require" if is_production else None

    _pool = await asyncpg.create_pool(
        dsn=database_url,
        ssl=ssl,
        min_size=2,
        max_size=10,  # Neon free tier has connection limits — keep this reasonable
    )
    print("✅ Database pool created")


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        print("🔌 Database pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Was create_pool() called?")
    return _pool


async def fetch_one(sql: str, *args):
    """Fetch a single row, or None if not found."""
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(sql, *args)


async def fetch_all(sql: str, *args):
    """Fetch all matching rows."""
    async with get_pool().acquire() as conn:
        return await conn.fetch(sql, *args)


async def execute(sql: str, *args):
    """Run an INSERT, UPDATE, or DELETE."""
    async with get_pool().acquire() as conn:
        return await conn.execute(sql, *args)


async def fetch_val(sql: str, *args):
    """Fetch a single value (e.g. for COUNT queries or returning an ID)."""
    async with get_pool().acquire() as conn:
        return await conn.fetchval(sql, *args)
