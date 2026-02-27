# ============================================================
# FastAPI App Entry Point
#
# This is where everything wires together:
# - Database pool created on startup
# - Redis connection created on startup
# - All routes registered
# - CORS configured (so browsers can call your API)
# ============================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os

from app.database import create_pool, close_pool
from app.redis_client import create_redis, close_redis
from app.routes.urls import router as urls_router
from app.routes.tenants import router as tenants_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs startup/shutdown code.
    `yield` separates startup (before) from shutdown (after).
    FastAPI calls this automatically.
    """
    # ── STARTUP ──────────────────────────────────────────
    print("🚀 Starting up...")
    await create_pool()   # PostgreSQL connection pool
    await create_redis()  # Redis connection
    print("✅ All connections ready")

    yield  # App is running

    # ── SHUTDOWN ─────────────────────────────────────────
    print("🛑 Shutting down...")
    await close_pool()
    await close_redis()


app = FastAPI(
    title="URL Shortener API",
    description="A production-ready URL shortening service with analytics and multi-tenancy",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────────────
# Allows browsers to call your API from any frontend domain.
# In production you'd replace ["*"] with your actual frontend URL.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # Cannot be True when allow_origins=["*"] — CORS spec forbids it
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ───────────────────────────────────────────────────────────────────
app.include_router(urls_router)
app.include_router(tenants_router)


# ── Health check ─────────────────────────────────────────────────────────────
# Render uses this to know your app started successfully.
# If this returns 200, the deployment is considered healthy.
@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "url-shortener"}


# ── Root ─────────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "service": "URL Shortener API",
        "docs": "/docs",          # FastAPI auto-generates Swagger UI at /docs
        "register": "POST /tenants/register",
        "shorten": "POST /shorten",
    }
