# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS deps
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN --mount=type=secret,id=build_ca \
    if [ -s /run/secrets/build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/build_ca npm_config_cafile=/run/secrets/build_ca; fi; \
    npm ci --no-audit --no-fund

FROM node:22-alpine AS builder
WORKDIR /app
ARG API_INTERNAL_URL=http://api:8000
ENV API_INTERNAL_URL=$API_INTERNAL_URL NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web ./
RUN rm -rf node_modules/.cache && npm run build

FROM node:22-alpine AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /app
RUN addgroup -S -g 10001 web && adduser -S -u 10001 -G web web
COPY --from=builder --chown=web:web /app/.next/standalone ./
COPY --from=builder --chown=web:web /app/.next/static ./.next/static
USER web
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=5 \
  CMD wget -qO- http://127.0.0.1:3000/login >/dev/null || exit 1
CMD ["node", "server.js"]
