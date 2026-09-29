"""Generates the provisioned Grafana dashboards. Edit here, then run:  python monitoring/grafana/build_dashboards.py"""
import json
from pathlib import Path

OUT = Path(__file__).parent / "dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}


class Board:
    def __init__(self, uid, title, refresh="5s"):
        self.uid, self.title, self.refresh = uid, title, refresh
        self.panels, self.y, self.x, self.row_h, self.next_id = [], 0, 0, 0, 1

    def row(self, title):
        self._newline()
        self.panels.append({"type": "row", "title": title, "id": self._id(), "collapsed": False,
                            "gridPos": {"h": 1, "w": 24, "x": 0, "y": self.y}})
        self.y += 1

    def ts(self, title, targets, w=8, h=7, unit="short", stack=False, desc=""):
        self._panel({"type": "timeseries", "title": title, "description": desc,
                     "fieldConfig": {"defaults": {"unit": unit, "custom": {
                         "lineWidth": 2, "fillOpacity": 12, "stacking": {"mode": "normal" if stack else "none"}}},
                         "overrides": []},
                     "options": {"legend": {"displayMode": "list", "placement": "bottom"},
                                 "tooltip": {"mode": "multi"}},
                     "targets": self._targets(targets)}, w, h)

    def stat(self, title, expr, w=4, h=4, unit="short", mappings=None, thresholds=None, desc=""):
        steps = thresholds or [{"color": "green", "value": None}]
        self._panel({"type": "stat", "title": title, "description": desc,
                     "fieldConfig": {"defaults": {"unit": unit, "mappings": mappings or [],
                                                  "thresholds": {"mode": "absolute", "steps": steps}},
                                     "overrides": []},
                     "options": {"colorMode": "background", "graphMode": "area", "reduceOptions": {
                         "calcs": ["lastNotNull"], "fields": "", "values": False}, "textMode": "value"},
                     "targets": self._targets([(expr, "")])}, w, h)

    def _targets(self, targets):
        return [{"datasource": DS, "expr": e, "legendFormat": leg, "refId": chr(65 + i)}
                for i, (e, leg) in enumerate(targets)]

    def _panel(self, p, w, h):
        if self.x + w > 24:
            self._newline()
        p.update({"id": self._id(), "datasource": DS, "gridPos": {"h": h, "w": w, "x": self.x, "y": self.y}})
        self.panels.append(p)
        self.x += w
        self.row_h = max(self.row_h, h)

    def _newline(self):
        if self.x:
            self.y += self.row_h
        self.x, self.row_h = 0, 0

    def _id(self):
        self.next_id += 1
        return self.next_id

    def dump(self):
        OUT.mkdir(exist_ok=True)
        board = {"uid": self.uid, "title": self.title, "tags": ["fuelguard"], "timezone": "browser",
                 "schemaVersion": 39, "version": 1, "refresh": self.refresh, "editable": True,
                 "time": {"from": "now-15m", "to": "now"}, "panels": self.panels,
                 "annotations": {"list": []}, "templating": {"list": []}}
        (OUT / f"{self.uid}.json").write_text(json.dumps(board, indent=2) + "\n", encoding="utf-8")
        print("wrote", OUT / f"{self.uid}.json")


CIRCUIT = [{"type": "value", "options": {"0": {"text": "CLOSED", "color": "green"},
                                         "1": {"text": "HALF-OPEN", "color": "orange"},
                                         "2": {"text": "OPEN", "color": "red"}}}]
YESNO = [{"type": "value", "options": {"0": {"text": "fresh", "color": "green"},
                                       "1": {"text": "STALE", "color": "red"}}}]
RED_ABOVE = lambda v: [{"color": "green", "value": None}, {"color": "red", "value": v}]

LAT = (
    'histogram_quantile({q}, sum by (le) '
    '(rate(fuelguard_http_request_duration_seconds_bucket{{route!="/metrics"}}[1m])))'
)
# "or vector(0)" so a healthy system shows 0% instead of "No data".
ERR = ('(sum(rate(fuelguard_http_requests_total{status=~"5.."}[1m])) or vector(0)) '
       '/ clamp_min(sum(rate(fuelguard_http_requests_total[1m])), 1e-9)')

# ---------------------------------------------------------------- operations
ops = Board("fuelguard-ops", "FuelGuard · Operations")
ops.row("Is the system healthy?")
ops.stat("Simulator circuit", "max(fuelguard_sim_circuit_state)", mappings=CIRCUIT,
         thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 2}])
ops.stat("Snapshot", "max(fuelguard_snapshot_stale)", mappings=YESNO, thresholds=RED_ABOVE(1))
ops.stat("Snapshot age", "max(fuelguard_snapshot_age_seconds)", unit="s", thresholds=RED_ABOVE(10))
ops.stat("p95 latency", LAT.format(q=0.95), unit="s", thresholds=RED_ABOVE(0.5))
ops.stat("Error rate", ERR, unit="percentunit", thresholds=RED_ABOVE(0.05))
ops.stat("DB buffered", "max(fuelguard_db_buffered)", thresholds=RED_ABOVE(1))
ops.row("Application")
ops.ts("Request rate by route", [('sum by (route) (rate(fuelguard_http_requests_total{route!="/metrics"}[1m]))',
                                  "{{route}}")], unit="reqps")
ops.ts("Latency p50 / p95 / p99", [(LAT.format(q=0.5), "p50"), (LAT.format(q=0.95), "p95"),
                                   (LAT.format(q=0.99), "p99")], unit="s")
ops.ts("Responses by status", [("sum by (status) (rate(fuelguard_http_requests_total[1m]))", "{{status}}")],
       unit="reqps", stack=True)
ops.row("Simulator integration")
ops.ts("Simulator calls by result", [("sum by (code) (rate(fuelguard_sim_requests_total[1m]))", "{{code}}")],
       unit="reqps", stack=True, desc="503 = injected fault; circuit_open = refused locally while the breaker is open")
ops.ts("Simulator latency p95 by endpoint", [(
    "histogram_quantile(0.95, sum by (le, endpoint) (rate(fuelguard_sim_request_duration_seconds_bucket[1m])))",
    "{{endpoint}}")], unit="s")
ops.ts("Retries, invalid responses, SSE reconnects", [
    ("sum(rate(fuelguard_sim_retries_total[1m])) * 60", "retries / min"),
    ("sum(rate(fuelguard_sim_invalid_responses_total[1m])) * 60", "invalid / min"),
    ("sum(increase(fuelguard_sse_reconnects_total[5m]))", "SSE reconnects (5m)")])
ops.row("The simulated network")
ops.stat("Tick", "max(fuelguard_sim_tick)", w=4)
ops.stat("Service level", "max(fuelguard_sim_service_level)", w=4, unit="percentunit",
         thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 0.95},
                     {"color": "green", "value": 0.99}])
ops.stat("Pacer", "max(fuelguard_pacer_running)", w=4,
         mappings=[{"type": "value", "options": {"0": {"text": "off", "color": "text"},
                                                 "1": {"text": "RUNNING", "color": "blue"}}}])
ops.ts("Service level over time", [("max(fuelguard_sim_service_level)", "service level")], w=12, unit="percentunit")
ops.row("System")
ops.ts("CPU", [('rate(process_cpu_seconds_total{job="backend"}[1m])', "backend")], unit="percentunit")
ops.ts("Memory (RSS)", [('process_resident_memory_bytes{job="backend"}', "backend")], unit="bytes")
ops.ts("Targets up", [("up", "{{job}}")])
ops.dump()

# ---------------------------------------------------------------- intelligence
intel = Board("fuelguard-intel", "FuelGuard · Intelligence & decisions")
intel.row("Decisions")
intel.ts("Decisions by stage", [("sum by (stage) (increase(fuelguard_decisions_total[5m]))", "{{stage}}")],
         w=12, stack=True)
intel.ts("Allocation legs by result", [("sum by (result) (increase(fuelguard_allocations_total[5m]))",
                                        "{{result}}")], w=12, stack=True)
intel.ts("Pre-check blocks and simulator rejections", [(
    'sum by (code) (increase(fuelguard_allocations_total{result=~"skipped|rejected"}[5m]))', "{{code}}")], w=12,
    desc="PRECHECK_* = blocked before posting, so no fuel was lost")
intel.row("Decision Twin")
intel.stat("Latest Twin error", "max(fuelguard_twin_error_liters)", w=6, unit="litre", thresholds=RED_ABOVE(1000))
intel.ts("Twin error per verified decision (p50 / p90)", [
    ("histogram_quantile(0.5, sum by (le) (rate(fuelguard_twin_error_liters_hist_bucket[30m])))", "p50"),
    ("histogram_quantile(0.9, sum by (le) (rate(fuelguard_twin_error_liters_hist_bucket[30m])))", "p90")],
    w=18, unit="litre")
intel.row("Fallbacks and forecasts")
intel.ts("Fallback activations", [("sum by (component) (increase(fuelguard_fallback_activations_total[5m]))",
                                   "{{component}}")], w=12)
intel.ts("Forecast error (from forecaster)", [("fuelguard_forecast_error", "{{station}} {{fuel}}")], w=12,
         desc="Published by the forecaster service once it is deployed")
intel.dump()

# ---------------------------------------------------------------- load test
lt = Board("fuelguard-loadtest", "FuelGuard · Load test", refresh="5s")
lt.row("During a k6 run")
lt.ts("Throughput", [('sum(rate(fuelguard_http_requests_total{route!="/metrics"}[30s]))', "req/s")], w=12,
      unit="reqps")
lt.ts("Latency", [(LAT.format(q=0.5).replace("[1m]", "[30s]"), "p50"),
                  (LAT.format(q=0.95).replace("[1m]", "[30s]"), "p95"),
                  (LAT.format(q=0.99).replace("[1m]", "[30s]"), "p99")], w=12, unit="s")
lt.ts("Error rate", [(ERR.replace("[1m]", "[30s]"), "5xx share")], w=8, unit="percentunit")
lt.ts("Backend CPU", [('rate(process_cpu_seconds_total{job="backend"}[30s])', "cpu")], w=8, unit="percentunit")
lt.ts("Backend memory", [('process_resident_memory_bytes{job="backend"}', "rss")], w=8, unit="bytes")
lt.ts(
    "Simulator calls during the run",
    [("sum by (code) (rate(fuelguard_sim_requests_total[30s]))", "{{code}}")],
    w=24,
    unit="reqps",
    stack=True,
    desc="Flat while dashboard reads rise = the snapshot cache protects the simulator",
)
lt.dump()
