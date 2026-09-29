"""Policy Gauntlet on the OFFICIAL simulator image (blueprint section 10).

For every scenario in gauntlet/scenarios/*.yaml and every policy:
  reset + pause the simulator -> inject the scenario's events -> for each tick: ask the backend's
  decision engine for a recommendation with that policy selected, submit its legs through the
  allocation writer (same pre-checks as production), step one tick -> read /v1/metrics.

Policies:
  noop       never sends anything (counterfactual floor)
  rule-40    the simple "refill below 40%" rule from scripts/crisis_sim.py (a naive operator)
  greedy-v1  the intelligence lane's baseline
  lp-v2      the candidate (LP optimizer)

The candidate passes a scenario if, against greedy-v1: service level drops by at most 0.5 pp,
unmet demand rises by at most 2%, and allocation failures don't increase. Exit code 1 if any
scenario fails (CI gate). Results go to gauntlet/results/ and to the backend's /api/policy-runs.

    python scripts/gauntlet_official.py [--ticks 192] [--scenarios "gauntlet/scenarios/0[23]*.yaml"]

Resets the simulator many times: never run it during a demo.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("--backend", default="http://localhost:8080")
p.add_argument("--sim", default="http://localhost:8000")
p.add_argument("--key", default=os.environ.get("OPERATOR_KEY", "local-dev-key"))
p.add_argument("--scenarios", default=str(ROOT / "gauntlet" / "scenarios" / "*.yaml"))
p.add_argument("--policies", default="noop,rule-40,greedy-v1,lp-v2")
p.add_argument("--ticks", type=int, default=None, help="override each scenario's duration_ticks")
p.add_argument("--every", type=int, default=1, help="decide every N ticks")
p.add_argument("--baseline", default="greedy-v1")
p.add_argument("--candidate", default="lp-v2")
args = p.parse_args()

SL_DROP_PP, UNMET_RISE = 0.5, 0.02


def call(method, url, body=None, key=False, timeout=60):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-Operator-Key"] = args.key
    r = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                               headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "null")


api = lambda m, path, body=None: call(m, args.backend + path, body, key=True)
sim = lambda m, path, body=None: call(m, args.sim + path, body)


def to_sim_event(ev: dict) -> dict:
    """Scenario YAML -> POST /admin/events. The simulator takes id *lists* for filters."""
    params = dict(ev.get("parameters") or {})
    if "depot_id" in params:
        params["depot_ids"] = [params.pop("depot_id")]
    if "fuel" in params:
        params["fuel_types"] = [params.pop("fuel")]
    params.pop("max_dispatch_per_tick", None)  # not a simulator parameter: CONSTRAINED is a label only
    return {"type": ev["type"], "start_tick": int(ev.get("start_tick", 0)),
            "duration_ticks": int(ev.get("duration_ticks", 1)), "parameters": params}


ROUTE_FOR = {"station-mirpur": ("depot-gazipur", "route-gazipur-mirpur"),
             "station-tongi": ("depot-gazipur", "route-gazipur-tongi"),
             "station-karnaphuli": ("depot-patiya", "route-patiya-karnaphuli"),
             "station-coxsbazar": ("depot-patiya", "route-patiya-coxsbazar")}


def rule40_legs(state: dict) -> list[dict]:
    """Refill any station fuel whose stock + in-transit is below 40% of capacity (primary route only)."""
    legs = []
    for s in state["stations"]:
        depot, route = ROUTE_FOR[s["id"]]
        for fuel, inv in s["inventory"].items():
            cap = s["capacity"][fuel]
            pos = inv + state["in_transit_totals"].get(s["id"], {}).get(fuel, 0)
            if pos < 0.4 * cap:
                legs.append({"route_id": route, "source_depot_id": depot, "station_id": s["id"], "fuel_type": fuel,
                             "quantity": round(min(cap * 0.9 - pos, 6000))})
    return legs


def run(scenario: dict, policy: str) -> dict:
    ticks = args.ticks or int(scenario.get("duration_ticks", 96))
    api("POST", "/api/pacer", {"enabled": False})
    api("POST", "/api/chaos/faults/clear")
    code, _ = api("POST", "/api/chaos/sim/reset")
    assert code == 200, f"reset failed: {code}"
    api("POST", "/api/chaos/sim/pause")
    for ev in scenario.get("events") or []:
        code, body = sim("POST", "/admin/events", to_sim_event(ev))
        assert code == 201, f"event rejected: {code} {body}"
    results = {"accepted": 0, "skipped": 0, "rejected": 0, "held": 0}
    codes: dict[str, int] = {}
    latencies, fallbacks, engine_errors = [], 0, 0
    run_id = f"g{int(time.time())}{policy.replace('-', '')}"
    for t in range(ticks):
        if policy != "noop" and t % args.every == 0:
            legs = []
            if policy == "rule-40":
                legs = rule40_legs(api("GET", "/api/state")[1])
            else:
                started = time.perf_counter()
                code, rec = api("POST", f"/api/recommendations?policy={policy}")
                latencies.append((time.perf_counter() - started) * 1000)
                if code != 200:
                    engine_errors += 1
                else:
                    fallbacks += bool(rec.get("fallback_used"))
                    chosen = next(c for c in rec["candidates"] if c["id"] == rec["selected_candidate_id"])
                    legs = chosen["legs"]
            if legs:
                code, resp = api("POST", "/api/allocations", {"decision_id": f"{run_id}-{t}", "legs": [
                    {k: leg[k] for k in ("route_id", "source_depot_id", "station_id", "fuel_type", "quantity")}
                    for leg in legs]})
                for s in resp.get("submissions", []) if code == 200 else []:
                    results[s["result"]] += 1
                    if s["error_code"]:
                        codes[s["error_code"]] = codes.get(s["error_code"], 0) + 1
        api("POST", "/api/chaos/sim/step")  # steps and refreshes the backend snapshot before the next decision
    m = sim("GET", "/v1/metrics")[1]
    latencies.sort()
    return {"policy": policy, "scenario_id": scenario.get("id"), "ticks": ticks,
            "service_level": round(m["service_level"], 5), "unmet_liters": round(m["unmet_demand_liters"], 1),
            "served_liters": round(m["served_demand_liters"], 1), "allocation_failures": m["allocation_failures"],
            "legs": results, "blocked_codes": codes, "fallback_runs": fallbacks, "engine_errors": engine_errors,
            "decision_ms_p50": round(latencies[len(latencies) // 2], 1) if latencies else None,
            "decision_ms_p95": round(latencies[int(len(latencies) * 0.95)], 1) if latencies else None}


def verdict(base: dict, cand: dict) -> tuple[bool, list[str]]:
    reasons = []
    sl_delta_pp = (cand["service_level"] - base["service_level"]) * 100
    unmet_rise = (cand["unmet_liters"] - base["unmet_liters"]) / max(base["unmet_liters"], 1.0)
    if sl_delta_pp < -SL_DROP_PP:
        reasons.append(f"service level {sl_delta_pp:+.2f} pp vs {args.baseline}")
    if unmet_rise > UNMET_RISE and cand["unmet_liters"] - base["unmet_liters"] > 50:
        reasons.append(f"unmet demand {unmet_rise * 100:+.1f}% vs {args.baseline}")
    if cand["allocation_failures"] > base["allocation_failures"]:
        reasons.append(f"allocation failures {cand['allocation_failures']} > {base['allocation_failures']}")
    if cand["engine_errors"]:
        # A decision engine that crashes for part of the run can't pass, even if the baseline (which
        # runs through the same engine) crashed too.
        reasons.append(f"decision engine failed on {cand['engine_errors']} ticks")
    return not reasons, reasons


def main() -> int:
    code, _ = api("GET", "/api/health")
    if code != 200:
        print("backend not reachable or wrong key", code)
        return 2
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                         cwd=ROOT, check=False).stdout.strip()
    policies = args.policies.split(",")
    report, failed = [], []
    try:
        for path in sorted(glob.glob(args.scenarios)):
            scenario = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
            runs = {}
            for policy in policies:
                started = time.time()
                runs[policy] = run(scenario, policy)
                r = runs[policy]
                print(f"{scenario['id']:<22} {policy:<10} SL={r['service_level'] * 100:6.2f}%  "
                      f"unmet={r['unmet_liters']:>9,.0f} L  fail={r['allocation_failures']}  legs={r['legs']}  "
                      f"{time.time() - started:.0f}s", flush=True)
                api("POST", "/api/policy-runs", {**r, "git_sha": sha, "scenario_name": scenario.get("name")})
            ok, reasons = (True, [])
            if args.baseline in runs and args.candidate in runs:
                ok, reasons = verdict(runs[args.baseline], runs[args.candidate])
                if not ok:
                    failed.append(scenario["id"])
            report.append({"scenario_id": scenario["id"], "name": scenario.get("name"), "passed": ok,
                           "reasons": reasons, "runs": runs})
    finally:
        api("POST", "/api/chaos/sim/reset")
        api("POST", "/api/chaos/sim/pause")

    out = ROOT / "gauntlet" / "results"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    doc = {"git_sha": sha, "created": datetime.now(UTC).isoformat(), "ticks_override": args.ticks,
           "baseline": args.baseline, "candidate": args.candidate, "scenarios": report}
    (out / f"{stamp}-official.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    lines = [f"# Policy Gauntlet on the official simulator ({sha})", "",
             (f"Candidate `{args.candidate}` vs baseline `{args.baseline}`. "
              f"Pass = service level drop ≤ {SL_DROP_PP} pp, unmet rise ≤ {UNMET_RISE * 100:.0f}%, "
              "no extra allocation failures, no decision-engine errors."), "",
             "| Scenario | " + " | ".join(f"{p} SL / unmet L" for p in policies) + " | Result |",
             "|---|" + "---|" * len(policies) + "---|"]
    for s in report:
        cells = [f"{s['runs'][p]['service_level'] * 100:.2f}% / {s['runs'][p]['unmet_liters']:,.0f}" for p in policies]
        result = "✅ pass" if s["passed"] else "❌ " + "; ".join(s["reasons"])
        naive = s["runs"].get("rule-40")
        cand = s["runs"].get(args.candidate)
        if naive and cand and cand["service_level"] < naive["service_level"] - SL_DROP_PP / 100:
            result += f" ⚠ worse than the naive rule-40 by {(naive['service_level'] - cand['service_level']) * 100:.1f} pp"
        lines.append(f"| {s['name']} | " + " | ".join(cells) + f" | {result} |")
    lines += ["", "Legs sent (accepted) per run: " + "; ".join(
        f"{s['scenario_id']}: " + ", ".join(f"{p} {s['runs'][p]['legs']['accepted']}" for p in policies)
        for s in report)]
    (out / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nsaved {out / f'{stamp}-official.json'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
