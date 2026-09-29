import type { AllocationLeg, CurrentView, DecisionRecord, ExplainResponse, HealthReport, NetworkSnapshot, Autonomy } from './types';

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

const KEY_STORE = 'fuelguard.operatorKey';
export const operatorKey = {
  get: (): string => { try { return sessionStorage.getItem(KEY_STORE) ?? ''; } catch { return ''; } },
  set: (k: string) => { try { sessionStorage.setItem(KEY_STORE, k); } catch { /* private mode */ } },
};

export async function call<T>(path: string, init: RequestInit & { operator?: boolean } = {}, timeoutMs = 6000): Promise<T> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (init.body) headers['Content-Type'] = 'application/json';
  if (init.operator) headers['X-Operator-Key'] = operatorKey.get();
  try {
    const res = await fetch(path, { ...init, headers, signal: ctl.signal });
    const body = await res.json().catch(() => null);
    if (!res.ok) {
      const d = body?.detail;
      const code = typeof d === 'object' && d ? d.code ?? 'ERROR' : 'HTTP_' + res.status;
      const msg = typeof d === 'object' && d ? d.message ?? JSON.stringify(d) : typeof d === 'string' ? d : res.statusText;
      throw new ApiError(res.status, code, msg);
    }
    return body as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, 'UNREACHABLE', e instanceof Error && e.name === 'AbortError' ? 'Request timed out' : 'Backend unreachable');
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  state: () => call<NetworkSnapshot>('/api/state'),
  health: () => call<HealthReport>('/api/health'),
  current: () => call<CurrentView>('/api/recommendations/current', {}, 15000),
  decisions: (limit = 100) => call<DecisionRecord[]>(`/api/decisions?limit=${limit}`),
  demandHistory: (stationId: string, limit = 400) =>
    call<{ tick: number; fuel_type: string; demand_liters: number; unmet_liters: number }[]>(
      `/api/demand-history?limit=${limit}&station_id=${encodeURIComponent(stationId)}`),
  approve: (id: string, body: { by: string; reason: string; legs?: AllocationLeg[] }) =>
    call<DecisionRecord>(`/api/decisions/${encodeURIComponent(id)}/approve`, { method: 'POST', body: JSON.stringify(body), operator: true }, 20000),
  reject: (id: string, body: { by: string; reason: string }) =>
    call<DecisionRecord>(`/api/decisions/${encodeURIComponent(id)}/reject`, { method: 'POST', body: JSON.stringify(body), operator: true }),
  explain: (decision_id: string | null, question?: string) =>
    call<ExplainResponse>('/api/explain', { method: 'POST', body: JSON.stringify({ decision_id, question }) }, 20000),
  rearm: () => call<Autonomy>('/api/autonomy/rearm', { method: 'POST', operator: true }),
  setMode: (mode: 'MANUAL' | 'SUPERVISED') =>
    call<Autonomy>('/api/autonomy/mode', { method: 'POST', body: JSON.stringify({ mode }), operator: true }),
};
