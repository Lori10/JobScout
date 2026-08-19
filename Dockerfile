# syntax=docker/dockerfile:1

FROM node:20-alpine AS frontend-builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.11-slim AS runtime
WORKDIR /app

COPY pyproject.toml ./
COPY jobscout/ ./jobscout/
RUN pip install --no-cache-dir .

COPY --from=frontend-builder /build/dist ./frontend/dist
RUN test -f ./frontend/dist/index.html || \
    (echo "ERROR: frontend build missing at /app/frontend/dist -- aborting" && exit 1)

RUN useradd --create-home --uid 1000 jobscout \
    && mkdir -p /app/data \
    && chown -R jobscout:jobscout /app
USER jobscout

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=15s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/jobs',timeout=10).status==200 else 1)"

CMD ["python", "-m", "jobscout", "serve", "--host", "0.0.0.0"]
