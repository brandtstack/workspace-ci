# Shared Next.js image, built by .github/workflows/app.yml. Contract: dockerfiles/README.md.
# Stages: deps -> build -> runtime-base -> runtime-plain | runtime-dotenvx. The workflow picks the
# last one with `target:`. No lint, test or typecheck here: `ci / verify` gates the commit.
# migrate-true | migrate-false -> migrate-src: the Prisma CLI for migrations at start (contract `migrate`).
# BuildKit builds only the one MIGRATE selects, in parallel with `build`.
ARG MIGRATE=false

FROM node:24.22.3-alpine AS deps
# The contract's `node:` must match this image's major version (a FROM tag cannot be an ARG and stay
# exact-pinned), so a mismatch fails the build instead of silently using another Node.
ARG NODE=24
RUN got="$(node -p "process.versions.node.split('.')[0]")" \
 && if [ "$got" != "$NODE" ]; then echo "contract node: $NODE but this image has Node $got" >&2; exit 1; fi
RUN apk add --no-cache libc6-compat ca-certificates
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
# No audit/fund/update check: the audit's advisory lookups cost ~40 s per build (Renovate's osv alerts cover it).
ENV NPM_CONFIG_AUDIT=false NPM_CONFIG_FUND=false NPM_CONFIG_UPDATE_NOTIFIER=false
# The private-registry token is a BuildKit secret, never an ARG (build logs render ARGs in clear).
# A public-deps build has no secret file, so it is optional.
RUN --mount=type=secret,id=npm_token \
    if [ -f /run/secrets/npm_token ]; then NPM_TOKEN="$(cat /run/secrets/npm_token)"; export NPM_TOKEN; fi; \
    npm ci

FROM node:24.22.3-alpine AS build
ARG PRISMA=false
ARG PUBLIC_BUILD_ENV_B64=""
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
RUN apk add --no-cache libc6-compat ca-certificates
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Prisma layout: generator `prisma-client` with output "../generated/prisma" (see README).
RUN if [ "$PRISMA" = "true" ]; then npx prisma generate; fi \
 && mkdir -p generated public
# Contract build args (NEXT_PUBLIC_*/VITE_*) arrive as one ARG: base64 of newline-separated K=V lines.
# Public values only by contract, so the ARG showing in the build log is acceptable. read.sh restricts
# the keys; each line is exported quoted, never eval'd.
# `.env.production` (dotenvx: committed, encrypted) is removed before the build for every target: neither
# its ciphertext nor a stray plaintext may be visible to `next build` or end up in the standalone output.
RUN set -e; \
    if [ -n "$PUBLIC_BUILD_ENV_B64" ]; then \
      printf '%s' "$PUBLIC_BUILD_ENV_B64" | base64 -d > /tmp/public-build.env; \
      while IFS= read -r line || [ -n "$line" ]; do if [ -n "$line" ]; then export "$line"; fi; done < /tmp/public-build.env; \
      rm -f /tmp/public-build.env; \
    fi; \
    rm -f .env.production; \
    npm run build

# Contract `migrate: false`: an empty /opt/migrate, so start.sh skips migrations.
FROM node:24.22.3-alpine AS migrate-false
RUN mkdir -p /opt/migrate

# Contract `migrate: true`: the Prisma CLI (and dotenv, if the lockfile has it) in its own prefix, plus the
# schema, the migrations and prisma.config.ts (README "next"; it may import only `prisma/config` and `dotenv`).
# - Pinned by the app's own lockfile: a two-dependency package.json beside a copy of package-lock.json, so npm
#   reuses every locked version and checks each tarball's integrity hash. The check after the install fails the
#   build if any installed package differs from the app's lockfile.
# - No registry token and no app .npmrc: prisma and dotenv are public, so nothing secret is in this environment.
# - The schema engine: npm 11 skips install scripts not covered by `allowScripts`, and without @prisma/engines'
#   postinstall the CLI downloads the engine into ~/.cache at run time, i.e. at every container start. So the
#   postinstall runs explicitly here (it fetches the engine for this platform from Prisma's CDN and checks its
#   hash). app.yml's "Migrate CLI loads" runs the CLI with --network none, which fails if anything is fetched.
# - Only the schema and prisma/migrations are copied, never the rest of prisma/ (seeds, dumps, .env). The schema is
#   either prisma/schema.prisma or a multi-file prisma/schema/ directory (only its *.prisma files, sub-folders kept;
#   it must hold at least one): exactly one of the two must exist. The
#   prisma/ directory is bind-mounted for the copy only, so nothing else from it enters a layer.
FROM node:24.22.3-alpine AS migrate-true
ENV NPM_CONFIG_AUDIT=false NPM_CONFIG_FUND=false NPM_CONFIG_UPDATE_NOTIFIER=false
WORKDIR /opt/migrate
COPY package-lock.json ./app-lock.json
COPY --from=ws next/migrate-pin.cjs /tmp/migrate-pin.cjs
RUN set -e; \
    node /tmp/migrate-pin.cjs prepare app-lock.json; \
    printf 'registry=https://registry.npmjs.org/\n' > .npmrc; \
    npm install --omit=dev --no-audit --no-fund; \
    node /tmp/migrate-pin.cjs check app-lock.json; \
    (cd node_modules/@prisma/engines && node scripts/postinstall.js); \
    ls node_modules/@prisma/engines/schema-engine-* >/dev/null; \
    rm -rf .npmrc package.json package-lock.json app-lock.json /tmp/migrate-pin.cjs /root/.npm
RUN --mount=type=bind,source=prisma,target=/src/prisma \
    set -e; \
    mkdir -p /opt/migrate/prisma; \
    if [ -f /src/prisma/schema.prisma ] && [ ! -e /src/prisma/schema ]; then \
      cp /src/prisma/schema.prisma /opt/migrate/prisma/schema.prisma; \
    elif [ -d /src/prisma/schema ] && [ ! -e /src/prisma/schema.prisma ]; then \
      cd /src/prisma/schema; \
      [ -n "$(find . -type f -name '*.prisma' | head -n 1)" ] || { echo "prisma: prisma/schema/ has no *.prisma files" >&2; exit 1; }; \
      find . -type f -name '*.prisma' -exec sh -c 'mkdir -p "$2/$(dirname "$1")" && cp "$1" "$2/$1"' _ {} /opt/migrate/prisma/schema \; ; \
    else \
      echo "prisma: need prisma/schema.prisma or prisma/schema/" >&2; exit 1; \
    fi; \
    cp -R /src/prisma/migrations /opt/migrate/prisma/migrations
COPY prisma.config.ts /opt/migrate/prisma.config.ts

FROM migrate-${MIGRATE} AS migrate-src

FROM node:24.22.3-alpine AS runtime-base
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=80 HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
COPY --from=build --chown=node:node /app/generated ./generated
# Root-owned and read-only for the app user: migrate deploy only reads it.
COPY --from=migrate-src /opt/migrate /opt/migrate
COPY --from=ws --chmod=0755 next/start.sh ./start.sh
USER node
EXPOSE 80

FROM runtime-base AS runtime-plain
CMD ["/bin/sh", "/app/start.sh"]

FROM runtime-base AS runtime-dotenvx
COPY --from=docker.io/dotenv/dotenvx:v2.38.2 /usr/local/bin/dotenvx /usr/local/bin/dotenvx
# The encrypted .env.production comes from the app repo (the build context, not the build stage, which
# removed it); the wrapper and the check come from workspace-ci (named context `ws`).
COPY --chown=node:node .env.production ./
COPY --from=ws --chown=node:node --chmod=0755 next/dotenvx-entry.sh ./
COPY --from=ws --chown=node:node --chmod=0755 next/check-env-encrypted.sh /tmp/check-env-encrypted.sh
# Build gates: the file must hold no plaintext value (fails the build, prints names only), and the dotenvx
# binary must run. The CI smoke does not go through the ENTRYPOINT: app.yml runs a dotenvx image with
# `--entrypoint node ... server.js`, because the wrapper's `--strict` precheck needs the private key,
# which CI never has.
RUN sh /tmp/check-env-encrypted.sh .env.production && rm -f /tmp/check-env-encrypted.sh
RUN /usr/local/bin/dotenvx --version >/dev/null
# The wrapper wraps start.sh, so a migration sees the decrypted DATABASE_URL.
ENTRYPOINT ["/bin/sh", "./dotenvx-entry.sh"]
CMD ["/bin/sh", "/app/start.sh"]
