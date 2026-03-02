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

# CORS: allow all origins.
# This API is protected by API key authentication — CORS restriction adds no
# security here. Any domain can call the API, but cannot do anything useful
# without a valid sk_live_... key. Wildcard is the correct setting.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Guest-Token"],
)


# ── Fixed routes MUST be registered before the wildcard router ────────────────
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
app.include_router(tenants_router)
app.include_router(urls_router)
