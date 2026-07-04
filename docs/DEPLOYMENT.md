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
```

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

### 3. Create Initial Admin

```sql
INSERT INTO user_profiles (id, keycloak_sub, username, email, is_site_admin)
VALUES (
  gen_random_uuid(),
  '<clerk-sub-from-token>',
  'admin',
  'admin@yourdomain.com',
  true
);
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
  "email_verified": "{{user.primary_email_address_verified}}",
  "username": "{{user.username}}"
}
```

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
