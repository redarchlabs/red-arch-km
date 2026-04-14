// Ramp test — 1 → 50 VUs over 5 minutes to find the knee of the latency curve.
// Catches issues that only surface under parallelism: connection pool
// exhaustion, LLM rate limits, Qdrant timeouts.

import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const AUTH_TOKEN = __ENV.AUTH_TOKEN;
const ORG_ID = __ENV.ORG_ID;

if (!AUTH_TOKEN || !ORG_ID) {
  throw new Error("AUTH_TOKEN and ORG_ID environment variables are required");
}

const QUERIES = [
  "quarterly revenue targets",
  "incident response procedure",
  "new hire checklist",
  "legal contract review",
  "vendor security assessment",
  "product roadmap q2",
  "customer churn analysis",
  "engineering hiring pipeline",
];

export const options = {
  stages: [
    { duration: "1m", target: 10 },
    { duration: "2m", target: 30 },
    { duration: "2m", target: 50 },
    { duration: "1m", target: 0 },
  ],
  thresholds: {
    http_req_failed: ["rate<0.02"],
    http_req_duration: ["p(95)<800", "p(99)<2500"],
  },
};

export default function () {
  const query = QUERIES[Math.floor(Math.random() * QUERIES.length)];
  const response = http.post(
    `${BASE_URL}/api/search/`,
    JSON.stringify({ query, limit: 5, tags: [] }),
    {
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${AUTH_TOKEN}`,
        "X-Org-ID": ORG_ID,
      },
      tags: { endpoint: "search" },
    }
  );

  check(response, { "status is 200": (r) => r.status === 200 });
  sleep(Math.random() * 2 + 0.5); // 0.5–2.5s think time
}
