// Stress test for RAG chat — sustained 100 VUs for 10 minutes.
//
// Chat is the most expensive endpoint: vector search + graph fuzzy
// search + LLM call. This script tests the non-streaming endpoint
// (/api/search/chat) so we can measure full-response latency; the
// streaming endpoint is tested separately in CI.
//
// NOTE: without per-org rate limiting this will rack up OpenAI spend
// fast. Use a scoped API key and a dedicated test tenant.

import http from "k6/http";
import { check, sleep } from "k6";
import { Trend } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const AUTH_TOKEN = __ENV.AUTH_TOKEN;
const ORG_ID = __ENV.ORG_ID;

if (!AUTH_TOKEN || !ORG_ID) {
  throw new Error("AUTH_TOKEN and ORG_ID environment variables are required");
}

const chatLatency = new Trend("chat_latency_ms");

const QUERIES = [
  "What are the main risks in the engineering roadmap?",
  "Summarize the last incident response report.",
  "What is the onboarding process for new hires?",
  "Which vendors require security review this quarter?",
];

export const options = {
  stages: [
    { duration: "2m", target: 100 },
    { duration: "6m", target: 100 },
    { duration: "2m", target: 0 },
  ],
  thresholds: {
    http_req_failed: ["rate<0.05"],
    chat_latency_ms: ["p(95)<10000", "p(99)<30000"],
  },
};

export default function () {
  const query = QUERIES[Math.floor(Math.random() * QUERIES.length)];
  const start = Date.now();
  const response = http.post(
    `${BASE_URL}/api/search/chat`,
    JSON.stringify({ query, tags: [], use_knowledge_graph: true }),
    {
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${AUTH_TOKEN}`,
        "X-Org-ID": ORG_ID,
      },
      timeout: "60s",
    }
  );
  chatLatency.add(Date.now() - start);

  check(response, {
    "status is 200": (r) => r.status === 200,
    "answer present": (r) => {
      try {
        return typeof r.json("answer") === "string";
      } catch {
        return false;
      }
    },
  });

  sleep(Math.random() * 3 + 2); // 2–5s think time
}
