// Smoke test for the search endpoint.
// 1 VU, 30 seconds — just confirms the endpoint works and stays under
// the p95 budget during low load.

import http from "k6/http";
import { check } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const AUTH_TOKEN = __ENV.AUTH_TOKEN;
const ORG_ID = __ENV.ORG_ID;

if (!AUTH_TOKEN || !ORG_ID) {
  throw new Error("AUTH_TOKEN and ORG_ID environment variables are required");
}

const QUERIES = [
  "quarterly revenue",
  "security incident",
  "engineering onboarding",
  "customer support escalation",
  "compliance checklist",
];

export const options = {
  vus: 1,
  duration: "30s",
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<500", "p(99)<1500"],
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
    }
  );

  check(response, {
    "status is 200": (r) => r.status === 200,
    "response has hits array": (r) => {
      try {
        return Array.isArray(r.json("hits"));
      } catch {
        return false;
      }
    },
  });
}
