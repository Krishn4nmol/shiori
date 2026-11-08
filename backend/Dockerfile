# Shiori backend: the API and the ingestion/graph worker share this image.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HF_HOME=/models \
    STORAGE_DIR=/data/uploads

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv

WORKDIR /app
# Dependencies first, so code changes don't reinstall them (CPU-only PyTorch, see pyproject).
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY . .
RUN uv sync --locked --no-dev

# Run as a normal user. Only the model cache and uploads are written to, so only they change
# owner (chown -R over the whole venv would copy ~1 GB into a new image layer).
RUN useradd --create-home shiori && mkdir -p /models /data/uploads \
    && chown shiori /models /data /data/uploads
USER shiori
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
# Apply database migrations, then serve. Behind Caddy, which sets X-Forwarded-* headers.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
