# syntax=docker/dockerfile:1
# Shared Next.js image, built by .github/workflows/app.yml. Contract: dockerfiles/README.md.
# Stages: deps -> build -> runtime-base -> runtime-plain | runtime-dotenvx. The workflow picks the
# last one with `target:`. No lint, test or typecheck here: `ci / verify` gates the commit.
FROM node:24.22.3-alpine AS deps
# The contract's `node:` must match this image's major version (a FROM tag cannot be an ARG and stay
# exact-pinned), so a mismatch fails the build instead of silently using another Node.
ARG NODE=24
RUN got="$(node -p "process.versions.node.split('.')[0]")" \
 && if [ "$got" != "$NODE" ]; then echo "contract node: $NODE but this image has Node $got" >&2; exit 1; fi
RUN apk add --no-cache libc6-compat ca-certificates
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
# The private-registry token is a BuildKit secret, never an ARG (build logs render ARGs in clear).
# A public-deps build has no secret file, so it is optional.
RUN --mount=type=secret,id=npm_token \
    if [ -f /run/secrets/npm_token ]; then NPM_TOKEN="$(cat /run/secrets/npm_token)"; export NPM_TOKEN; fi; \
    npm ci

FROM node:24.22.3-alpine AS build
ARG PRISMA=false
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
RUN apk add --no-cache libc6-compat ca-certificates
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Prisma layout: generator `prisma-client` with output "../generated/prisma" (see README).
RUN if [ "$PRISMA" = "true" ]; then npx prisma generate; fi \
 && mkdir -p generated public
RUN npm run build

FROM node:24.22.3-alpine AS runtime-base
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=80 HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
COPY --from=build --chown=node:node /app/generated ./generated
USER node
EXPOSE 80

FROM runtime-base AS runtime-plain
CMD ["node", "server.js"]

FROM runtime-base AS runtime-dotenvx
COPY --from=docker.io/dotenv/dotenvx:v2.38.2 /usr/local/bin/dotenvx /usr/local/bin/dotenvx
# The encrypted .env.production comes from the app repo; the wrapper from workspace-ci (named context `ws`).
COPY --chown=node:node .env.production ./
COPY --from=ws --chown=node:node --chmod=0755 next/dotenvx-entry.sh ./
# Build gate: the CI smoke bypasses the ENTRYPOINT, so exercise the binary here.
RUN /usr/local/bin/dotenvx --version >/dev/null
ENTRYPOINT ["/bin/sh", "./dotenvx-entry.sh"]
CMD ["node", "server.js"]
