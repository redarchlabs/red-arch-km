# Load tests

k6 scripts for smoke, soak, and stress testing the API and brain-api.

## Running

Install k6: https://k6.io/docs/get-started/installation/

```bash
# Smoke test (sanity check — 1 VU, 30s)
k6 run tests/load/search_smoke.js

# Ramp test (ramp from 1 → 50 VUs over 5 minutes)
k6 run tests/load/search_ramp.js

# Stress test (sustained 100 VUs for 10 minutes)
k6 run tests/load/chat_stress.js
```

## Environment variables

Every script reads:

| Variable | Default | Purpose |
|----------|---------|---------|
| `BASE_URL` | `http://localhost:8000` | API base URL |
| `AUTH_TOKEN` | (required for protected endpoints) | Bearer token |
| `ORG_ID` | (required for org-scoped endpoints) | X-Org-ID header |

## Performance budgets

These thresholds are enforced by k6 — failing a threshold fails the run:

- **Search**: p95 < 500ms, p99 < 1500ms, error rate < 1%
- **Chat streaming**: time-to-first-byte p95 < 2s, completion p95 < 10s
- **Document ingest enqueue** (API side, not brain-api): p95 < 300ms

## CI integration

Smoke tests run on every PR against a staging deployment. Ramp and stress
tests run nightly against staging with results posted to Grafana.
