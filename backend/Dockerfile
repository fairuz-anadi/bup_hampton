# Build context is the repo root (see docker-compose.yml), because the intelligence lane imports
# `forecaster.*` and `backend.app.*` from outside backend/.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# libgomp1: OpenMP runtime needed by LightGBM
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements-intel.txt ./
RUN pip install -r requirements.txt -r requirements-intel.txt

COPY backend/app ./app
COPY forecaster ./forecaster
COPY fixtures ./fixtures
COPY rag_data ./rag_data
# Lets `backend.app.*` imports resolve to the same code as `app.*`.
RUN ln -s /app /app/backend

RUN useradd --create-home --uid 10001 fuelguard \
 && mkdir -p /data /app/forecaster/models/weights \
 && chown -R fuelguard /data /app/forecaster/models/weights
USER fuelguard

ARG DEPLOYMENT_VERSION=dev
ENV DEPLOYMENT_VERSION=${DEPLOYMENT_VERSION}

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
