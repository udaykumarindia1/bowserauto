# syntax=docker/dockerfile:1.6
# Next.js (standalone output) + the single-origin proxy in server.mjs.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    NEXT_PORT=3001 \
    BACKEND_URL=http://backend:8000
# http-proxy is the only runtime dependency of server.mjs that the standalone bundle does not trace.
RUN npm install --omit=dev --no-audit --no-fund --no-package-lock http-proxy@^1.18.1 \
    && addgroup -S app && adduser -S app -G app
COPY --from=builder --chown=app:app /app/.next/standalone ./
COPY --from=builder --chown=app:app /app/.next/static ./.next/static
COPY --from=builder --chown=app:app /app/public ./public
COPY --from=builder --chown=app:app /app/server.mjs ./server.mjs
USER app
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD wget -qO- http://127.0.0.1:3000/login >/dev/null 2>&1 || exit 1
CMD ["node", "server.mjs"]
