// Question: how long from "operator approves" to a PENDING allocation in the official simulator,
// and do retries under load ever create a duplicate shipment?
// Each iteration registers a recommendation and approves it, which posts one small allocation.
// Every approval is sent twice (a retry) to prove idempotency. Runs on a freshly reset simulator.
import http from 'k6/http';
import { check } from 'k6';
import { Trend, Counter } from 'k6/metrics';
import { BASE, SIM, OPERATOR, JSON_HEADERS } from './lib.js';

const approveToSubmitted = new Trend('approve_to_submitted', true);
const accepted = new Counter('legs_accepted');
const blocked = new Counter('legs_blocked_by_precheck');

const ROUTES = [
  ['route-gazipur-mirpur', 'depot-gazipur', 'station-mirpur'],
  ['route-gazipur-tongi', 'depot-gazipur', 'station-tongi'],
  ['route-patiya-karnaphuli', 'depot-patiya', 'station-karnaphuli'],
  ['route-patiya-coxsbazar', 'depot-patiya', 'station-coxsbazar'],
];
const FUELS = ['DIESEL', 'PETROL', 'OCTANE'];

export const options = {
  scenarios: {
    approvers: {
      executor: 'ramping-vus', startVUs: 1,
      stages: [{ duration: '30s', target: Number(__ENV.VUS || 10) }, { duration: __ENV.HOLD || '60s', target: Number(__ENV.VUS || 10) }],
    },
  },
  thresholds: { http_req_failed: ['rate<0.01'], checks: ['rate>0.99'] },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export function setup() {
  http.post(`${SIM}/admin/faults/clear`, null, JSON_HEADERS);
  http.post(`${SIM}/admin/pause`, null, JSON_HEADERS);
  http.post(`${SIM}/admin/reset`, null, JSON_HEADERS);
  http.post(`${SIM}/admin/pause`, null, JSON_HEADERS);
  return { run: Date.now() };
}

export default function (data) {
  const [route, depot, station] = ROUTES[(__VU + __ITER) % ROUTES.length];
  const fuel = FUELS[__ITER % FUELS.length];
  const id = `lt-${data.run}-${__VU}-${__ITER}`;
  const leg = { route_id: route, source_depot_id: depot, station_id: station, fuel_type: fuel, quantity: 25 };
  const rec = {
    id, tick: 0, created_at: new Date().toISOString(), mode: 'prevention',
    candidates: [{ id: 'noop', policy: 'noop', legs: [] }, { id: 'lt', policy: 'loadtest', legs: [leg] }],
    selected_candidate_id: 'lt', futures: [], risks: [], signals: [], confidence: 0.9,
  };
  const created = http.post(`${BASE}/api/decisions`, JSON.stringify({ recommendation: rec }), { ...OPERATOR, tags: { endpoint: 'create' } });
  check(created, { 'created 201': (r) => r.status === 201 });

  const t0 = Date.now();
  const body = JSON.stringify({ by: 'k6', reason: 'load test' });
  const approved = http.post(`${BASE}/api/decisions/${id}/approve`, body, { ...OPERATOR, tags: { endpoint: 'approve' } });
  approveToSubmitted.add(Date.now() - t0);
  const ok = check(approved, { 'approved 200': (r) => r.status === 200 });
  if (ok) {
    const sub = approved.json('submissions.0');
    if (sub && sub.result === 'accepted') accepted.add(1);
    else blocked.add(1);
  }
  // Retry of the same approval must be refused, never posted twice.
  const retry = http.post(`${BASE}/api/decisions/${id}/approve`, body, { ...OPERATOR, tags: { endpoint: 'approve_retry' },
    responseCallback: http.expectedStatuses(409) });
  check(retry, { 'retry refused 409': (r) => r.status === 409 });
}

export function teardown(data) {
  const allocs = http.get(`${SIM}/v1/allocations`).json();
  const keys = allocs.filter((a) => a.idempotency_key.startsWith(`fg-lt-${data.run}`)).map((a) => a.idempotency_key);
  const dupes = keys.length - new Set(keys).size;
  console.log(`simulator allocations from this run: ${keys.length}, duplicate keys: ${dupes}`);
  check(dupes, { 'no duplicate shipments': (d) => d === 0 });
}
