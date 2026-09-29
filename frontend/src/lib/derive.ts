// Turns raw state into what an operator needs right now. Every number here comes from the snapshot, the current
// recommendation or the simulator's demand history; nothing is invented for display.
import type { AllocationLeg, CurrentView, DecisionRecord, Fuel, Gate, NetworkSnapshot, Recommendation, RiskItem } from '../api/types';
import { FUELS } from '../api/types';
import type { Tone } from '../components/ui';
import { eventName, fuelName, humanize, litres, placeName, routeName } from './format';

export interface StationState { tone: Tone; label: string }

/** Healthy / At risk / Shortage / Outage per station. */
export function stationStates(snap: NetworkSnapshot, rec: Recommendation | null): Record<string, StationState> {
  const out: Record<string, StationState> = {};
  for (const s of snap.stations) {
    const dry = s.status === 'OPEN' && FUELS.some((f) => (s.inventory[f] ?? 0) <= 0.5);
    const risky = (rec?.risks ?? []).filter((r) => r.station_id === s.id && r.hours_to_stockout != null && r.hours_to_stockout < 99);
    const worst = Math.max(0, ...risky.map((r) => r.p_stockout));
    out[s.id] = s.status !== 'OPEN' ? { tone: 'crit', label: 'Outage' }
      : dry ? { tone: 'crit', label: 'Shortage' }
      : worst >= 0.5 ? { tone: 'crit', label: 'At risk' }
      : worst >= 0.25 ? { tone: 'warn', label: 'Watch' }
      : { tone: 'ok', label: 'Healthy' };
  }
  return out;
}

export function overall(snap: NetworkSnapshot, rec: Recommendation | null): { tone: Tone; label: string } {
  const states = Object.values(stationStates(snap, rec));
  if (states.some((s) => s.label === 'Shortage' || s.label === 'Outage')) return { tone: 'crit', label: 'Shortage in the network' };
  if (states.some((s) => s.tone === 'crit')) return { tone: 'crit', label: 'Risk needs attention' };
  if (snap.events.some((e) => e.status === 'ACTIVE') || states.some((s) => s.tone === 'warn')) return { tone: 'warn', label: 'Under pressure' };
  return { tone: 'ok', label: 'Network operating normally' };
}

export interface Attention { station_id: string; fuel: Fuel; hours: number | null; p: number; tone: Tone; why: string }

/** The few station × fuel pairs worth a human's attention, worst first. */
export function attention(snap: NetworkSnapshot, rec: Recommendation | null, max = 3): Attention[] {
  const items: Attention[] = [];
  for (const s of snap.stations) {
    if (s.status !== 'OPEN') {
      items.push({ station_id: s.id, fuel: 'DIESEL', hours: null, p: 1, tone: 'crit', why: 'Station is in outage; it cannot be served until it reopens.' });
      continue;
    }
    for (const f of FUELS) if ((s.inventory[f] ?? 0) <= 0.5) items.push({ station_id: s.id, fuel: f, hours: 0, p: 1, tone: 'crit', why: `${fuelName(f)} tank is empty.` });
  }
  for (const r of rec?.risks ?? []) {
    if (r.hours_to_stockout == null || r.hours_to_stockout >= 99 || r.p_stockout < 0.25) continue;
    if (items.some((i) => i.station_id === r.station_id && i.fuel === r.fuel_type)) continue;
    items.push({ station_id: r.station_id, fuel: r.fuel_type, hours: r.hours_to_stockout, p: r.p_stockout,
      tone: r.p_stockout >= 0.5 ? 'crit' : 'warn', why: riskWhy(snap, rec, r) });
  }
  return items.sort((a, b) => b.p - a.p || (a.hours ?? 99) - (b.hours ?? 99)).slice(0, max);
}

function riskWhy(snap: NetworkSnapshot, rec: Recommendation | null, r: RiskItem): string {
  const sig = rec?.signals.find((s) => s.station_id === r.station_id && (!s.fuel_type || s.fuel_type === r.fuel_type));
  if (sig?.message) return humanize(snap, sig.message);
  const st = snap.stations.find((s) => s.id === r.station_id);
  const bits = [`${litres(st?.inventory[r.fuel_type])} left`];
  if (snap.routes.filter((x) => x.destination_station_id === r.station_id).length === 1) bits.push('single supply route');
  if ((st?.demand_multiplier ?? 1) > 1.05) bits.push(`demand ×${st!.demand_multiplier.toFixed(1)}`);
  return `Inventory falling faster than it is resupplied (${bits.join(', ')}).`;
}

/** The station × fuel the current recommendation is mainly about: the first leg's target if it is at risk, else the worst risk. */
export function focus(snap: NetworkSnapshot, rec: Recommendation | null, legs: AllocationLeg[]): { station_id: string; fuel: Fuel; risk: RiskItem | null } | null {
  const risks = (rec?.risks ?? []).filter((r) => r.hours_to_stockout != null && r.hours_to_stockout < 99);
  for (const l of legs) {
    const r = risks.find((x) => x.station_id === l.station_id && x.fuel_type === l.fuel_type);
    if (r) return { station_id: r.station_id, fuel: r.fuel_type, risk: r };
  }
  const worst = [...risks].sort((a, b) => b.p_stockout - a.p_stockout)[0];
  if (worst) return { station_id: worst.station_id, fuel: worst.fuel_type, risk: worst };
  if (legs[0]) return { station_id: legs[0].station_id, fuel: legs[0].fuel_type, risk: null };
  const a = attention(snap, rec, 1)[0];
  return a ? { station_id: a.station_id, fuel: a.fuel, risk: null } : null;
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
  if (typeof p.delay_ticks === 'number') bits.push(`+${p.delay_ticks} ticks`);
  return bits.join(' · ');
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
    { ok: route?.status === 'AVAILABLE', label: route ? `Route ${route.status === 'AVAILABLE' ? 'available' : 'disrupted'} (${route.transit_ticks} ticks)` : 'Route unknown' },
    { ok: station?.status === 'OPEN', label: station?.status === 'OPEN' ? 'Destination station open' : 'Destination station in outage' },
    { ok: !!depot && (depot.inventory[f] ?? 0) - leg.quantity >= reserve, label: `Depot keeps its reserve (${litres(reserve)})` },
    // The writer splits anything above max_shipment into legs, so this only fails for an unknown route.
    { ok: !!route, label: route ? `Shipment limit ${litres(route.max_shipment)}${leg.quantity > route.max_shipment ? ' · split into legs' : ''}` : 'Shipment limit' },
    { ok: headroom >= leg.quantity - 1e-6, label: `Tank headroom incl. fuel in transit: ${litres(Math.max(0, headroom))}` },
    ...(blocked ? [{ ok: false, label: 'Blocked by the backend gate' }] : []),
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
