FROM oven/bun:1 AS deps

WORKDIR /app
COPY ui/package.json ui/bun.lock* ./
RUN bun install --frozen-lockfile || bun install

FROM node:22-alpine AS builder

WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY ui/ .

# NEXT_PUBLIC_* are inlined into the client bundle at build time, so they must
# be present as env during `npm run build`. Passed as --build-arg by the cloud
# build (infra/terraform/cloudbuild.yaml). Defaults keep local dev builds
# working unchanged.
ARG NEXT_PUBLIC_API_URL=""
ARG NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=""
ARG NEXT_PUBLIC_CLERK_SIGN_IN_URL="/login"
ARG NEXT_PUBLIC_CLERK_SIGN_UP_URL="/sign-up"
ARG NEXT_PUBLIC_CLERK_JWT_TEMPLATE="redarch-km"
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY \
    NEXT_PUBLIC_CLERK_SIGN_IN_URL=$NEXT_PUBLIC_CLERK_SIGN_IN_URL \
    NEXT_PUBLIC_CLERK_SIGN_UP_URL=$NEXT_PUBLIC_CLERK_SIGN_UP_URL \
    NEXT_PUBLIC_CLERK_JWT_TEMPLATE=$NEXT_PUBLIC_CLERK_JWT_TEMPLATE

RUN npm run build

FROM node:22-alpine AS runner

WORKDIR /app

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000
ENV PORT=3000 HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
