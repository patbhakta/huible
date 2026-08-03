# ── Stage 1: Build ────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

COPY src/ src/
RUN python -c "import compileall; compileall.compile_dir('src', quiet=1)"

# ── Stage 2: Runtime ───────────────────────────────────────────────
FROM python:3.12-alpine AS runtime

RUN apk add --no-cache \
    libstdc++ \
    tzdata \
    ca-certificates \
    curl

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --from=builder /build/src /app/src
COPY migrations/ /app/migrations/
COPY alembic.ini /app/alembic.ini

RUN addgroup -S huible && adduser -S -G huible -H huible
USER huible

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8000/api/v1/health || exit 1

CMD ["python", "-m", "huible"]
