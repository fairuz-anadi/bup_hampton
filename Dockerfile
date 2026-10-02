# Stage 1: Build the React/TypeScript Operator UI
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Production Python runtime for FuelGuard
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# Install OpenMP runtime (libgomp1) for LightGBM and curl
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements-intel.txt ./
RUN pip install -r requirements.txt -r requirements-intel.txt

COPY backend/app ./app
COPY forecaster ./forecaster
COPY fixtures ./fixtures
COPY rag_data ./rag_data
COPY rl ./rl
COPY scripts ./scripts
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

RUN ln -s /app /app/backend

RUN useradd --create-home --uid 10001 fuelguard \
 && mkdir -p /data /app/forecaster/models/weights \
 && chown -R fuelguard /data /app/forecaster/models/weights
USER fuelguard

ARG DEPLOYMENT_VERSION=prod-render
ENV DEPLOYMENT_VERSION=${DEPLOYMENT_VERSION}
ENV PORT=8080

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
