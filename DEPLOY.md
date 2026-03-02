# Deployment Guide
## Neon (Postgres) + Upstash (Redis) + Render (App)
### Free forever. No credit card required.

---

## What you are deploying

```
Your App (Render)
  ├── POST /shorten      → writes to Neon, caches in Upstash
  ├── GET  /{code}       → reads from Upstash (fast) or Neon (fallback)
  ├── GET  /urls/list    → reads from Neon
  ├── GET  /urls/{code}/stats → reads from Neon
  ├── POST /tenants/register  → creates account + API key
  └── GET  /health       → liveness check (Render uses this)
```

---

## Step 1 — Neon (PostgreSQL)

1. Go to **neon.tech** → sign up free (GitHub login works)
2. Click **New Project** → name it `url-shortener` → pick a region
3. On the project page, click **Connection Details**
4. Set the connection string format to **"Connection string"** and copy it.
   It looks like:
   ```
   postgresql://alex:password@ep-cool-name-123.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
   **Save this — it is your `DATABASE_URL`.**

5. Click the **SQL Editor** tab in Neon's sidebar
6. Paste the entire contents of `schema_full.sql` into the editor
7. Click **Run**
8. You should see: `CREATE TABLE`, `CREATE INDEX`, `INSERT` — no errors

---

## Step 2 — Upstash (Redis)

1. Go to **upstash.com** → sign up free
2. Click **Create Database**
3. Name it `url-shortener-cache` → pick the **same region as Neon**
4. Click **Create**
5. On the database page, click **"redis-cli"** under Connect to see the URL.
   It looks like:
   ```
   rediss://default:AXxxxx@us1-abc-123.upstash.io:6379
   ```
   Note `rediss://` with two s's — that's SSL, which is correct.
   **Save this — it is your `REDIS_URL`.**

---

## Step 3 — Push to GitHub

In your terminal, from the project folder:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
```

Create a new **empty** repo at github.com (no README, no .gitignore).
Then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/url-shortener.git
git push -u origin main
```

---

## Step 4 — Deploy on Render

1. Go to **render.com** → sign up free (GitHub login works)
2. Click **New +** → **Web Service**
3. Click **Connect account** → authorize GitHub → select your `url-shortener` repo
4. Render detects the `Dockerfile` automatically. Fill in:
   - **Name**: `url-shortener` (this becomes your subdomain)
   - **Region**: same region as Neon and Upstash
   - **Runtime**: Docker ← make sure this is selected
   - **Plan**: Free

5. Scroll to **Environment Variables** and add these four:

   | Key            | Value                                          |
   |----------------|------------------------------------------------|
   | `DATABASE_URL` | (paste from Neon — the full connection string) |
   | `REDIS_URL`    | (paste from Upstash — starts with rediss://)   |
   | `ENV`          | `production`                                   |
   | `BASE_URL`     | `https://url-shortener.onrender.com`           |

   > For `BASE_URL`: use the URL shown in Render's service settings.
   > It will be `https://<your-service-name>.onrender.com`.
   > You can update it after the first deploy if needed.

6. Click **Create Web Service**

Render builds your Docker image and deploys. Takes 3–5 minutes.
Watch the **Logs** tab — you should see:
```
✅ Database pool created
✅ Redis connected
✅ All connections ready
INFO: Application startup complete.
```

---

## Step 5 — Verify it works

Replace `YOUR-APP.onrender.com` with your actual Render URL.

### Health check
```bash
curl https://YOUR-APP.onrender.com/health
# → {"status":"ok","service":"url-shortener"}
```

### Register an account (get your API key)
```bash
curl -s -X POST https://YOUR-APP.onrender.com/tenants/register \
  -H "Content-Type: application/json" \
  -d '{"name": "My App", "email": "you@example.com"}' | python3 -m json.tool
```
Response:
```json
{
  "message": "Account created",
  "tenant_id": 1,
  "api_key": "sk_live_abc123...",
  "warning": "Save this key — it will not be shown again."
}
```
**Copy `api_key` and save it now.** You cannot retrieve it again.

### Shorten a URL
```bash
curl -s -X POST https://YOUR-APP.onrender.com/shorten \
  -H "Authorization: Bearer sk_live_abc123..." \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.google.com/search?q=system+design"}' | python3 -m json.tool
```
Response:
```json
{
  "short_code": "1",
  "short_url": "https://YOUR-APP.onrender.com/1",
  "original_url": "https://www.google.com/search?q=system+design"
}
```

### Test the redirect
```bash
curl -L https://YOUR-APP.onrender.com/1
# → follows redirect to Google
```
Or paste the short URL in your browser — you'll be redirected.

### View analytics
```bash
curl -s https://YOUR-APP.onrender.com/urls/1/stats \
  -H "Authorization: Bearer sk_live_abc123..." | python3 -m json.tool
```

### List all your URLs
```bash
curl -s https://YOUR-APP.onrender.com/urls/list \
  -H "Authorization: Bearer sk_live_abc123..." | python3 -m json.tool
```

### Interactive API docs (no curl needed)
Open in browser: `https://YOUR-APP.onrender.com/docs`
FastAPI auto-generates a full Swagger UI. You can test every endpoint there.

---

## Calling it from another app

Once deployed, any app calls it over HTTP — no shared code needed.

**Python app:**
```python
import httpx

def shorten(long_url: str) -> str:
    response = httpx.post(
        "https://YOUR-APP.onrender.com/shorten",
        headers={"Authorization": "Bearer sk_live_abc123..."},
        json={"url": long_url},
    )
    return response.json()["short_url"]

short = shorten("https://very-long-url.com/path?with=params")
```

**Node.js / JavaScript app:**
```javascript
async function shorten(longUrl) {
  const res = await fetch("https://YOUR-APP.onrender.com/shorten", {
    method: "POST",
    headers: {
      "Authorization": "Bearer sk_live_abc123...",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ url: longUrl }),
  });
  const data = await res.json();
  return data.short_url;
}
```

---

## Free tier limitations

| Service   | Limitation                                               | Impact                          |
|-----------|----------------------------------------------------------|---------------------------------|
| **Render**  | App sleeps after 15 min idle; ~45s cold-start wake       | First click after idle is slow  |
| **Neon**    | 0.5 GB storage; ~100 concurrent connections              | Holds ~500K URLs comfortably    |
| **Upstash** | 500K Redis commands/month (≈250K cached redirects/month) | After limit, falls back to DB   |

**To remove the cold-start:** upgrade Render to the $7/month Starter plan.
Everything else scales cheaply as pay-as-you-go.

---

## Redeploy after code changes

```bash
git add .
git commit -m "your change"
git push
```
Render auto-deploys on every push to `main`. No other steps needed.
