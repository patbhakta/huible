# ── Stage 1: Build ────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir --prefix=/install .
RUN python -c "import compileall; compileall.compile_dir('src', quiet=1)"

# ── Stage 2: Runtime ───────────────────────────────────────────────
# NOTE: must match the builder's base family (glibc). Building wheels on
# python:3.12-slim and running them on alpine (musl) breaks native extensions
# like pydantic_core / asyncpg / uvloop. Keep both stages on slim.
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --from=builder /build/src /app/src
COPY migrations/ /app/migrations/
COPY alembic.ini /app/alembic.ini

RUN addgroup --system huible && adduser --system --ingroup huible --no-create-home huible
USER huible

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8000/health || exit 1

CMD ["python", "-m", "huible.api"]
