# Next.js frontend.
#
# `output: "standalone"` in next.config.ts produces a self-contained server with
# only the files it actually needs, so the runtime stage carries no build
# tooling, no dev dependencies, and no source.

# ---------------------------------------------------------------------------
# Dependencies -- cached independently of source changes.
# ---------------------------------------------------------------------------
FROM node:20-bookworm-slim AS deps

WORKDIR /app

COPY web/package.json web/package-lock.json ./

# `npm ci` installs exactly the lockfile, and fails if package.json and the
# lockfile disagree. `npm install` would silently resolve something newer, so an
# image could ship a dependency set nobody tested.
RUN npm ci --no-audit --no-fund

# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
FROM node:20-bookworm-slim AS builder

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY web/ ./

ENV NEXT_TELEMETRY_DISABLED=1

# NEXT_PUBLIC_* values are inlined into the browser bundle at build time. ORBIT
# is single-origin (ADR-0009), so the API base is the relative path "/api" and
# there is nothing environment-specific -- and nothing secret -- to bake in.
RUN npm run build

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM node:20-bookworm-slim AS runtime

RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

WORKDIR /app

# node:20-slim ships a `node` user (uid 1000); reusing it avoids creating a
# redundant account just to drop privileges.
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public

USER node

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD curl --fail --silent --show-error http://localhost:3000/ || exit 1

CMD ["node", "server.js"]
