import type { AllocationLeg, NetworkSnapshot, Recommendation, TwinFuture } from '../api/types';

export const litres = (n: number | null | undefined, unit = true) =>
  n == null || Number.isNaN(n) ? '—' : `${Math.round(n).toLocaleString('en-US')}${unit ? ' L' : ''}`;
export const kL = (n: number) => (Math.abs(n) >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `${Math.round(n)}`);
export const pct = (x: number | null | undefined, digits = 1) => (x == null ? '—' : `${(x * 100).toFixed(digits)}%`);
export const hours = (h: number | null | undefined) =>
  h == null || h >= 99 ? 'beyond horizon' : h < 1 ? `${Math.round(h * 60)} min` : `${h.toFixed(1)} h`;
export const ago = (s: number | null | undefined) =>
  s == null ? '—' : s < 2 ? 'just now' : s < 60 ? `${Math.round(s)} s ago` : `${Math.round(s / 60)} min ago`;
export const fuelName = (f: string) => f.charAt(0) + f.slice(1).toLowerCase();
export const eventName = (t: string) => t.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

/** "station-mirpur" -> "Mirpur", using snapshot names when available. */
export function placeName(snap: NetworkSnapshot | null, id: string | null | undefined): string {
  if (!id) return 'Network';
  const hit = snap?.stations.find((s) => s.id === id) ?? snap?.depots.find((d) => d.id === id);
  if (hit?.name) return hit.name.replace(/ (Fuel|Industrial|Highway|Regional) Station$/, '').replace(/ (Station|Depot)$/, '');
  const raw = id.replace(/^(station|depot|route)-/, '');
  return raw === 'coxsbazar' ? "Cox's Bazar" : raw.replace(/(^|-)(\w)/g, (_, s: string, c: string) => (s ? ' ' : '') + c.toUpperCase());
}
export const routeName = (snap: NetworkSnapshot | null, routeId: string) => {
  const r = snap?.routes.find((x) => x.id === routeId);
  return r ? `${placeName(snap, r.source_depot_id)} → ${placeName(snap, r.destination_station_id)}` : routeId;
};

/** Simulated clock: "Day 2 · 00:30". */
export function simClock(snap: NetworkSnapshot | null): string {
  if (!snap) return '—';
  const mins = snap.tick * (snap.tick_minutes || 15);
  const day = Math.floor(mins / 1440) + 1;
  const hh = String(Math.floor((mins % 1440) / 60)).padStart(2, '0');
  const mm = String(mins % 60).padStart(2, '0');
  return `Day ${day} · ${hh}:${mm}`;
}

// ---- Recommendation shape: the fixture and the intelligence service fill different fields. ----
export function recLegs(rec: Recommendation): AllocationLeg[] {
  if (rec.legs?.length) return rec.legs;
  return rec.candidates?.find((c) => c.id === rec.selected_candidate_id)?.legs ?? [];
}
export const recFutures = (rec: Recommendation): TwinFuture[] => (rec.futures?.length ? rec.futures : rec.twin_futures ?? []);
export const recConstraints = (rec: Recommendation): string[] => (rec.constraints?.length ? rec.constraints : rec.constraints_applied ?? []);
export const recPolicy = (rec: Recommendation) => rec.versions?.policy || rec.policy || rec.selected_candidate_id;
export function chosenFuture(rec: Recommendation): TwinFuture | undefined {
  const fs = recFutures(rec);
  return fs.find((f) => f.candidate_id === rec.selected_candidate_id) ?? fs.find((f) => f.candidate_id === recPolicy(rec)) ?? fs[fs.length - 1];
}
export const noopFuture = (rec: Recommendation) => recFutures(rec).find((f) => f.candidate_id === 'noop') ?? recFutures(rec)[0];
export const futureLabel = (f: TwinFuture) => f.label || f.name || f.candidate_id;
export const futureNotes = (f: TwinFuture) => (Array.isArray(f.notes) ? f.notes : f.notes ? [f.notes] : []);
