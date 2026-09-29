// Question: where does the decision engine saturate (detection, forecast, LP, Twin), and how long does
// one full recommendation take under concurrent requests? Dry run: nothing is registered or sent.
// Runs one recommendation at a time inside the backend (the engine is serialized), so this measures
// queueing as well as compute.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { BASE } from './lib.js';

export const options = {
  scenarios: {
    planners: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: __ENV.RAMP || '60s', target: Number(__ENV.VUS || 50) },
        { duration: __ENV.HOLD || '60s', target: Number(__ENV.VUS || 50) },
      ],
    },
  },
  thresholds: { http_req_failed: ['rate<0.01'] },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const r = http.post(`${BASE}/api/recommendations`, null, { tags: { endpoint: 'recommend' }, timeout: '30s' });
  check(r, {
    'recommendation 200': (res) => res.status === 200,
    'has three futures': (res) => res.status === 200 && res.json('futures').length === 3,
  });
  sleep(Number(__ENV.THINK || 1));
}
