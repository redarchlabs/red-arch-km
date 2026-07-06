# Deployment

This guide covers production deployment of Red Arch Knowledge Management Platform.

## Prerequisites

- Docker and Docker Compose v2
- PostgreSQL 18 (or managed PostgreSQL service)
- Redis 7.x
- Qdrant 1.12+
- Neo4j 5.25+
- Clerk account with application configured
- OpenAI API key

## Production Architecture

```
                    ┌────────────┐
                    │   Nginx    │
                    │ (ingress)  │
                    └─────┬──────┘
                          │
          ┌───────────────┼───────────────┐
          │               │               │
    ┌─────▼─────┐   ┌─────▼─────┐   ┌─────▼─────┐
    │    UI     │   │    API    │   │ Brain API │
    │ (replicas)│   │ (replicas)│   │ (replicas)│
    └───────────┘   └─────┬─────┘   └─────┬─────┘
                          │               │
    ┌─────────────────────┴───────────────┤
    │                                     │
┌───▼───┐ ┌────────┐ ┌───────┐ ┌──────┐ ┌▼─────┐
│Postgres│ │ Redis  │ │Qdrant │ │Neo4j │ │Celery│
│  (HA)  │ │(cluster)│ │(cluster)│ │(cluster)│ │Workers│
└────────┘ └────────┘ └───────┘ └──────┘ └──────┘
```

## Environment Configuration

Copy `.env.example` to `.env` and configure:

### Required Variables

```bash
# PostgreSQL
POSTGRES_USER=redarch
POSTGRES_PASSWORD=<strong-password>
POSTGRES_DB=redarch_km
DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}

# Redis
REDIS_URL=redis://redis:6379/0

# Qdrant
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=<api-key>  # Required for Qdrant Cloud

# Neo4j
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<strong-password>

# OpenAI
OPENAI_API_KEY=<your-api-key>
OPENAI_CHAT_MODEL=gpt-5-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# Clerk
CLERK_SECRET_KEY=<sk_...>
CLERK_JWT_ISSUER=https://<your-clerk-instance>.clerk.accounts.com
# REQUIRED whenever Clerk is enabled: comma-separated allowlist of UI origins.
# Clerk session tokens carry no `aud`, so the backend enforces `azp` against this
# list instead; startup FAILS if it is unset. Match the UI Origin byte-for-byte
# (scheme+host, no trailing slash, include the port only if the origin has one).
CLERK_ALLOWED_AZP=https://app.yourdomain.com
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=<pk_...>
NEXT_PUBLIC_CLERK_SIGN_IN_URL=/login
NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up
NEXT_PUBLIC_CLERK_JWT_TEMPLATE=redarch-km

# API
API_SECRET_KEY=<random-256-bit-key>
API_DEBUG=false
API_CORS_ORIGINS=["https://app.yourdomain.com"]

# Brain API
BRAIN_API_KEY=<shared-secret>

# Internal API (worker callbacks)
INTERNAL_API_KEY=<separate-shared-secret>
API_URL=http://api:8000

# Celery
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1

# Object storage (MinIO / S3-compatible) — stores uploaded document originals
STORAGE_ENDPOINT=http://minio:9000        # S3 API URL. NEVER use a host with an
                                          # underscore (e.g. "km2_minio") — botocore
                                          # rejects it as an invalid endpoint.
STORAGE_ACCESS_KEY=<access-key>
STORAGE_SECRET_KEY=<strong-secret>
STORAGE_BUCKET=km-documents               # Auto-created by the createbuckets init container
STORAGE_REGION=us-east-1
MAX_FILE_SIZE_MB=50
```

### Object Storage (MinIO / S3)

Uploaded document originals (PDF, images, `.docx`, `.md`, `.txt`) are kept in an
S3-compatible bucket: the API writes the original on upload, the worker downloads
it for OCR/extraction, and the reader serves it back (text inline, PDFs/images via
a short-lived presigned URL).

- **Bucket creation is automatic.** `docker/docker-compose.infra.yml` includes a
  one-shot `createbuckets` container that creates `STORAGE_BUCKET` once MinIO is
  healthy. It is idempotent and safe to re-run on every `up`. In production
  against managed S3, create the bucket via your provider instead.
- **Endpoint hostname:** containers reach storage at `http://minio:9000`; host
  processes (the hybrid dev layout) use `http://localhost:9000`. Do **not** use a
  hostname containing an underscore — botocore raises `Invalid endpoint`.
- **Binaries for extraction** live in the worker image: `tesseract-ocr` +
  `poppler-utils` (PDF/image OCR) and `mammoth` (`.docx` → Markdown).
- **Backup:** the bucket holds the only copy of uploaded originals — include it
  in backups (`mc mirror` or your provider's replication).

### Email (SMTP) Configuration

Intake forms send email invitations via SMTP. Email is disabled by default (dev/test); enable by setting both
`SMTP_HOST` and `SMTP_FROM`:

```bash
# Gmail (using App Password or OAuth2 token)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=<app-password>
SMTP_FROM=your-email@gmail.com
SMTP_USE_TLS=true

# SendGrid
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USERNAME=apikey
SMTP_PASSWORD=<sendgrid-api-key>
SMTP_FROM=noreply@yourdomain.com
SMTP_USE_TLS=true

# AWS SES
SMTP_HOST=email-smtp.<region>.amazonaws.com
SMTP_PORT=587
SMTP_USERNAME=<iam-smtp-user>
SMTP_PASSWORD=<iam-smtp-password>
SMTP_FROM=noreply@yourdomain.com
SMTP_USE_TLS=true
```

- If `SMTP_HOST` is empty, email sending is silently disabled (safe for dev).
- Recipient emails in form links are validated; malformed addresses are rejected (400).

### Encryption for Per-Org Secrets

Per-org OpenAI API keys are encrypted at rest using Fernet (symmetric encryption):

```bash
# Generate a 32-byte random key
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Set in .env
ORG_ENCRYPTION_KEY=<32-byte-random-string>
```

**In production, this is MANDATORY.** A dev default keeps local/test environments working but logs a warning at startup.
Existing keys are encrypted by migration 016 on upgrade; the migration is idempotent.

### Workflow Webhooks (SSRF Guard)

Workflows can send data to external webhooks via the `send_webhook` action. To prevent SSRF attacks, only allowlisted hosts
are accepted:

```bash
# Comma-separated list of allowed webhook hosts (no scheme/path)
WORKFLOW_WEBHOOK_ALLOWLIST=api.slack.com,webhooks.example.com,example-webhook-provider.com

# Empty or unset = webhooks disabled
WORKFLOW_WEBHOOK_ALLOWLIST=
```

### Celery Beat (Workflow Scheduling)

Workflows use a poll-based dispatch model: Celery Beat periodically sweeps the `workflow_outbox` table for pending events
and processes them. Without Celery Beat running, workflows do not fire.

**Start Celery Beat** (in production):

```bash
# Via docker-compose
docker compose -f docker-compose.prod.yml up -d beat

# Or standalone
celery -A worker.celery_app beat --loglevel=info
```

Beat calls two internal endpoints every minute (by default):
- `POST /api/internal/workflows/dispatch-batch` — Process pending change-triggered workflows
- `POST /api/internal/workflows/run-timers` — Resume due delayed runs and fire due scheduled workflows

**Partition Maintenance:**

The workflow tables (`workflow_outbox`, `workflow_runs`, `workflow_run_steps`) are RANGE-partitioned by `created_at` with
monthly boundaries. Partitions are pre-created by the `workflow_ensure_partitions(months_ahead)` PL/pgSQL function; Celery Beat
calls `POST /api/internal/workflows/maintain-partitions?months_ahead=2` once per day (configurable) to pre-create partitions
2 months ahead of the current date.

**Backfill existing partitions** (after upgrade):

```bash
# Manually, via psql
SELECT workflow_ensure_partitions(2);

# Or via internal endpoint (requires INTERNAL_API_KEY)
curl -X POST \
  -H "X-Internal-API-Key: ${INTERNAL_API_KEY}" \
  http://api:8000/api/internal/workflows/maintain-partitions?months_ahead=2
```

### Internal API Key

The worker communicates with the API via an internal, service-to-service key separate from `BRAIN_API_KEY`:

```bash
# Generate a strong key
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Set in .env
INTERNAL_API_KEY=<32-byte-random-string>
```

The worker passes this as an `X-Internal-API-Key` header on callbacks (document status, workflow dispatch, etc.).
Without it, the worker cannot report processing results.

## Docker Compose Production

Use `docker/docker-compose.prod.yml`:

```bash
cd docker
docker compose -f docker-compose.prod.yml up -d
```

### Production Compose Differences

| Feature | Dev | Prod |
|---------|-----|------|
| Source mounts | Yes | No |
| Hot reload | Yes | No |
| Debug mode | Yes | No |
| Resource limits | No | Yes |
| Health checks | Basic | Strict |
| Replicas | 1 | Configurable |

## Database Setup

### 1. Run Migrations

```bash
# Using docker
docker compose exec api alembic upgrade head

# Or directly
cd services/api
alembic upgrade head
```

### 2. Initialize RLS Roles

The `init-db.sql` script creates:
- `app_user` role (RLS-enforced)
- `app_admin` role (BYPASSRLS)

For managed PostgreSQL, run manually:

```sql
-- Create roles
CREATE ROLE app_user LOGIN PASSWORD 'changeme';
CREATE ROLE app_admin LOGIN PASSWORD 'changeme' BYPASSRLS;

-- Grant permissions
GRANT CONNECT ON DATABASE redarch_km TO app_user;
GRANT ALL PRIVILEGES ON DATABASE redarch_km TO app_admin;
```

### 3. Create Initial Admin (first-run setup wizard)

The first site admin is claimed through the built-in setup wizard — no manual
SQL needed:

1. Start the API with no site admin in the database. It prints a one-time
   **setup token** to its logs (`docker logs km2_api` or the uvicorn console):

   ```
   ========================================================================
     FIRST-RUN SETUP: no site admin exists yet.
     One-time setup token (valid 24h, single use):

         <token>

     Sign in at the UI and open /setup to claim global admin.
   ========================================================================
   ```

2. Sign in to the UI with the Clerk account that should become the global
   administrator; you are redirected to `/setup`.
3. Paste the token. Your account gets `is_site_admin = true` and the wizard
   walks you through creating the first organization.

The token is stored only as a SHA-256 hash in Redis, is single-use, and
expires after `API_SETUP_TOKEN_TTL_SECONDS` (default 86400). An unclaimed
token survives API restarts (so a copied token stays valid); to force a
reissue, delete the `setup:token:hash` Redis key (or wait out the TTL) and
restart the API. Once a site admin exists the wizard is disabled
(`POST /api/setup/claim` returns 409).

> **Log-shipping caveat:** the plaintext token is printed to the API's
> stdout logs. If your logs ship to a centralized aggregator, everyone with
> read access there can see it for up to the TTL — shorten
> `API_SETUP_TOKEN_TTL_SECONDS` for production bootstraps or redact this
> WARNING line in the shipping pipeline.

Fallback (broken Redis, air-gapped debugging) — flip the flag directly:

```sql
UPDATE user_profiles SET is_site_admin = true
WHERE auth_subject = '<clerk-sub-from-token>';
```

## Clerk Configuration

### 1. Create Application in Clerk Dashboard

- Sign in to Clerk Dashboard (https://dashboard.clerk.com)
- Create a new application
- Choose authentication methods: Email + Password, Google, etc.

### 2. Configure Sign-In and Sign-Up URLs

In the Clerk Dashboard, set:
- Sign-in URL: `https://app.yourdomain.com/login`
- Sign-up URL: `https://app.yourdomain.com/sign-up`

### 3. Create JWT Template

Create a JWT template named `redarch-km` that emits:
```json
{
  "email": "{{user.primary_email_address}}",
  "email_verified": "{{user.email_verified}}",
  "username": "{{user.username}}"
}
```

> **Critical:** the `email_verified` shortcode MUST be exactly `{{user.email_verified}}`
> (verified live to emit a boolean). `{{user.primary_email_address_verified}}` is **not**
> a valid Clerk shortcode — it renders as a literal string, so `email_verified` never
> equals `true` and the backend 403-locks out every migrated user on first Clerk login.
> The template must emit `email`, `email_verified`, **and** `username`: omitting `email`
> causes silent membership loss; omitting `email_verified` blocks the verified-email relink.

### 4. Configure Keys and Secrets

Retrieve from Clerk Dashboard:
- **Publishable Key** (`pk_...`): Used by frontend
- **Secret Key** (`sk_...`): Used by backend for token validation
- **JWT Issuer**: Your Clerk Frontend API URL (e.g., `https://your-instance.clerk.accounts.com`)

### 5. User Management

Users sign up/sign in directly via Clerk. On first API login, profiles are auto-created from the Clerk JWT.

## Scaling

### Horizontal Scaling

Scale stateless services:

```bash
docker compose up -d --scale api=3 --scale brain-api=2 --scale worker=4
```

Or with Kubernetes:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  replicas: 3
  # ...
```

### Infrastructure Scaling

| Component | Scaling Strategy |
|-----------|------------------|
| PostgreSQL | Read replicas, connection pooling (PgBouncer) |
| Redis | Redis Cluster or Redis Sentinel |
| Qdrant | Distributed mode with sharding |
| Neo4j | Causal clustering |

## TLS/SSL

### Nginx Configuration

```nginx
server {
    listen 443 ssl http2;
    server_name app.yourdomain.com;

    ssl_certificate /etc/ssl/certs/app.crt;
    ssl_certificate_key /etc/ssl/private/app.key;

    location / {
        proxy_pass http://ui:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /api/ {
        proxy_pass http://api:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### Internal TLS

For production, configure TLS between services:

```bash
# PostgreSQL
POSTGRES_SSL_MODE=verify-full

# Redis
REDIS_URL=rediss://...

# Neo4j
NEO4J_URI=bolt+s://...
```

## Monitoring

### Health Checks

All services expose `/healthz`:

```bash
curl http://api:8000/healthz
curl http://brain-api:8020/healthz
```

### Metrics

Configure OpenTelemetry exporter:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_SERVICE_NAME=red-arch-km
```

Recommended stack:
- OpenTelemetry Collector
- Prometheus (metrics)
- Jaeger/Tempo (traces)
- Grafana (dashboards)

### Logging

JSON structured logging to stdout. Aggregate with:
- Fluentd/Fluent Bit
- Loki
- Elasticsearch

Log level configuration:

```bash
LOG_LEVEL=INFO  # DEBUG, INFO, WARNING, ERROR
```

## Backup & Recovery

### PostgreSQL

```bash
# Backup
pg_dump -h postgres -U redarch -d redarch_km | gzip > backup-$(date +%Y%m%d).sql.gz

# Restore
gunzip -c backup.sql.gz | psql -h postgres -U redarch -d redarch_km
```

For production, use:
- WAL archiving for point-in-time recovery
- Managed backup (AWS RDS, Google Cloud SQL)

### Qdrant

Qdrant snapshots:

```bash
curl -X POST 'http://qdrant:6333/collections/{collection}/snapshots'
```

### Neo4j

Neo4j backup:

```bash
neo4j-admin database dump neo4j --to-path=/backups
```

## Security Checklist

- [ ] All secrets in environment variables, not files
- [ ] Database passwords are strong (32+ chars)
- [ ] API keys rotated regularly
- [ ] TLS enabled for all external traffic
- [ ] Network policies restrict inter-service traffic
- [ ] RLS enabled on all tenant tables
- [ ] Rate limiting configured
- [ ] CORS restricted to allowed origins
- [ ] Clerk application hardened (sign-in method restrictions)
- [ ] Regular security updates applied

## Troubleshooting

### Database Connection Issues

```bash
# Check connectivity
docker compose exec api python -c "
from api.db import engine
import asyncio
asyncio.run(engine.connect())
"
```

### Qdrant/Neo4j Not Responding

```bash
# Check brain-api health
curl http://brain-api:8020/healthz

# Check service logs
docker compose logs brain-api
```

### Celery Tasks Not Processing

```bash
# Check worker logs
docker compose logs worker

# Check Redis connectivity
docker compose exec worker redis-cli -u $REDIS_URL ping
```

### Document Ingestion Failing

1. Check document status in API: `GET /documents/{id}`
2. Check worker logs for task errors
3. Check brain-api logs for ingestion errors
4. Verify OpenAI API key is valid
