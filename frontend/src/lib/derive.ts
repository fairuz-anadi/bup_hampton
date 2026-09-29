// Turns raw state into what an operator needs right now. Every number here comes from the snapshot, the current
// recommendation or the simulator's demand history; nothing is invented for display.
import type { AllocationLeg, CurrentView, DecisionRecord, Fuel, Gate, NetworkSnapshot, Recommendation, RiskItem, SimEvent, TwinFuture } from '../api/types';
import { FUELS } from '../api/types';
import type { Tone } from '../components/ui';
import { approxHours, horizonLabel, stepsWithTime } from './copy';
import { chosenFuture, eventName, fuelName, humanize, litres, noopFuture, placeName, routeName } from './format';

/** Risks below this probability are background noise (the engine reports every station × fuel). */
export const RISK_MIN_P = 0.25;
const realRisk = (r: RiskItem) => r.hours_to_stockout != null && r.hours_to_stockout < 99 && r.p_stockout >= RISK_MIN_P;

export interface StationState { tone: Tone; label: string }

/** Healthy / Needs attention / Unavailable per station. */
export function stationStates(snap: NetworkSnapshot, rec: Recommendation | null): Record<string, StationState> {
  const out: Record<string, StationState> = {};
  for (const s of snap.stations) {
    const dry = s.status === 'OPEN' && FUELS.some((f) => (s.inventory[f] ?? 0) <= 0.5);
    const worst = Math.max(0, ...(rec?.risks ?? []).filter((r) => r.station_id === s.id && realRisk(r)).map((r) => r.p_stockout));
    out[s.id] = s.status !== 'OPEN' ? { tone: 'crit', label: 'Unavailable' }
      : dry || worst >= 0.5 ? { tone: 'crit', label: 'Needs attention' }
      : worst >= RISK_MIN_P ? { tone: 'warn', label: 'Needs attention' }
      : { tone: 'ok', label: 'Healthy' };
  }
  return out;
}

export interface Attention { station_id: string; fuel: Fuel; hours: number | null; p: number; tone: Tone; kind: 'closed' | 'empty' | 'risk'; why: string }

/** The few station × fuel pairs worth a human's attention, worst first. */
export function attention(snap: NetworkSnapshot, rec: Recommendation | null, max = 3): Attention[] {
  const items: Attention[] = [];
  for (const s of snap.stations) {
    if (s.status !== 'OPEN') {
      items.push({ station_id: s.id, fuel: 'DIESEL', hours: null, p: 1, tone: 'crit', kind: 'closed', why: 'The station is temporarily closed and can’t receive or sell fuel until it reopens.' });
      continue;
    }
    for (const f of FUELS) if ((s.inventory[f] ?? 0) <= 0.5) items.push({ station_id: s.id, fuel: f, hours: 0, p: 1, tone: 'crit', kind: 'empty',
      why: `The ${fuelName(f).toLowerCase()} tank is empty${s.demand_multiplier > 1.05 ? ` and demand is ${s.demand_multiplier.toFixed(1)}× normal` : ''}.` });
  }
  for (const r of rec?.risks ?? []) {
    if (!realRisk(r) || items.some((i) => i.station_id === r.station_id && (i.fuel === r.fuel_type || i.kind === 'closed'))) continue;
    items.push({ station_id: r.station_id, fuel: r.fuel_type, hours: r.hours_to_stockout, p: r.p_stockout,
      tone: r.p_stockout >= 0.5 ? 'crit' : 'warn', kind: 'risk', why: demandWhy(snap, r.station_id, r.fuel_type) });
  }
  return items.sort((a, b) => b.p - a.p || (a.hours ?? 99) - (b.hours ?? 99)).slice(0, max);
}

/** "Demand at Tongi is currently 1.8× normal." from the simulator's own multiplier, else a stock-based reason. */
function demandWhy(snap: NetworkSnapshot, sid: string, fuel: Fuel): string {
  const st = snap.stations.find((s) => s.id === sid);
  const m = st?.demand_multiplier ?? 1;
  if (m > 1.05) return `Demand at ${placeName(snap, sid)} is currently ${m.toFixed(1)}× normal.`;
  const one = snap.routes.filter((x) => x.destination_station_id === sid).length === 1;
  return `${fuelName(fuel)} is being used faster than it is resupplied (${litres(st?.inventory[fuel])} left${one ? ', only one supply route' : ''}).`;
}

const TARGETS = ['station_ids', 'route_ids', 'region_ids', 'depot_ids'];
const eventKey = (e: SimEvent) => e.type + JSON.stringify(TARGETS.map((k) => (e.parameters[k] as string[] | undefined) ?? []));

/** Open events grouped by what they do, so one scenario injected twice shows (and counts) once. Latest-ending first. */
export function eventGroups(snap: NetworkSnapshot, statuses: SimEvent['status'][] = ['ACTIVE']): SimEvent[][] {
  const m: Record<string, SimEvent[]> = {};
  for (const e of snap.events) if (statuses.includes(e.status)) (m[eventKey(e)] ??= []).push(e);
  return Object.values(m).map((g) => g.sort((a, b) => b.end_tick - a.end_tick));
}
export const activeEvents = (snap: NetworkSnapshot) => eventGroups(snap).map((g) => g[0]);

/** Status banner for the whole network, in plain words. */
export function overall(snap: NetworkSnapshot, rec: Recommendation | null, legs: AllocationLeg[]): { tone: Tone; title: string; sub: string } {
  const items = attention(snap, rec, 9);
  const stations = [...new Set(items.map((i) => i.station_id))];
  const active = activeEvents(snap);
  if (!items.length) {
    if (active.length) return { tone: 'warn', title: `${active.length} active disruption${active.length > 1 ? 's' : ''}`,
      sub: `${active.map((e) => describeActive(snap, e)).join(' · ')}. All ${snap.stations.length} stations are still being served.` };
    return { tone: 'ok', title: 'Network operating normally', sub: `All ${snap.stations.length} stations are currently being served.` };
  }
  const top = items[0];
  const where = placeName(snap, top.station_id);
  const sub = top.kind === 'closed' ? `${where} is temporarily unavailable.`
    : top.kind === 'empty' ? `${where} has run out of ${fuelName(top.fuel).toLowerCase()}.`
    : `${where} may run short of ${fuelName(top.fuel).toLowerCase()} in about ${approxHours(top.hours ?? 0)}.`;
  const crit = items.some((i) => i.tone === 'crit');
  if (crit && legs.length) return { tone: 'crit', title: 'Action recommended', sub: top.kind === 'risk' ? `${where} is at risk of running out of ${fuelName(top.fuel).toLowerCase()}.` : sub };
  return { tone: crit ? 'crit' : 'warn', title: `${stations.length} station${stations.length > 1 ? 's need' : ' needs'} attention`, sub };
}

/** "Tongi station unavailable", "Gazipur → Mirpur route closed", "Demand in Dhaka 1.8× normal". */
export function describeActive(snap: NetworkSnapshot, e: SimEvent): string {
  const p = e.parameters;
  const ids = (k: string) => (p[k] as string[] | undefined) ?? [];
  if (e.type === 'station_outage') return `${ids('station_ids').map((x) => placeName(snap, x)).join(', ')} station unavailable`;
  if (e.type === 'route_disruption') return `${ids('route_ids').map((x) => routeName(snap, x)).join(', ')} route closed`;
  if (e.type === 'demand_spike') {
    const where = ids('region_ids').map((r) => snap.regions.find((x) => x.id === r)?.name ?? r).concat(ids('station_ids').map((x) => placeName(snap, x))).join(', ');
    return `Demand in ${where || 'the network'}${typeof p.multiplier === 'number' ? ` ${p.multiplier}× normal` : ' raised'}`;
  }
  const d = describeEvent(snap, p);
  return `${eventName(e.type)}${d ? `: ${d}` : ''}`;
}

/** The station × fuel the current recommendation is mainly about: a leg target at real risk, else the top attention item. */
export function focus(snap: NetworkSnapshot, rec: Recommendation | null, legs: AllocationLeg[]): { station_id: string; fuel: Fuel; risk: RiskItem | null } | null {
  const risks = (rec?.risks ?? []).filter(realRisk);
  for (const l of legs) {
    const r = risks.find((x) => x.station_id === l.station_id && x.fuel_type === l.fuel_type);
    if (r) return { station_id: r.station_id, fuel: r.fuel_type, risk: r };
  }
  const a = attention(snap, rec, 1)[0];
  if (a) return { station_id: a.station_id, fuel: a.fuel, risk: risks.find((x) => x.station_id === a.station_id && x.fuel_type === a.fuel) ?? null };
  if (legs[0]) return { station_id: legs[0].station_id, fuel: legs[0].fuel_type, risk: null };
  return null;
}

export function describeEvent(snap: NetworkSnapshot, p: Record<string, unknown>): string {
  const bits: string[] = [];
  const ids = (k: string) => ((p[k] as string[] | undefined) ?? []).map((x) => (k === 'route_ids' ? routeName(snap, x) : placeName(snap, x)));
  if (p.region_ids) bits.push(((p.region_ids as string[]) ?? []).map((r) => snap.regions.find((x) => x.id === r)?.name ?? r).join(', '));
  if (p.station_ids) bits.push(ids('station_ids').join(', '));
  if (p.depot_ids) bits.push(ids('depot_ids').join(', '));
  if (p.route_ids) bits.push(ids('route_ids').join(', '));
  if (typeof p.multiplier === 'number') bits.push(`×${p.multiplier}`);
  if (typeof p.factor === 'number') bits.push(`×${p.factor}`);
  if (typeof p.delay_ticks === 'number') bits.push(`+${p.delay_ticks} steps`);
  return bits.join(' · ');
}

// ---------------------------------------------------------------- Decision Center: the story

export interface Situation { headline: string; detail: string | null; ratio: number | null }

/** Section 1, "What's happening?", in two plain sentences. */
export function situation(snap: NetworkSnapshot, rec: Recommendation | null, f: ReturnType<typeof focus>, ev: DemandEvidence | null): Situation {
  if (!f) {
    const active = activeEvents(snap);
    const fut = rec ? noopFuture(rec) : undefined;
    return {
      headline: active.length ? `${active.map((e) => describeActive(snap, e)).join('; ')}.` : 'Nothing needs attention right now.',
      detail: `No station is expected to run short${fut ? ` in ${horizonLabel(fut.horizon_ticks, snap)}` : ''}, counting fuel already on the way.`,
      ratio: null,
    };
  }
  const st = snap.stations.find((s) => s.id === f.station_id);
  const where = placeName(snap, f.station_id), fuel = fuelName(f.fuel).toLowerCase();
  if (st?.status !== 'OPEN') return { headline: `${where} station is temporarily unavailable.`, detail: 'It can’t receive or sell fuel until it reopens.', ratio: null };
  const m = st.demand_multiplier > 1.05 ? st.demand_multiplier : ev?.anomaly && ev.median > 0 ? ev.current / ev.median : null;
  const empty = (st.inventory[f.fuel] ?? 0) <= 0.5;
  const headline = m && m > 1.05 ? `${where}’s ${fuel} demand is ${m.toFixed(1)}× higher than normal.`
    : empty ? `${where} has run out of ${fuel}.`
    : `${where} is using ${fuel} faster than it is being resupplied.`;
  const h = f.risk?.hours_to_stockout;
  const detail = empty ? `The ${fuel} tank is already empty, so customers there can’t be served until fuel arrives.`
    : h != null ? `At the current rate, the station may run short in about ${approxHours(h)}.` : null;
  return { headline, detail, ratio: m };
}

/** Section 2, "Why this action?": each line is checked against the snapshot, and only true lines are shown. */
export function whyBullets(snap: NetworkSnapshot, rec: Recommendation, legs: AllocationLeg[], f: ReturnType<typeof focus>): string[] {
  const lead = legs[0];
  if (!lead) return [];
  const out: string[] = [];
  const to = placeName(snap, lead.station_id), from = placeName(snap, lead.source_depot_id), fuel = fuelName(lead.fuel_type).toLowerCase();
  const risks = rec.risks.filter(realRisk).sort((a, b) => (a.hours_to_stockout ?? 99) - (b.hours_to_stockout ?? 99));
  if (risks[0] && risks[0].station_id === lead.station_id) out.push(`${to} is expected to run short first`);
  else if (f?.risk && f.station_id === lead.station_id) out.push(`${to} may run short in about ${approxHours(f.risk.hours_to_stockout ?? 0)}`);
  const depot = snap.depots.find((d) => d.id === lead.source_depot_id);
  const reserve = depot ? 0.1 * (depot.capacity[lead.fuel_type] ?? 0) : 0;
  if (depot && (depot.inventory[lead.fuel_type] ?? 0) - lead.quantity >= reserve) out.push(`${from} has enough ${fuel} and keeps its safety reserve`);
  const route = snap.routes.find((r) => r.id === lead.route_id);
  if (route?.status === 'AVAILABLE') out.push('The route is currently open');
  const chosen = chosenFuture(rec);
  if (chosen) {
    const others = Object.entries(chosen.unmet_by_station ?? {}).filter(([sid, l]) => sid !== lead.station_id && l > 0.5);
    out.push(others.length ? `${others.length} other station${others.length > 1 ? 's' : ''} may still see a shortage (${others.map(([sid]) => placeName(snap, sid)).join(', ')})`
      : 'Other stations remain sufficiently supplied');
  }
  return out;
}

export interface Impact {
  without: TwinFuture; with: TwinFuture; avoided: number; horizon: string;
  affectedWithout: number; affectedWith: number;
}

/** Section 3, "Expected impact": do nothing vs the recommended plan, from the Decision Twin. */
export function impact(snap: NetworkSnapshot, rec: Recommendation): Impact | null {
  const n = noopFuture(rec), c = chosenFuture(rec);
  if (!n || !c) return null;
  const affected = (f: TwinFuture) => Object.values(f.unmet_by_station ?? {}).filter((l) => l > 0.5).length;
  return { without: n, with: c, avoided: n.network_unmet_liters - c.network_unmet_liters, horizon: horizonLabel(n.horizon_ticks, snap),
    affectedWithout: affected(n), affectedWith: affected(c) };
}

/** "Send 3,000 L diesel · Gazipur → Tongi" / "No shipment needed". Used to notice when the recommendation changes. */
export function summarize(snap: NetworkSnapshot | null, legs: AllocationLeg[]): string {
  if (!legs.length) return 'No shipment needed';
  if (legs.length === 1) return `Send ${litres(legs[0].quantity)} ${fuelName(legs[0].fuel_type).toLowerCase()} · ${placeName(snap, legs[0].source_depot_id)} → ${placeName(snap, legs[0].station_id)}`;
  return `${legs.length} shipments · ${litres(legs.reduce((s, l) => s + l.quantity, 0))}`;
}
export const legsSignature = (rec: Recommendation | null, legs: AllocationLeg[]) =>
  rec ? legs.map((l) => `${l.route_id}:${l.fuel_type}:${Math.round(l.quantity / 100)}`).sort().join('|') || 'none' : 'no-rec';

/** "Expected arrival: ~2 simulation steps (about 30 min)". */
export function arrival(snap: NetworkSnapshot, leg: AllocationLeg): string | null {
  const r = snap.routes.find((x) => x.id === leg.route_id);
  return r ? stepsWithTime(r.transit_ticks, snap) : null;
}

/** Deduplicated detector signals (the engine can repeat one per fuel or per tick). */
export function uniqueSignals(snap: NetworkSnapshot, rec: Recommendation) {
  const seen = new Set<string>();
  return rec.signals.filter((s) => { const k = s.kind + s.message; if (seen.has(k)) return false; seen.add(k); return true; })
    .map((s) => ({ ...s, text: humanize(snap, s.message) }));
}

// ---------------------------------------------------------------- Detect: demand evidence

export interface DemandObs { tick: number; fuel_type: string; demand_liters: number; unmet_liters: number }
export interface DemandEvidence { current: number; low: number; high: number; median: number; changePct: number; anomaly: boolean; series: { tick: number; v: number }[]; baselineTicks: number }

/** Recent demand (last 4 ticks) against the normal range (p10–p90 of the 48 ticks before). */
export function demandEvidence(obs: DemandObs[], fuel: string): DemandEvidence | null {
  const rows = obs.filter((o) => o.fuel_type === fuel).sort((a, b) => a.tick - b.tick);
  if (rows.length < 8) return null;
  const recent = rows.slice(-4), base = rows.slice(-52, -4);
  if (base.length < 4) return null;
  const vals = base.map((r) => r.demand_liters).sort((a, b) => a - b);
  const q = (p: number) => vals[Math.min(vals.length - 1, Math.max(0, Math.round(p * (vals.length - 1))))];
  const current = recent.reduce((s, r) => s + r.demand_liters, 0) / recent.length;
  const median = q(0.5), low = q(0.1), high = q(0.9);
  const changePct = median > 0 ? (current - median) / median : 0;
  return { current, low, high, median, changePct, anomaly: current > high * 1.1 || current < low * 0.9,
    series: rows.slice(-52).map((r) => ({ tick: r.tick, v: r.demand_liters })), baselineTicks: base.length };
}

// ---------------------------------------------------------------- Decide: constraint checks

export interface Check { ok: boolean; label: string }

/** The same guardrails the backend gate and allocation writer enforce, shown per leg. */
export function constraintChecks(snap: NetworkSnapshot, leg: AllocationLeg, gate: Gate | null): Check[] {
  const route = snap.routes.find((r) => r.id === leg.route_id);
  const station = snap.stations.find((s) => s.id === leg.station_id);
  const depot = snap.depots.find((d) => d.id === leg.source_depot_id);
  const f = leg.fuel_type;
  const headroom = station ? (station.capacity[f] ?? 0) - (station.inventory[f] ?? 0) - (snap.in_transit_totals[station.id]?.[f] ?? 0) : 0;
  const reserve = depot ? 0.1 * (depot.capacity[f] ?? 0) : 0;
  const blocked = gate?.blocked_legs?.some((b) => b.route_id === leg.route_id);
  return [
    { ok: route?.status === 'AVAILABLE', label: route ? `Route ${route.status === 'AVAILABLE' ? 'open' : 'closed'} (${route.transit_ticks} steps)` : 'Route unknown' },
    { ok: station?.status === 'OPEN', label: station?.status === 'OPEN' ? 'Destination station open' : 'Destination station unavailable' },
    { ok: !!depot && (depot.inventory[f] ?? 0) - leg.quantity >= reserve, label: `Depot keeps its reserve (${litres(reserve)})` },
    // The writer splits anything above max_shipment into legs, so this only fails for an unknown route.
    { ok: !!route, label: route ? `Shipment limit ${litres(route.max_shipment)}${leg.quantity > route.max_shipment ? ' · split into legs' : ''}` : 'Shipment limit' },
    { ok: headroom >= leg.quantity - 1e-6, label: `Tank space incl. fuel on the way: ${litres(Math.max(0, headroom))}` },
    ...(blocked ? [{ ok: false, label: 'Blocked by the backend safety gate' }] : []),
  ];
}

// ---------------------------------------------------------------- activity feed

export interface Activity { tick: number; tone: Tone; text: string }

export function activity(snap: NetworkSnapshot | null, current: CurrentView | null, records: DecisionRecord[], max = 12): Activity[] {
  const out: Activity[] = [];
  for (const e of snap?.events ?? []) {
    if (e.start_tick <= (snap?.tick ?? 0)) out.push({ tick: e.start_tick, tone: 'warn', text: `${eventName(e.type)} started${describeEvent(snap!, e.parameters) ? `: ${describeEvent(snap!, e.parameters)}` : ''}` });
    if (e.status === 'RESOLVED') out.push({ tick: e.end_tick, tone: 'ok', text: `${eventName(e.type)} resolved` });
  }
  for (const r of records) {
    const litresTotal = r.recommendation ? (r.recommendation.legs?.length ? r.recommendation.legs : r.recommendation.candidates?.find((c) => c.id === r.recommendation!.selected_candidate_id)?.legs ?? []).reduce((s, l) => s + l.quantity, 0) : 0;
    out.push({ tick: r.sim_tick, tone: 'idle', text: `Recommendation ${r.decision_id} generated (${litres(litresTotal)})` });
    if (r.approval) out.push({ tick: r.sim_tick, tone: r.approval.decision === 'approved' ? 'ok' : 'crit', text: `${r.decision_id} ${r.approval.decision} by ${r.approval.by}` });
    if (r.submissions.length) out.push({ tick: r.sim_tick, tone: 'ok', text: `Simulator accepted ${r.submissions.filter((s) => s.result === 'accepted').length} of ${r.submissions.length} shipment(s)` });
    if (r.twin_check) out.push({ tick: r.sim_tick, tone: 'idle', text: `Twin checked itself: projected ${litres(r.twin_check.predicted_l)} vs actual ${litres(r.twin_check.actual_l)}` });
  }
  for (const l of current?.autonomy?.log ?? []) if (l.tick != null) out.push({ tick: l.tick, tone: l.message.includes('MANUAL') && l.message.indexOf('->') < l.message.indexOf('MANUAL') ? 'crit' : 'idle', text: l.message.replace('->', '→') });
  return out.sort((a, b) => b.tick - a.tick).slice(0, max);
}
