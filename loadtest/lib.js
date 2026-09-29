// Shared settings for the k6 workloads. BASE and KEY come from -e flags (see scripts/run_loadtest.py).
export const BASE = __ENV.BASE || 'http://localhost:8080';
export const SIM = __ENV.SIM || 'http://localhost:8000';
export const KEY = __ENV.KEY || 'local-dev-key';
export const JSON_HEADERS = { headers: { 'Content-Type': 'application/json' } };
export const OPERATOR = { headers: { 'Content-Type': 'application/json', 'X-Operator-Key': KEY } };
export const SUMMARY_TREND_STATS = ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'];
