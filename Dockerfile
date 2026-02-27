# ============================================================
# Dockerfile — Python / FastAPI
#
# Render will use this to build and run your app.
# ============================================================

FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install dependencies first (Docker caches this layer if requirements.txt unchanged)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Expose the port Render expects
EXPOSE 8000

# Start the server
# --host 0.0.0.0 makes it accessible from outside the container
# --workers 2 runs 2 parallel processes (good for free tier)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
