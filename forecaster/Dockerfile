FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY forecaster/requirements.txt .
RUN pip install -r requirements.txt

# Copy forecaster code, shared contracts, and calibration fixtures
COPY forecaster ./forecaster
COPY backend/app/contracts.py ./app/contracts.py
COPY backend/app/__init__.py ./app/__init__.py
COPY fixtures ./fixtures

RUN useradd --create-home --uid 10001 fuelguard
USER fuelguard

EXPOSE 8090
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "forecaster.main:app", "--host", "0.0.0.0", "--port", "8090"]
