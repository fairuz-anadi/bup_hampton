// Crisis and fault presets for the Chaos Lab and the playbooks on the Crises screen.
// Payload shapes follow the simulator integration guide §7.7–7.10; reactions follow the blueprint (sections 07, 08, 11).

export type EventType = 'demand_spike' | 'route_disruption' | 'station_outage' | 'depot_constraint' | 'shipment_delay' | 'supply_shortfall';
export type FaultType = 'latency' | 'unavailable' | 'error_rate' | 'stale_data' | 'stream_disconnect';

export interface EventPreset {
  id: string; name: string; type: EventType; duration_ticks: number; parameters: Record<string, unknown>;
  steps: [string, string][]; // [label, what FuelGuard does]
}
export interface FaultPreset {
  id: string; name: string; type: FaultType; duration_seconds: number; parameters: Record<string, unknown>;
  steps: [string, string][];
}

export const EVENT_PRESETS: EventPreset[] = [
  { id: 'spike', name: 'Demand spike', type: 'demand_spike', duration_ticks: 12, parameters: { region_ids: ['region-dhaka'], multiplier: 1.8 },
    steps: [['Detect', 'Dhaka stations’ demand multiplier jumps to 1.8×; demand runs above forecast; the event is ACTIVE.'],
      ['Evaluate', 'Re-forecast with the new multiplier and recompute time to stockout for the region.'],
      ['Respond', 'Raise allocations to affected stations while keeping reserves for Chattogram. Confidence drops, so Autonomous falls to Supervised.'],
      ['Explain', 'The copilot names the spike, the station at risk and the shipment that covers it, with projected impact.'],
      ['Recover', 'Event resolves → multiplier back → confidence recovers → mode climbs back after 3 healthy ticks.']] },
  { id: 'route', name: 'Route disruption', type: 'route_disruption', duration_ticks: 16, parameters: { route_ids: ['route-gazipur-mirpur'] },
    steps: [['Detect', 'Route DISRUPTED. A shipment departing over it would FAIL and its fuel is not refunded.'],
      ['Evaluate', 'Does the destination have another route? Mirpur and Karnaphuli do; Tongi and Cox’s Bazar do not.'],
      ['Respond', 'Guardrails block the route. Re-plan over the 4-tick backup, or contain the shortage for single-route stations.'],
      ['Explain', '“Gazipur → Mirpur is closed; Patiya → Mirpur takes 4 ticks instead of 2, so fuel goes that way.”'],
      ['Recover', 'Route AVAILABLE → the shorter route is preferred again.']] },
  { id: 'outage', name: 'Station outage', type: 'station_outage', duration_ticks: 8, parameters: { station_ids: ['station-tongi'] },
    steps: [['Detect', 'Station OUTAGE; served demand drops to 0.'],
      ['Evaluate', 'No allocation can serve a closed station; its unmet demand is expected.'],
      ['Respond', 'Guardrails stop shipments to it; depot capacity goes to other stations.'],
      ['Explain', '“Tongi is closed, so its shortfall is expected; spare fuel goes to Mirpur instead.”'],
      ['Recover', 'Station OPEN → its refill is ranked first.']] },
  { id: 'depot', name: 'Depot constraint', type: 'depot_constraint', duration_ticks: 20, parameters: { depot_ids: ['depot-patiya'] },
    steps: [['Detect', 'Depot CONSTRAINED.'],
      ['Evaluate', 'Verified in hour one: CONSTRAINED is a label only; dispatch capacity is unchanged. Treated as a signal.'],
      ['Respond', 'Confidence drops a step (active crisis); plans still use Patiya where it is the only route.'],
      ['Explain', 'The copilot reports the constraint and why plans did not change.'],
      ['Recover', 'Depot OPEN → crisis factor back to normal.']] },
  { id: 'delay', name: 'Shipment delay', type: 'shipment_delay', duration_ticks: 1, parameters: { delay_ticks: 8, depot_ids: ['depot-gazipur'], fuel_types: ['PETROL'] },
    steps: [['Detect', 'A supply arrival turns DELAYED and moves later. One-shot: it does not undo itself.'],
      ['Evaluate', 'Re-project depot stock with the later arrival.'],
      ['Respond', 'Protect the depot reserve; send only what stations need until the supply lands.'],
      ['Explain', '“Gazipur petrol supply moved 8 ticks later; we keep a reserve until then.”'],
      ['Recover', 'Supply ARRIVED → reserve released.']] },
  { id: 'short', name: 'Supply shortfall', type: 'supply_shortfall', duration_ticks: 1, parameters: { factor: 0.5, depot_ids: ['depot-patiya'], fuel_types: ['DIESEL'] },
    steps: [['Detect', 'A scheduled supply quantity is halved. One-shot: it does not restore itself.'],
      ['Evaluate', 'Depot stock may no longer cover the region until the next delivery.'],
      ['Respond', 'If prevention is infeasible → containment: spread the shortage so no station runs dry first.'],
      ['Explain', 'The copilot explains why some unmet demand is unavoidable and how it is spread.'],
      ['Recover', 'Next delivery → normal optimization.']] },
];

export const FAULT_PRESETS: FaultPreset[] = [
  { id: 'latency', name: 'API latency', type: 'latency', duration_seconds: 60, parameters: { delay_ms: 800 },
    steps: [['Detect', 'Simulator calls slow down; per-call timeouts may fire.'], ['Explain', 'Health shows the simulator degraded.'],
      ['Adapt', 'UI reads cached state; decisions continue on the last good snapshot.'], ['Fallback', 'Circuit opens only if timeouts pile up.'],
      ['Recover', 'Fault expires → latency normal → health green.']] },
  { id: 'unavailable', name: 'API unavailable', type: 'unavailable', duration_seconds: 45, parameters: {},
    steps: [['Detect', '503 FAULT_INJECTED on /v1/* while /v1/health stays ok → degraded, not down.'], ['Explain', 'Banner: running on cached state, writes held.'],
      ['Adapt', 'Circuit opens after 5 failures; data goes stale; mode → Manual.'], ['Fallback', 'Cached snapshot with its age on every screen.'],
      ['Recover', 'Half-open probe passes → re-sync → mode steps back up one level at a time.']] },
  { id: 'error_rate', name: 'Random API errors', type: 'error_rate', duration_seconds: 90, parameters: { rate: 0.25 },
    steps: [['Detect', 'About 1 in 4 calls fail; retry counters climb.'], ['Explain', 'Health may show degraded.'],
      ['Adapt', 'Reads retried; POSTs retried with the same idempotency key, so no duplicate shipments.'], ['Fallback', 'Failed resources keep older values, marked with age.'],
      ['Recover', 'Fault expires → retries back to 0.']] },
  { id: 'stale_data', name: 'Stale data', type: 'stale_data', duration_seconds: 60, parameters: {},
    steps: [['Detect', 'X-Simulator-Stale: true on /v1/* reads.'], ['Explain', 'Red banner: data may be stale; recommend only.'],
      ['Adapt', 'Snapshot marked stale → freshness factor 0 → Manual mode; nothing executes.'], ['Fallback', 'Recommendations are shown but locked.'],
      ['Recover', 'Header disappears → fresh data → confidence recovers.']] },
  { id: 'stream_disconnect', name: 'Stream disconnect', type: 'stream_disconnect', duration_seconds: 60, parameters: {},
    steps: [['Detect', 'GET /v1/stream returns 503.'], ['Explain', 'Health: event stream degraded, polling REST.'],
      ['Adapt', 'Reconnect with backoff; REST polling continues (REST is the source of truth).'], ['Fallback', 'Polling.'],
      ['Recover', 'Stream back → full REST re-sync.']] },
];

export const FORECASTER_STEPS: [string, string][] = [
  ['Detect', 'Forecaster /health fails or calls time out.'], ['Explain', 'Health: forecaster down; explanations mention the fallback.'],
  ['Adapt', 'Forecast-fit and health factors drop; mode falls to Supervised.'], ['Fallback', 'In-process profile predictor keeps risk and the optimizer running.'],
  ['Recover', 'Forecaster healthy → primary forecasts resume.'],
];

export const playbookFor = (type: string) => EVENT_PRESETS.find((p) => p.type === type);
