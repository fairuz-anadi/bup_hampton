"""Runs one k6 workload against the compose stack and records the results.

    python scripts/run_loadtest.py dashboard-read [-e VUS=200 -e HOLD=60s]

k6 runs in the grafana/k6 container on the compose network, so it hits the backend the same way the
frontend would. While it runs, `docker stats` is sampled for CPU and memory. Output:
  loadtest/results/<date>-<workload>.json   k6 summary + resource peaks
  a markdown row printed at the end, for docs/load-test.md
"""
import argparse
import json
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LT = ROOT / "loadtest"
NETWORK = os.environ.get("COMPOSE_NETWORK", "bup_hampton_default")

p = argparse.ArgumentParser()
p.add_argument("workload")
p.add_argument("-e", "--env", action="append", default=[], help="extra k6 env, e.g. VUS=100")
p.add_argument("--key", default=os.environ.get("OPERATOR_KEY", "local-dev-key"))
args = p.parse_args()

samples: dict[str, list[tuple[float, float]]] = {}
stop = threading.Event()


def parse_mem(s: str) -> float:
    num = float("".join(c for c in s if c.isdigit() or c == "."))
    unit = s.lstrip("0123456789.").strip().lower()
    return num * {"b": 1 / 2**20, "kib": 1 / 1024, "mib": 1, "gib": 1024, "kb": 1 / 1000, "mb": 1, "gb": 1000}.get(unit, 1)


def sampler():
    while not stop.is_set():
        out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], capture_output=True,
                             text=True).stdout
        for line in out.splitlines():
            try:
                s = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = s["Name"]
            if "bup_hampton" not in name:
                continue
            cpu = float(s["CPUPerc"].rstrip("%") or 0)
            mem = parse_mem(s["MemUsage"].split("/")[0].strip())
            samples.setdefault(name.replace("bup_hampton-", "").rsplit("-", 1)[0], []).append((cpu, mem))
        stop.wait(2)


results = LT / "results"
results.mkdir(exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d-%H%M")
summary_name = f"{stamp}-{args.workload}.k6.json"
cmd = ["docker", "run", "--rm", "--network", NETWORK, "-v", f"{LT.as_posix()}:/scripts",
       "-e", "BASE=http://backend:8080", "-e", "SIM=http://simulator-api:8000", "-e", f"KEY={args.key}"]
for e in args.env:
    cmd += ["-e", e]
cmd += ["grafana/k6:0.54.0", "run", "--quiet", "--summary-export", f"/scripts/results/{summary_name}",
        f"/scripts/{args.workload}.js"]

t = threading.Thread(target=sampler, daemon=True)
t.start()
started = time.time()
proc = subprocess.run(cmd, text=True, capture_output=True)
stop.set()
t.join()
print(proc.stdout[-6000:])
if proc.returncode not in (0, 99):  # 99 = thresholds crossed; still record it
    print(proc.stderr[-3000:])
    raise SystemExit(proc.returncode)

k6 = json.loads((results / summary_name).read_text())
m = k6["metrics"]
dur = m["http_req_duration"]
peaks = {name: {"cpu_peak_pct": round(max(c for c, _ in v), 1), "mem_peak_mib": round(max(x for _, x in v), 1)}
         for name, v in samples.items() if v}
record = {
    "workload": args.workload, "env": args.env, "started": datetime.fromtimestamp(started).isoformat(),
    "wall_seconds": round(time.time() - started, 1), "thresholds_passed": proc.returncode == 0,
    "requests": m["http_reqs"]["count"], "throughput_rps": round(m["http_reqs"]["rate"], 1),
    "error_rate": round(m["http_req_failed"]["value"], 4), "peak_vus": m.get("vus_max", {}).get("max"),
    "latency_ms": {k: round(dur[k], 1) for k in ("avg", "med", "p(95)", "p(99)", "max") if k in dur},
    "resources": peaks,
    "custom": {k: v for k, v in m.items() if k in ("approve_to_submitted", "legs_accepted",
                                                   "legs_blocked_by_precheck", "checks")},
}
(results / f"{stamp}-{args.workload}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
(results / summary_name).unlink()
lat = record["latency_ms"]
be = peaks.get("backend", {})
print(f"| {args.workload} | {record['peak_vus']} | {record['requests']} | {record['throughput_rps']} | "
      f"{lat.get('avg')} | {lat.get('med')} | {lat.get('p(95)')} | {lat.get('p(99)')} | "
      f"{record['error_rate'] * 100:.2f}% | {be.get('cpu_peak_pct')}% | {be.get('mem_peak_mib')} MiB |")
print("saved", results / f"{stamp}-{args.workload}.json")
