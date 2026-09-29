// Plain-language copy for the operator UI. Engine ids and raw numbers stay available as "technical" labels;
// these helpers only translate, they never invent values.
import type { CurrentView, NetworkSnapshot } from '../api/types';
import type { Tone } from '../components/ui';

/** Engine candidate id -> the name a first-time user sees. */
export function planName(id: string): string {
  const k = id.toLowerCase();
  if (k === 'noop' || k.startsWith('no-op')) return 'Do nothing';
  if (k.startsWith('greedy')) return 'Priority-based plan';
  if (k.startsWith('lp')) return 'Optimized plan';
  if (k.startsWith('containment')) return 'Shortage-sharing plan';
  return id;
}

/** "greedy-v1" -> "Greedy-v1", shown small next to the plain name. */
export const techName = (id: string) => (id === 'noop' ? 'No-op' : id.replace(/^greedy/i, 'Greedy').replace(/^lp/i, 'LP'));

/** 6.2 -> "6 hours", 1.4 -> "1.5 hours", 0.5 -> "30 min". Rounded on purpose: these are forecasts. */
export function approxHours(h: number): string {
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min`;
  const r = h < 10 ? Math.round(h * 2) / 2 : Math.round(h);
  return `${r} hour${r === 1 ? '' : 's'}`;
}

export const tickMinutes = (snap: NetworkSnapshot | null) => snap?.tick_minutes || 15;

/** 7 -> "7 simulation steps". */
export const stepsLabel = (n: number) => `${n} simulation step${n === 1 ? '' : 's'}`;

/** 7 -> "7 simulation steps (about 2 hours)", using the simulator's own minutes per step. */
export const stepsWithTime = (n: number, snap: NetworkSnapshot | null) =>
  `${stepsLabel(n)} (about ${approxHours((n * tickMinutes(snap)) / 60)})`;

/** A Twin horizon in ticks -> "the next 6 hours". */
export const horizonLabel = (ticks: number, snap: NetworkSnapshot | null) => `the next ${approxHours((ticks * tickMinutes(snap)) / 60)}`;

// ---------------------------------------------------------------- who is in control

export interface Control { label: string; line: string; tone: Tone; confidence: number | null }

/** The human-control badge: one plain label instead of internal mode names. */
export function control(cur: CurrentView | null): Control {
  const a = cur?.autonomy;
  if (!a) return { label: 'Human-supervised', line: 'Every shipment is reviewed by an operator.', tone: 'idle', confidence: null };
  const conf = cur?.gate?.confidence ?? a.confidence;
  const stale = (a.factors.find((f) => f.key === 'fresh')?.value ?? 1) < 0.5;
  if (a.mode === 'MANUAL') {
    return { label: 'Human review required', tone: 'warn', confidence: conf,
      line: stale ? 'Simulator data is out of date, so FuelGuard only recommends. An operator must review every action.'
        : 'FuelGuard is not confident enough to act without operator review.' };
  }
  if (a.mode === 'SUPERVISED') return { label: 'Human-supervised', tone: 'idle', confidence: conf, line: 'FuelGuard has produced a recommendation for operator review.' };
  return { label: 'Automatic within limits', tone: 'ok', confidence: conf,
    line: 'Confidence is high: routine shipments may be sent automatically inside safety limits. Anything larger still goes to an operator.' };
}

// ---------------------------------------------------------------- events

const EVENT_TITLE: Record<string, string> = {
  demand_spike: 'Demand surge', station_outage: 'Station unavailable', route_disruption: 'Route closed',
  depot_constraint: 'Depot constrained', shipment_delay: 'Shipment delayed', supply_shortfall: 'Supply shortfall',
};
export const eventTitle = (type: string) => EVENT_TITLE[type] ?? type.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

const FAULT_TITLE: Record<string, string> = {
  stream_disconnect: 'Data connection lost', stale_data: 'Stale simulator data', unavailable: 'Simulator unavailable',
  error_rate: 'Random simulator errors', latency: 'Slow simulator',
};
export const faultTitle = (type: string) => FAULT_TITLE[type] ?? type.replace(/_/g, ' ');
