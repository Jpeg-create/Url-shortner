-- Full schema (schema.sql + schema_multitenancy.sql combined)
-- ============================================================
-- URL Shortener Schema
-- Run this once to set up your PostgreSQL database
-- ============================================================

-- Core table. Every shortened URL lives here.
CREATE TABLE urls (
    id           BIGSERIAL PRIMARY KEY,          -- auto-increment; we Base62-encode this to make the short code
    short_code   VARCHAR(10) UNIQUE NOT NULL,    -- the encoded ID e.g. "aX3kP"
    original_url TEXT NOT NULL,                  -- the full destination URL
    user_id      BIGINT,                         -- optional: if you add accounts later
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    expires_at   TIMESTAMPTZ,                    -- NULL means never expires
    is_active    BOOLEAN DEFAULT TRUE            -- lets us deactivate links without deleting
);

-- Index on short_code because EVERY redirect query looks up by this column.
-- Without this index, Postgres scans the entire table on each lookup.
CREATE INDEX idx_urls_short_code ON urls(short_code);
CREATE INDEX idx_urls_user_id ON urls(user_id);

-- ============================================================
-- Analytics table. Separate from urls intentionally.
--
-- WHY SEPARATE?
-- If we stored click_count on the urls table, every redirect
-- would UPDATE the same row. At high traffic, thousands of
-- requests fight over a row lock — this is called write contention.
-- Separate inserts into this table are much safer and faster.
-- ============================================================
CREATE TABLE clicks (
    id          BIGSERIAL PRIMARY KEY,
    short_code  VARCHAR(10) NOT NULL REFERENCES urls(short_code),
    ip_address  INET,                   -- Postgres has a native IP type
    user_agent  TEXT,                   -- browser / device info
    referrer    TEXT,                   -- where the click came from
    country     VARCHAR(2),             -- filled in by a geo-lookup service
    clicked_at  TIMESTAMPTZ DEFAULT NOW()
);

-- We query clicks heavily by short_code (e.g. "show me all clicks for /aX3kP")
-- and by time (e.g. "clicks in the last 7 days"), so index both.
CREATE INDEX idx_clicks_short_code ON clicks(short_code);
CREATE INDEX idx_clicks_clicked_at ON clicks(clicked_at);

-- ============================================================
-- Rate limiting table (used by the Node.js version).
-- The Python version uses Redis for this instead.
-- Both approaches are valid — Redis is faster but adds a dependency.
-- ============================================================
CREATE TABLE rate_limits (
    ip_address  INET PRIMARY KEY,
    request_count INT DEFAULT 1,
    window_start  TIMESTAMPTZ DEFAULT NOW()
);
-- ============================================================
-- Multi-tenant additions — run AFTER schema.sql
-- ============================================================

-- ============================================================
-- Tenants table
-- A "tenant" is any app or company using your shortener.
-- Could be your own internal apps, or paying customers.
-- ============================================================
CREATE TABLE tenants (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,          -- "Acme Corp" or "My E-commerce App"
    email       VARCHAR(255) UNIQUE NOT NULL,   -- contact / billing email
    plan        VARCHAR(50) DEFAULT 'free',     -- free | pro | enterprise
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- API keys table
-- Each tenant can have MULTIPLE keys (e.g. one per environment:
-- dev key, staging key, production key). This lets them rotate
-- keys without downtime — generate a new one, migrate, revoke old.
-- ============================================================
CREATE TABLE api_keys (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   BIGINT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    key_hash    VARCHAR(64) UNIQUE NOT NULL,    -- SHA-256 hash of the key (never store raw)
    key_prefix  VARCHAR(8) NOT NULL,            -- first 8 chars shown in UI so user can identify key
    label       VARCHAR(100),                   -- "Production key", "Mobile app key"
    last_used   TIMESTAMPTZ,                    -- when was this key last seen
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    expires_at  TIMESTAMPTZ                     -- optional expiry
);

CREATE INDEX idx_api_keys_hash      ON api_keys(key_hash);
CREATE INDEX idx_api_keys_tenant_id ON api_keys(tenant_id);

-- ============================================================
-- Add tenant_id to the urls table
-- Now every URL belongs to a tenant, so analytics and link
-- management are scoped — Tenant A can't see Tenant B's links.
-- ============================================================
ALTER TABLE urls ADD COLUMN tenant_id BIGINT REFERENCES tenants(id);
CREATE INDEX idx_urls_tenant_id ON urls(tenant_id);

-- ============================================================
-- Plan limits table
-- Defines what each plan tier is allowed to do.
-- Check these at request time to enforce limits.
-- ============================================================
CREATE TABLE plan_limits (
    plan                VARCHAR(50) PRIMARY KEY,
    max_urls            INT DEFAULT 100,        -- total URLs the tenant can create
    requests_per_minute INT DEFAULT 10,         -- rate limit for /shorten
    analytics_retention INT DEFAULT 30,         -- how many days of click data to keep
    custom_domains      BOOLEAN DEFAULT FALSE
);

INSERT INTO plan_limits VALUES ('free',       100,    10,  30,   FALSE);
INSERT INTO plan_limits VALUES ('pro',        10000,  100, 365,  TRUE);
INSERT INTO plan_limits VALUES ('enterprise', -1,     1000, -1,  TRUE);  -- -1 = unlimited
