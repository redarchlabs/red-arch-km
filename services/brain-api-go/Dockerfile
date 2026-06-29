# Build stage
FROM golang:1.22-alpine AS builder

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
RUN cd services/brain-api-go && go mod download

# Copy source code
COPY packages/ packages/
COPY services/brain-api-go/ services/brain-api-go/

# Build the binary
WORKDIR /build/services/brain-api-go
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-w -s" -o /brain-api ./cmd/server

# Runtime stage
FROM alpine:3.20

# Install runtime dependencies
RUN apk add --no-cache ca-certificates tzdata

# Create non-root user
RUN adduser -D -u 1000 appuser
USER appuser

# Copy binary
COPY --from=builder /brain-api /brain-api

EXPOSE 8020

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:8020/healthz || exit 1

CMD ["/brain-api"]
