# ============================================
# Webhook Handler - Application Image
# ============================================
# One image, three services:
#   - api    (uvicorn app.main:app)
#   - worker (celery worker)
#   - beat   (celery beat)
# The command differs per service in docker-compose.yml.
# ============================================

# -------- Base image --------
FROM python:3.12-slim

# -------- Environment --------
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# -------- System dependencies --------
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# -------- Working directory --------
WORKDIR /app

# -------- Dependencies (layer caching) --------
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# -------- Application code --------
COPY app/ ./app/
COPY create_tables.py .

# Provide a default .env inside the image.
COPY .env.example .env

# -------- Non-root user --------
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
RUN mkdir -p /tmp/prometheus_multiproc \
    && chown -R appuser:appuser /tmp/prometheus_multiproc
USER appuser

# -------- Port (documentation only) --------
EXPOSE 8000

# -------- Default command --------
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
