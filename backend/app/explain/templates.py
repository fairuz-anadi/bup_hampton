"""Deterministic explanations from structured facts: the floor under the LangGraph copilot.

Four jobs, each split into `*_facts()` (what the copilot may use) and a template that renders them:
  explain      one recommendation: what, why, binding constraints, projected impact, alternatives, confidence
  investigate  one station: stock, fuel on the way, routes, risk, what is planned for it
  summarize    the network right now: are we OK and what is going wrong
  incident     a tick window: events, decisions and what happened, as a short report

No LLM, no network, and no number that is not in the facts. The LangGraph layer (graph.py) receives the
same facts and falls back to these templates when the model is missing, slow, or unfaithful.

Wording rule (blueprint section 05): projections are never presented as outcomes. We write
"X L projected unmet demand avoided vs the no-action counterfactual", never "saved X L".
"""
from __future__ import annotations

import re

from app.contracts import DecisionRecord, ExplainResponse, NetworkSnapshot, Recommendation
from app.decisions.gate import constraints_of, futures_of, policy_of, selected_legs

FUELS = ("DIESEL", "PETROL", "OCTANE")
_SUFFIX = re.compile(r" ((Fuel|Industrial|Highway|Regional) Station|Station|Depot)$")


def _l(x: float | None) -> str:
    return "n/a" if x is None else f"{x:,.0f} L"


def name(snap: NetworkSnapshot | None, sid: str | None) -> str:
    if not sid:
        return "network"
    if snap is not None:
        for coll in (snap.stations, snap.depots):
            for item in coll:
                if item.id == sid and item.name:
                    return _SUFFIX.sub("", item.name)
    return sid.split("-", 1)[-1].replace("coxsbazar", "Cox's Bazar").title()


_ROUTE_ID = re.compile(r"\broute-([a-z0-9]+)-([a-z0-9]+)\b")
_PLACE_ID = re.compile(r"\b(?:station|depot)-[a-z0-9]+\b")


def humanize(snap: NetworkSnapshot | None, text: str) -> str:
    """Engine messages use ids ("station-mirpur", "route-gazipur-tongi"); operators read names."""
    text = _ROUTE_ID.sub(lambda m: f"{name(snap, 'depot-' + m.group(1))} → {name(snap, 'station-' + m.group(2))}",
                         text)
    text = _PLACE_ID.sub(lambda m: name(snap, m.group(0)), text)
    text = re.sub(r"\bStation (?=[A-Z])", "", text).replace(" -> ", " → ")
    return re.sub(r" \([^()]+ → [^()]+\)", "", text)  # "(Gazipur → Tongi)" repeats the route just named


def _hours(r) -> float | None:
    h = r.hours_to_stockout
    return None if h is None or h >= 99 else h


def _has_backup(snap: NetworkSnapshot | None, r) -> bool:
    """Count the routes into the station; the risk item's own flag is only a fallback."""
    if snap is not None and snap.routes:
        return sum(1 for route in snap.routes if route.station_id == r.station_id) > 1
    return r.has_backup_route


def _response(lines: list[str], cited: list[str], conf: float = 1.0, sep: str = "\n\n") -> ExplainResponse:
    return ExplainResponse(text=sep.join(lines), cited_facts=cited, source="template", confidence=conf,
                           llm_model="template", is_fallback=True)


# ---------------------------------------------------------------------------------------------
# explain: one recommendation
# ---------------------------------------------------------------------------------------------

def decision_facts(rec: Recommendation, snap: NetworkSnapshot | None, gate: dict | None = None) -> dict:
    futures = futures_of(rec)
    by_id = {f.candidate_id: f for f in futures}
    noop = by_id.get("noop") or (futures[0] if futures else None)
    chosen = by_id.get(rec.selected_candidate_id) or by_id.get(policy_of(rec)) or (futures[-1] if futures else None)
    before = noop.network_unmet_liters if noop else (rec.before_projected_unmet or None)
    after = chosen.network_unmet_liters if chosen else (rec.after_projected_unmet or None)
    return {
        "kind": "decision", "decision_id": rec.id, "tick": rec.tick, "policy": policy_of(rec), "mode": rec.mode,
        "confidence": rec.confidence,
        "legs": [{"route": leg.route_id, "from": name(snap, leg.source_depot_id), "to": name(snap, leg.station_id),
                  "fuel": str(leg.fuel_type), "litres": leg.quantity} for leg in selected_legs(rec)],
        "signals": [{"kind": s.kind, "severity": s.severity, "message": humanize(snap, s.message)}
                    for s in rec.signals],
        "risks": [{"station": name(snap, r.station_id), "fuel": str(r.fuel_type), "hours": _hours(r),
                   "p_stockout": r.p_stockout, "backup_route": _has_backup(snap, r)} for r in rec.risks[:4]],
        "constraints": [humanize(snap, c) for c in constraints_of(rec)],
        "futures": [{"id": f.candidate_id, "label": f.label or f.name or f.candidate_id,
                     "unmet_l": f.network_unmet_liters, "service_level": f.service_level,
                     "first_stockout_tick": f.first_stockout_tick,
                     "notes": f.notes if isinstance(f.notes, list) else [f.notes]} for f in futures],
        "before_unmet_l": before, "after_unmet_l": after,
        "avoided_l": None if before is None or after is None else max(0.0, before - after),
        "chosen_label": (chosen.label or chosen.name) if chosen else None,
        "fallback_used": list(rec.fallback_used), "stale": rec.built_on_stale_data,
        "gate": gate or {},
        "multiagent": {
            "consensus_score": rec.multiagent_decision.consensus_score,
            "verdict": rec.multiagent_decision.executive_verdict,
            "critic": rec.multiagent_decision.critic_review,
        } if getattr(rec, "multiagent_decision", None) else None,
    }


def explain_decision(f: dict) -> ExplainResponse:
    cited: list[str] = []

    def cite(s: str) -> str:
        cited.append(s)
        return s

    lines: list[str] = []
    ma = f.get("multiagent")
    if ma and ma.get("verdict"):
        lines.append("**Consensus.** " + cite(ma["verdict"]))

    legs = f["legs"]
    if legs:
        parts = [f"{_l(x['litres'])} {x['fuel'].lower()} {x['from']} → {x['to']}" for x in legs]
        lines.append("**Recommend.** " + cite("Send " + "; ".join(parts) + "."))
    else:
        lines.append("**Recommend.** " + cite("No shipment this tick. Nothing we could send would reduce the "
                                               "projected shortage."))
    if f["mode"] == "containment":
        lines.append(cite("Containment mode: a single-route station cannot be fully supplied, so the plan spreads "
                          "the shortage instead of letting one station run dry."))

    why = []
    for r in f["risks"][:2]:
        if r["hours"] is not None:
            backup = "" if r["backup_route"] else ", no backup route"
            why.append(cite(f"{r['station']} {r['fuel'].lower()} is projected to run out in {r['hours']:.1f} h "
                            f"(P = {r['p_stockout']:.2f}{backup})"))
    for s in f["signals"][:3]:
        if s["message"]:
            why.append(cite(s["message"]))
    if why:
        lines.append("**Why.** " + ". ".join(w.rstrip(".") for w in why) + ".")

    if f["constraints"]:
        lines.append("**Binding constraints.** " + "; ".join(cite(c) for c in f["constraints"][:4]) + ".")

    if f["before_unmet_l"] is not None and f["after_unmet_l"] is not None:
        lines.append("**Projected impact.** " + cite(
            f"{_l(f['avoided_l'])} projected unmet demand avoided vs the no-action counterfactual "
            f"({_l(f['before_unmet_l'])} → {_l(f['after_unmet_l'])} over the Twin horizon)") + ". These are "
            "projections from our Decision Twin, not outcomes.")

    alts = [x for x in f["futures"] if x["label"] != f["chosen_label"]]
    if alts:
        alt_txt = []
        for a in alts:
            note = f" ({'; '.join(n for n in a['notes'] if n)})" if any(a["notes"]) else ""
            alt_txt.append(cite(f"{a['label']}: {_l(a['unmet_l'])} projected unmet{note}"))
        lines.append("**Alternatives considered.** " + "; ".join(alt_txt) + ".")

    gate = f["gate"] or {}
    conf_line = cite(f"Confidence {f['confidence']:.2f}")
    if gate:
        who = "needs operator approval" if gate.get("requires_human") else "may auto-execute inside guardrails"
        if not gate.get("executable", True):
            who = "cannot be executed (recommend only)"
        conf_line += f"; this decision {who}"
        if gate.get("reasons"):
            conf_line += ": " + "; ".join(gate["reasons"][:3])
    lines.append("**Confidence.** " + conf_line + ".")
    if f["fallback_used"]:
        lines.append(cite("Running on fallback: " + ", ".join(f["fallback_used"])) + ". Quality is reduced, "
                     "not stopped.")
    if f["stale"]:
        lines.append(cite("Built on stale simulator data") + ". Shown for reference; execution is locked.")
    return _response(lines, cited, f["confidence"])


# ---------------------------------------------------------------------------------------------
# investigate: one station
# ---------------------------------------------------------------------------------------------

def station_facts(snap: NetworkSnapshot, station_id: str, rec: Recommendation | None = None) -> dict | None:
    st = snap.station_map.get(station_id)
    if st is None:
        return None
    routes = [r for r in snap.routes if r.station_id == station_id]
    arriving = snap.in_transit_totals.get(station_id, {})
    return {
        "kind": "station", "tick": snap.tick, "station": name(snap, station_id), "station_id": station_id,
        "status": st.status, "demand_multiplier": st.demand_multiplier,
        "fuels": [{"fuel": f, "inventory_l": round(st.inventory.get(f, 0.0)),
                   "capacity_l": round(st.capacity.get(f, 0.0)), "in_transit_l": round(arriving.get(f, 0.0))}
                  for f in FUELS],
        "routes": [{"route": r.id, "from": name(snap, r.depot_id), "status": r.status, "transit_ticks": r.transit_ticks}
                   for r in routes],
        "risks": [{"fuel": str(r.fuel_type), "hours": _hours(r), "p_stockout": r.p_stockout}
                  for r in (rec.risks if rec else []) if r.station_id == station_id],
        "planned": [{"fuel": str(leg.fuel_type), "litres": leg.quantity, "from": name(snap, leg.source_depot_id)}
                    for leg in (selected_legs(rec) if rec else []) if leg.station_id == station_id],
        "events": [e.type for e in snap.events if e.status == "ACTIVE" and station_id in _event_targets(snap, e)],
    }


def _event_targets(snap: NetworkSnapshot, e) -> set[str]:
    p = e.parameters or {}
    if e.type == "route_disruption":
        ids = p.get("route_ids") or [r.id for r in snap.routes]
        return {r.station_id for r in snap.routes if r.id in ids}
    if e.type in ("demand_spike",) and p.get("region_ids"):
        return {s.id for s in snap.stations if s.region_id in p["region_ids"]}
    return set(p.get("station_ids") or [s.id for s in snap.stations])


def investigate_station(f: dict) -> ExplainResponse:
    cited: list[str] = []
    stock = "; ".join(f"{x['fuel'].lower()} {_l(x['inventory_l'])} of {_l(x['capacity_l'])}"
                      + (f" (+{_l(x['in_transit_l'])} on the way)" if x["in_transit_l"] else "") for x in f["fuels"])
    lines = [f"**{f['station']}** is {f['status']} at tick {f['tick']}, demand ×{f['demand_multiplier']:.2f}. "
             f"Stock: {stock}."]
    cited.append(lines[0])
    routes = [f"{r['from']} ({r['status'].lower()}, {r['transit_ticks']} ticks)" for r in f["routes"]]
    lines.append(("Supplied from " + ", ".join(routes) + "."
                  + (" It has no backup route." if len(f["routes"]) == 1 else "")) if routes else "No routes in.")
    cited.append(lines[-1])
    risky = [r for r in f["risks"] if r["hours"] is not None]
    if risky:
        lines.append("Projected to run out: " + "; ".join(f"{r['fuel'].lower()} in {r['hours']:.1f} h "
                                                         f"(P = {r['p_stockout']:.2f})" for r in risky) + ".")
        cited.append(lines[-1])
    else:
        lines.append("No stockout projected inside the horizon.")
    if f["planned"]:
        lines.append("Recommended now: " + "; ".join(f"{_l(p['litres'])} {p['fuel'].lower()} from {p['from']}"
                                                     for p in f["planned"]) + ".")
        cited.append(lines[-1])
    if f["events"]:
        lines.append("Affected by: " + ", ".join(e.replace("_", " ") for e in f["events"]) + ".")
        cited.append(lines[-1])
    return _response(lines, cited, sep=" ")


# ---------------------------------------------------------------------------------------------
# summarize: the network now
# ---------------------------------------------------------------------------------------------

def network_facts(snap: NetworkSnapshot) -> dict:
    m = snap.metrics
    return {
        "kind": "network", "tick": snap.tick, "stale": snap.is_stale,
        "service_level": m.service_level if m else None, "unmet_total_l": m.unmet_demand_liters if m else None,
        "active_events": [{"type": e.type, "until_tick": e.end_tick} for e in snap.events if e.status == "ACTIVE"],
        "disrupted_routes": [r.id for r in snap.routes if r.status == "DISRUPTED"],
        "outage_stations": [name(snap, s.id) for s in snap.stations if s.status != "OPEN"],
        "dry_tanks": [f"{name(snap, s.id)} {str(fuel).lower()}" for s in snap.stations
                      for fuel, q in s.inventory.items() if q <= 0.5],
    }


def network_summary(snap: NetworkSnapshot) -> ExplainResponse:
    return network_summary_from_facts(network_facts(snap))


def network_summary_from_facts(f: dict) -> ExplainResponse:
    cited: list[str] = []
    lines = []

    def add(s: str) -> None:
        lines.append(s)
        cited.append(s)

    if f["service_level"] is not None:
        add(f"Service level {f['service_level']:.1%} so far; {f['unmet_total_l']:,.0f} L unmet in total.")
    if f["active_events"]:
        add("Active: " + "; ".join(f"{e['type'].replace('_', ' ')} until tick {e['until_tick']}"
                                   for e in f["active_events"]) + ".")
    if f["disrupted_routes"]:
        add("Disrupted routes: " + ", ".join(f["disrupted_routes"]) + ".")
    if f["outage_stations"]:
        add("Stations in outage: " + ", ".join(f["outage_stations"]) + ".")
    if f["dry_tanks"]:
        add("Dry tanks now: " + ", ".join(f["dry_tanks"]) + ".")
    if f["stale"]:
        add("Data is stale; figures may be out of date.")
    if not f["active_events"] and not f["disrupted_routes"] and not f["outage_stations"]:
        lines.append("No active disruptions.")
    return _response(lines, cited, sep=" ")


# ---------------------------------------------------------------------------------------------
# incident: a tick window
# ---------------------------------------------------------------------------------------------

def incident_facts(snap: NetworkSnapshot, records: list[DecisionRecord], from_tick: int, to_tick: int,
                   mode_log: list[dict] | None = None) -> dict:
    events = [e for e in snap.events if e.start_tick <= to_tick and e.end_tick >= from_tick]
    decisions = sorted((r for r in records if from_tick <= r.sim_tick <= to_tick), key=lambda r: r.sim_tick)
    m = snap.metrics
    return {
        "kind": "incident", "from_tick": from_tick, "to_tick": to_tick, "now_tick": snap.tick,
        "events": [{"type": e.type, "start_tick": e.start_tick, "end_tick": e.end_tick, "status": e.status,
                    "parameters": e.parameters} for e in sorted(events, key=lambda e: e.start_tick)],
        "decisions": [{
            "id": r.decision_id, "tick": r.sim_tick, "stage": r.stage,
            "litres": sum(leg.quantity for leg in selected_legs(r.recommendation)) if r.recommendation else 0,
            "by": (r.approval or {}).get("by"), "reason": (r.approval or {}).get("reason"),
            "modified": bool((r.approval or {}).get("modified")),
            "accepted_legs": sum(1 for s in r.submissions if s.result == "accepted"),
            "twin_check": r.twin_check} for r in decisions],
        "mode_changes": [x for x in (mode_log or []) if x.get("tick") is not None
                         and from_tick <= x["tick"] <= to_tick and "Mode" in x.get("message", "")],
        "service_level_now": m.service_level if m else None, "unmet_total_l_now": m.unmet_demand_liters if m else None,
    }


def incident_report(f: dict) -> ExplainResponse:
    cited: list[str] = []

    def cite(s: str) -> str:
        cited.append(s)
        return s

    ev = f["events"]
    head = (f"Ticks {f['from_tick']}–{f['to_tick']}: " +
            (", ".join(f"{e['type'].replace('_', ' ')} (t{e['start_tick']}–t{e['end_tick']}, {e['status'].lower()})"
                       for e in ev) if ev else "no simulator events") + ".")
    lines = ["**Summary.** " + cite(head)]
    ds = f["decisions"]
    if ds:
        by_stage: dict[str, int] = {}
        for d in ds:
            by_stage[d["stage"]] = by_stage.get(d["stage"], 0) + 1
        lines.append("**Decisions.** " + cite(f"{len(ds)} decision(s): " +
                                               ", ".join(f"{v} {k}" for k, v in sorted(by_stage.items())) + ".") +
                     " " + " ".join(cite(
                         f"t{d['tick']} {d['id']}: {_l(d['litres'])} {d['stage']}"
                         + (f" by {d['by']}" if d["by"] else "") + (" (modified)" if d["modified"] else "")
                         + (f", reason: {d['reason']}" if d["reason"] else "") + ".") for d in ds[:6]))
    else:
        lines.append("**Decisions.** " + cite("No shipments were recommended in this window."))
    checks = [d["twin_check"] for d in ds if d["twin_check"]]
    if checks:
        lines.append("**Twin self-check.** " + "; ".join(cite(
            f"projected {_l(c['predicted_l'])} vs actual {_l(c['actual_l'])} unmet (error {_l(c['error_l'])})")
            for c in checks[:4]) + ".")
    if f["mode_changes"]:
        lines.append("**Autonomy.** " + " ".join(cite(f"t{x['tick']}: {x['message']}") for x in f["mode_changes"][:5]))
    if f["service_level_now"] is not None:
        lines.append("**Now.** " + cite(f"Service level {f['service_level_now']:.1%} at tick {f['now_tick']}, "
                                         f"{_l(f['unmet_total_l_now'])} unmet in total (simulator ground truth)") + ".")
    open_ev = [e for e in ev if e["status"] != "RESOLVED"]
    if open_ev:
        lines.append("**Open.** " + cite(", ".join(e["type"].replace("_", " ") for e in open_ev) +
                                          " still active or scheduled") + ".")
    return _response(lines, cited)
