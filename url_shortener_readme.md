# URL Shortener Service

**A scalable URL shortening web service with analytics and API access.**

Turn long URLs into short, shareable links, track usage, and manage your shortened URLs via a clean API. Built with modern full-stack technologies, this project demonstrates backend engineering, database design, API development, and deployment skills.

---

## Features

- Shorten any URL into a unique, compact link  
- Redirect short URLs to the original destination  
- API access for programmatic URL shortening  
- Tenant registration with API keys for secure access  
- View URL statistics (click counts, usage analytics)  
- Persistent storage with PostgreSQL + caching with Redis  
- Easy deployment on free cloud platforms

---

## Tech Stack

| Layer           | Technology                     |
|-----------------|--------------------------------|
| Backend         | Node.js, Express (or FastAPI if Python) |
| Database        | PostgreSQL (Neon)              |
| Cache           | Redis (Upstash)                |
| Deployment      | Render (free tier)             |
| API Docs        | FastAPI interactive docs       |
| Authentication  | API Key-based                  |

---

## Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/url-shortener.git
cd url-shortener
```

### 2. Set Up PostgreSQL (Neon)

1. Sign up at [Neon](https://neon.tech) with GitHub.  
2. Create a new project (e.g., `url-shortener`).  
3. Copy the **Connection string** (`DATABASE_URL`).  
4. Run the SQL schema to create tables:  

```sql
-- schema_full.sql
<contents of your SQL schema>
```

### 3. Set Up Redis (Upstash)

1. Sign up at [Upstash](https://upstash.com)  
2. Create a database (e.g., `url-shortener-cache`)  
3. Copy the **Redis URL** (`REDIS_URL`)  

### 4. Configure Environment Variables

Create a `.env` file in the root directory:

```env
DATABASE_URL=your_postgres_connection_string
REDIS_URL=your_redis_connection_string
ENV=production
BASE_URL=https://YOUR-APP-NAME.onrender.com
```

> Replace `YOUR-APP-NAME` with the deployed Render URL.

### 5. Deploy on Render

1. Sign up at [Render](https://render.com)  
2. Create a new **Web Service** connected to your GitHub repo  
3. Choose **Docker** as runtime (Render will auto-detect your Dockerfile)  
4. Add environment variables from `.env`  
5. Click **Create Web Service** → wait 2–4 minutes for deployment  

### 6. Test Your Deployment

**Health check:**

```bash
curl https://YOUR-APP.onrender.com/health
# → {"status": "ok", "service": "url-shortener"}
```

**Register tenant / get API key:**

```bash
curl -X POST https://YOUR-APP.onrender.com/tenants/register \
-H "Content-Type: application/json" \
-d '{"name":"My App","email":"you@email.com"}'
```

**Shorten a URL:**

```bash
curl -X POST https://YOUR-APP.onrender.com/shorten \
-H "Authorization: Bearer YOUR_API_KEY" \
-H "Content-Type: application/json" \
-d '{"url":"https://www.example.com/long/path"}'
```

**Visit short URL** in browser to test redirection.

**View stats / list URLs / account info** using similar API calls.

### 7. Use From Any App

**JavaScript Example:**

```javascript
const response = await fetch('https://YOUR-APP.onrender.com/shorten', {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer YOUR_API_KEY',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({ url: 'https://example.com/path' })
});
const { short_url } = await response.json();
console.log(short_url);
```

**Python Example:**

```python
import httpx

response = httpx.post(
    'https://YOUR-APP.onrender.com/shorten',
    headers={'Authorization': 'Bearer YOUR_API_KEY'},
    json={'url':'https://example.com/path'}
)
short_url = response.json()['short_url']
print(short_url)
```

### 8. Optional Features / Upgrades

- Always-on service: Upgrade Render to paid plan  
- Increased database storage: Upgrade Neon or Upstash  
- Add analytics dashboards, custom aliases, expiration dates  

### 9. Project Highlights for Resume

- **Backend engineering:** API design, authentication, caching  
- **Database management:** PostgreSQL schema, Redis caching  
- **Deployment & DevOps:** Docker, Render cloud, environment variables  
- **Professional documentation:** Full README, API examples

