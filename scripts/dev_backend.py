"""Run the backend locally (no Docker) with the intelligence lane importable.

    python scripts/dev_backend.py            # http://localhost:8080, simulator at SIMULATOR_URL or localhost:8000

Puts backend/ and the repo root on sys.path so both `app.*` and the intelligence lane's `backend.app.*` /
`forecaster.*` imports resolve, loads .env, and creates .env with a fresh OPERATOR_KEY if there is none yet.
Without DATABASE_URL, decision history stays in memory. Pair it with scripts/fake_simulator.py when the
official image is not available.
"""
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
env = ROOT / ".env"
if not env.exists():
    env.write_text(f"OPERATOR_KEY={secrets.token_urlsafe(24)}\n")
    print(f"created {env} with a new OPERATOR_KEY (read it from that file)")
for line in env.read_text().splitlines():
    k, _, v = line.partition("=")
    if k.strip() and not k.lstrip().startswith("#") and v.strip():
        os.environ.setdefault(k.strip(), v.split("#")[0].strip())
os.environ.setdefault("SIMULATOR_URL", "http://localhost:8000")
os.environ.setdefault("DB_BUFFER_PATH", str(ROOT / ".dev-buffer.jsonl"))
# docker-compose values in .env point at container hostnames; locally we run without them.
for k in ("DATABASE_URL", "FORECASTER_URL"):
    if "postgres:" in os.environ.get(k, "") or "forecaster:" in os.environ.get(k, ""):
        os.environ[k] = ""
os.chdir(ROOT / "backend")

import uvicorn  # noqa: E402

uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.getenv("PORT", "8080")), log_level="warning")
