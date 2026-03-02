-- ============================================================
-- URL Shortener — Full Schema
-- Run this ONCE in Neon's SQL Editor to set up your database.
-- ============================================================

-- ── Core URL table ────────────────────────────────────────────────────────────
-- short_code is Base62-encoded from the auto-increment id.
-- VARCHAR(20): Base62 of max BIGINT = ~11 chars; temp placeholder = ~16 chars.
CREATE TABLE IF NOT EXISTS urls (
    id           BIGSERIAL PRIMARY KEY,
    short_code   VARCHAR(20) UNIQUE NOT NULL,
    original_url TEXT NOT NULL,
    tenant_id    BIGINT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    expires_at   TIMESTAMPTZ,
    is_active    BOOLEAN DEFAULT TRUE
);

-- Every redirect does a lookup by short_code — this index is critical.
CREATE INDEX IF NOT EXISTS idx_urls_short_code ON urls(short_code);

-- ── Clicks table ─────────────────────────────────────────────────────────────
-- Separate from urls intentionally: concurrent INSERTs here are faster
-- than concurrent UPDATEs to a click_count column on the urls row.
CREATE TABLE IF NOT EXISTS clicks (
    id          BIGSERIAL PRIMARY KEY,
    short_code  VARCHAR(20) NOT NULL REFERENCES urls(short_code) ON DELETE CASCADE,
    ip_address  INET,
    user_agent  TEXT,
    referrer    TEXT,
    country     VARCHAR(2),
    clicked_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_clicks_short_code ON clicks(short_code);
CREATE INDEX IF NOT EXISTS idx_clicks_clicked_at  ON clicks(clicked_at);

-- ── Tenants table ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tenants (
    id         BIGSERIAL PRIMARY KEY,
    name       VARCHAR(255) NOT NULL,
    email      VARCHAR(255) UNIQUE NOT NULL,
    plan       VARCHAR(50) DEFAULT 'free',
    is_active  BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── API keys table ────────────────────────────────────────────────────────────
-- key_hash  : SHA-256 of the raw key (never stored in plain text).
-- key_prefix: first 10 chars shown in UI so users can identify keys.
--             VARCHAR(12) to fit "sk_live_XX" (10 chars) with room to spare.
CREATE TABLE IF NOT EXISTS api_keys (
    id         BIGSERIAL PRIMARY KEY,
    tenant_id  BIGINT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    key_hash   VARCHAR(64) UNIQUE NOT NULL,
    key_prefix VARCHAR(12) NOT NULL,
    label      VARCHAR(100),
    last_used  TIMESTAMPTZ,
    is_active  BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_keys_hash      ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_tenant_id ON api_keys(tenant_id);

-- ── FK: urls.tenant_id → tenants ─────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'urls_tenant_id_fkey'
    ) THEN
        ALTER TABLE urls
            ADD CONSTRAINT urls_tenant_id_fkey
            FOREIGN KEY (tenant_id) REFERENCES tenants(id);
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_urls_tenant_id ON urls(tenant_id);

-- ── Plan limits ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS plan_limits (
    plan                VARCHAR(50) PRIMARY KEY,
    max_urls            INT DEFAULT 100,
    requests_per_minute INT DEFAULT 10,
    analytics_retention INT DEFAULT 30,
    custom_domains      BOOLEAN DEFAULT FALSE
);

INSERT INTO plan_limits VALUES ('free',       100,    10,   30,  FALSE) ON CONFLICT DO NOTHING;
INSERT INTO plan_limits VALUES ('pro',        10000,  100,  365, TRUE)  ON CONFLICT DO NOTHING;
INSERT INTO plan_limits VALUES ('enterprise', -1,     1000, -1,  TRUE)  ON CONFLICT DO NOTHING;
