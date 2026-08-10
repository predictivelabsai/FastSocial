FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY fastsocial ./fastsocial
COPY migrations ./migrations
COPY alembic.ini ./
COPY static ./static
RUN pip install --no-cache-dir .

EXPOSE 5062

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl --fail http://127.0.0.1:5062/healthz || exit 1

CMD ["python", "-m", "fastsocial.entrypoint"]
