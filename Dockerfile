# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy and install dependencies first — Docker caches this layer.
# Rebuilds only when requirements.txt changes, not on every code change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Render injects PORT at runtime; default to 8000.
ENV PORT=8000
EXPOSE 8000

# Single worker: FastAPI is async — one process handles many concurrent
# requests via the event loop. Multiple workers on Render's free 512 MB
# tier risks OOM kills.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
