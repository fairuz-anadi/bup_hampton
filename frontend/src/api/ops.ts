// Chaos Lab, pacer and policy (backend app/api/control_routes.py) plus copilot and scoreboard (app/decisions/routes.py).
import { call } from './client';
import type { ExplainResponse } from './types';
import type { EventType, FaultType } from '../lib/playbooks';

export interface PacerStatus { running: boolean; interval_ms: number; ticks_done: number; last_error: string | null }
export interface PolicyStatus { active: string; accepted: string; history: { from: string; to: string; by: string; accepted: boolean }[] }
export interface TimelineEvent { id: number; type: string; start_tick: number; end_tick: number; status: string; parameters: Record<string, unknown> }
export interface TimelineFault { id: number; type: string; active: boolean; start_wall_time?: string; end_wall_time?: string; duration_seconds?: number; parameters?: Record<string, unknown> }
export interface ChaosTimeline { events: TimelineEvent[]; faults: TimelineFault[] }
export interface CopilotInfo { engine: string; llm: string | null; tracing: boolean; project: string | null }
export interface ScoreRow {
  decision_id: string; tick: number; policy: string; by: string | null; projected_unmet_l: number;
  avoided_vs_noop_l: number | null; vs_baseline_l: number | null; twin_check: { predicted_l: number; actual_l: number; error_l: number } | null;
}
export interface Scoreboard {
  executed: number; projected_avoided_vs_noop_l: number; projected_vs_baseline_l: number; verified: number;
  mean_twin_error_l: number | null; rows: ScoreRow[]; note: string;
}

const post = (path: string, body?: unknown, timeout = 10000) =>
  call<unknown>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body), operator: true }, timeout);

export const ops = {
  injectEvent: (type: EventType, duration_ticks: number, parameters: Record<string, unknown>, start_in_ticks = 1) =>
    post('/api/chaos/events', { type, start_in_ticks, duration_ticks, parameters }),
  injectFault: (type: FaultType, duration_seconds: number, parameters: Record<string, unknown>) =>
    post('/api/chaos/faults', { type, duration_seconds, parameters }),
  clearFaults: () => post('/api/chaos/faults/clear'),
  sim: (action: 'pause' | 'run' | 'step' | 'reset') => post(`/api/chaos/sim/${action}`),
  forecaster: (action: 'disable' | 'exit', seconds = 60) => post(`/api/chaos/forecaster/${action}?seconds=${seconds}`),
  timeline: () => call<ChaosTimeline>('/api/chaos/timeline'),
  pacer: () => call<PacerStatus>('/api/pacer'),
  setPacer: (enabled: boolean, interval_ms = 1000) => post('/api/pacer', { enabled, interval_ms }) as Promise<PacerStatus>,
  policy: () => call<PolicyStatus>('/api/policy'),
  setPolicy: (policy: string, accept = false) =>
    call<PolicyStatus>('/api/policy', { method: 'PUT', body: JSON.stringify({ policy, accept, by: 'operator' }), operator: true }),
  rollbackPolicy: () => post('/api/policy/rollback') as Promise<PolicyStatus>,
  scoreboard: () => call<Scoreboard>('/api/scoreboard'),
  copilotInfo: () => call<CopilotInfo>('/api/copilot/info'),
  investigate: (station_id: string, question?: string) =>
    call<ExplainResponse>('/api/copilot/investigate', { method: 'POST', body: JSON.stringify({ station_id, question }) }, 20000),
  summary: (question?: string) =>
    call<ExplainResponse>('/api/copilot/summary', { method: 'POST', body: JSON.stringify({ question }) }, 20000),
  incidentReport: (from_tick?: number, to_tick?: number) => {
    const q = new URLSearchParams();
    if (from_tick != null) q.set('from_tick', String(from_tick));
    if (to_tick != null) q.set('to_tick', String(to_tick));
    return call<ExplainResponse>(`/api/copilot/incident-report?${q}`, {}, 20000);
  },
};
