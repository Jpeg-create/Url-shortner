from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.database import create_pool, close_pool
from app.redis_client import create_redis, close_redis
from app.routes.urls import router as urls_router
from app.routes.tenants import router as tenants_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Starting up...")
    await create_pool()
    await create_redis()
    print("✅ All connections ready")
    yield
    print("🛑 Shutting down...")
    await close_pool()
    await close_redis()


app = FastAPI(
    title="URL Shortener API",
    description="A production-ready URL shortening service with analytics and multi-tenancy",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        # ── Production frontend ───────────────────────────────
        "https://mikralink.vercel.app",
        # ── Local development ────────────────────────────────
        "http://localhost:3000",
        "http://localhost:8080",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    # Covers Vercel preview URLs like mikralink-git-main-xxx.vercel.app
    allow_origin_regex=r"https://mikralink.*\.vercel\.app",
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# ── Fixed routes MUST be registered before the wildcard router ────────────────
# If urls_router is included first, its /{short_code} wildcard catches
# /health and / before these handlers ever get a chance to run.

@app.get("/health", tags=["system"])
async def health_check():
    return {"status": "ok", "service": "url-shortener"}


@app.get("/", tags=["system"])
async def root():
    return {
        "service": "URL Shortener API",
        "docs": "/docs",
        "register": "POST /tenants/register",
        "shorten": "POST /shorten",
    }


# ── Routers registered AFTER the fixed routes ─────────────────────────────────
app.include_router(tenants_router)  # /tenants/... — no wildcards, safe either way
app.include_router(urls_router)     # contains /{short_code} wildcard — must be last
