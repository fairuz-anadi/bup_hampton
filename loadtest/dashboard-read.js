// Question: how many operators can watch Mission Control live before latency degrades,
// and does the snapshot cache keep the load off the simulator?
// What one "operator" does: poll state every second, health and decision history every few seconds.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { BASE } from './lib.js';

export const options = {
  scenarios: {
    operators: {
      executor: 'ramping-vus',
      startVUs: 10,
      stages: [
        { duration: __ENV.RAMP || '60s', target: Number(__ENV.VUS || 200) },
        { duration: __ENV.HOLD || '60s', target: Number(__ENV.VUS || 200) },
        { duration: '10s', target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    'http_req_duration{endpoint:state}': ['p(95)<500'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const r = http.get(`${BASE}/api/state`, { tags: { endpoint: 'state' } });
  check(r, { 'state 200': (res) => res.status === 200 });
  if (__ITER % 3 === 0) {
    const h = http.get(`${BASE}/api/health`, { tags: { endpoint: 'health' } });
    check(h, { 'health 200': (res) => res.status === 200 });
  }
  if (__ITER % 5 === 0) {
    const d = http.get(`${BASE}/api/decisions?limit=20`, { tags: { endpoint: 'decisions' } });
    check(d, { 'decisions 200': (res) => res.status === 200 });
  }
  sleep(1);
}
