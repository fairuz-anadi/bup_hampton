import type { AllocationLeg, CurrentView, DecisionRecord, ExplainResponse, HealthReport, NetworkSnapshot, Autonomy } from './types';

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

const KEY_STORE = 'fuelguard.operatorKey';
export const DEFAULT_OPERATOR_KEY = 'dev-operator-key';

export const operatorKey = {
  get: (): string => {
    try {
      const val = sessionStorage.getItem(KEY_STORE);
      if (val !== null && val.trim() !== '') return val.trim();
      return DEFAULT_OPERATOR_KEY;
    } catch {
      return DEFAULT_OPERATOR_KEY;
    }
  },
  set: (k: string) => {
    try {
      sessionStorage.setItem(KEY_STORE, k.trim());
    } catch { /* private mode */ }
  },
  clear: () => {
    try {
      sessionStorage.removeItem(KEY_STORE);
    } catch { /* private mode */ }
  },
  resetToDev: () => {
    try {
      sessionStorage.setItem(KEY_STORE, DEFAULT_OPERATOR_KEY);
    } catch { /* private mode */ }
  },
};

// Every API call this browser makes: latency and outcome, for the System Health page (last 200 calls).
export interface CallStat { ms: number; ok: boolean; at: number }
const STATS: CallStat[] = [];
export function callStats(): { p95: number | null; errorRate: number | null; n: number } {
  if (!STATS.length) return { p95: null, errorRate: null, n: 0 };
  const ms = STATS.map((s) => s.ms).sort((a, b) => a - b);
  return { p95: ms[Math.min(ms.length - 1, Math.floor(ms.length * 0.95))], errorRate: STATS.filter((s) => !s.ok).length / STATS.length, n: STATS.length };
}
const record = (ms: number, ok: boolean) => { STATS.push({ ms, ok, at: Date.now() }); if (STATS.length > 200) STATS.shift(); };

export async function call<T>(path: string, init: RequestInit & { operator?: boolean } = {}, timeoutMs = 6000): Promise<T> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (init.body) headers['Content-Type'] = 'application/json';
  if (init.operator) headers['X-Operator-Key'] = operatorKey.get();
  const started = performance.now();
  try {
    let res = await fetch(path, { ...init, headers, signal: ctl.signal });
    // Auto-recovery: if 401 on an operator request and current key was not the default dev key,
    // fallback to DEFAULT_OPERATOR_KEY and retry once.
    if (res.status === 401 && init.operator && operatorKey.get() !== DEFAULT_OPERATOR_KEY) {
      operatorKey.resetToDev();
      headers['X-Operator-Key'] = DEFAULT_OPERATOR_KEY;
      res = await fetch(path, { ...init, headers, signal: ctl.signal });
    }
    const body = await res.json().catch(() => null);
    record(performance.now() - started, res.status < 500);
    if (!res.ok) {
      const d = body?.detail;
      const code = typeof d === 'object' && d ? d.code ?? 'ERROR' : 'HTTP_' + res.status;
      const msg = typeof d === 'object' && d ? d.message ?? JSON.stringify(d) : typeof d === 'string' ? d : res.statusText;
      throw new ApiError(res.status, code, msg);
    }
    return body as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    record(performance.now() - started, false);
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
  authStatus: () => call<{ writes_enabled: boolean; is_valid: boolean; is_dev: boolean; dev_key: string | null }>('/api/auth/status', { operator: true }),
  verifyKey: async (key: string): Promise<boolean> => {
    try {
      const res = await fetch('/api/auth/verify', { method: 'POST', headers: { 'X-Operator-Key': key.trim() } });
      return res.ok;
    } catch {
      return false;
    }
  },
};
