# Build stage
FROM golang:1.25-alpine AS builder

WORKDIR /build

# Install build dependencies
RUN apk add --no-cache git

# Copy go.work and module files first for better caching
COPY go.work go.work.sum* ./
COPY packages/accessmask/go.mod packages/accessmask/go.sum* ./packages/accessmask/
COPY packages/shared/go.mod packages/shared/go.sum* ./packages/shared/
COPY services/api-go/go.mod services/api-go/go.sum* ./services/api-go/
COPY services/brain-api-go/go.mod services/brain-api-go/go.sum* ./services/brain-api-go/
COPY services/worker-go/go.mod services/worker-go/go.sum* ./services/worker-go/

# Download dependencies
RUN cd services/api-go && go mod download

# Copy source code
COPY packages/ packages/
COPY services/api-go/ services/api-go/

# Build the binary
WORKDIR /build/services/api-go
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-w -s" -o /api ./cmd/server

# Runtime stage
FROM alpine:3.20

# Install runtime dependencies
RUN apk add --no-cache ca-certificates tzdata

# Create non-root user
RUN adduser -D -u 1000 appuser
USER appuser

# Copy binary and migrations
COPY --from=builder /api /api
COPY --from=builder /build/services/api-go/migrations /migrations

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:8000/healthz || exit 1

CMD ["/api"]
