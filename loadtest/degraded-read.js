// Question: while the simulator returns random 503s (error_rate 0.25), do our cached reads keep
// our own error rate near zero? Same traffic as dashboard-read, with a fault injected in setup().
import http from 'k6/http';
import { check, sleep } from 'k6';
import { BASE, SIM, JSON_HEADERS } from './lib.js';

const DURATION = Number(__ENV.FAULT_SECONDS || 180);

export const options = {
  scenarios: {
    operators: { executor: 'constant-vus', vus: Number(__ENV.VUS || 50), duration: __ENV.DURATION || '150s' },
  },
  thresholds: {
    // The simulator fails 25% of calls; we expect far fewer failures to reach operators.
    http_req_failed: ['rate<0.02'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export function setup() {
  const r = http.post(`${SIM}/admin/faults`, JSON.stringify({
    type: 'error_rate', duration_seconds: DURATION, parameters: { rate: 0.25 },
  }), JSON_HEADERS);
  check(r, { 'fault injected': (res) => res.status === 201 });
}

export default function () {
  const r = http.get(`${BASE}/api/state`, { tags: { endpoint: 'state' } });
  check(r, { 'state 200': (res) => res.status === 200 });
  if (__ITER % 3 === 0) {
    http.get(`${BASE}/api/health`, { tags: { endpoint: 'health' } });
  }
  sleep(1);
}

export function teardown() {
  http.post(`${SIM}/admin/faults/clear`, null, JSON_HEADERS);
}
