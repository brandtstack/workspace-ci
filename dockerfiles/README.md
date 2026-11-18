# Shared Dockerfiles

Built by `.github/workflows/app.yml` for the contract families `next`, `spa` and `go`
(`custom` uses the repo's own Dockerfile). The build context is the app repo. Helper files that live
here (`next/`, `nginx/`) reach the build through the BuildKit named context `ws`
(`build-contexts: ws=<ws-ci>/dockerfiles`; locally: `--build-context ws=dockerfiles`).
No image runs lint, test or typecheck: `ci / verify` gates the commit. The registry token is the
BuildKit secret `npm_token` (never an ARG); it is optional.

## Contract build-args

`build-args` (`NEXT_PUBLIC_*` / `VITE_*`, public values only) travel as one ARG,
`PUBLIC_BUILD_ENV_B64`: base64 of the newline-joined `K=V` lines, set by the workflow. The next and
spa build stages decode it and `export` each line before `npm run build`. The go family takes no
`build-args` (the contract rejects them). A `custom` build gets the same ARG too: a custom Dockerfile
must declare `ARG PUBLIC_BUILD_ENV_B64` and decode it the way next/spa do (`printf '%s' "$ARG" | base64 -d`,
then `export` each `K=V` line before the build), or its `build-args` have no effect.
Being an ARG, the value shows in the build log; never put a secret there.

## next (`next.Dockerfile`)

Target `runtime-plain` or `runtime-dotenvx` (the workflow picks it from the contract's `dotenvx`).
Build args: `NODE` (must equal the image's Node major, 24; a mismatch fails the build), `PRISMA`
and `MIGRATE` (`true`/`false`). Port 80, `HOSTNAME=0.0.0.0`, user `node`. The CMD of both runtimes is
`/bin/sh /app/start.sh` (`next/start.sh`), which ends in `exec node /app/server.js`.

The repo must provide:
- `package.json`, `package-lock.json` and `.npmrc` at the app root.
- `output: 'standalone'` in `next.config`, and a `/healthz` route.
- `public/` is optional (the build creates an empty one).
- With `dotenvx`: a committed, encrypted `.env.production`. The wrapper `next/dotenvx-entry.sh` and
  the dotenvx binary come from this repo. The build removes `.env.production` before `next build`
  (every target), and the `runtime-dotenvx` stage runs `next/check-env-encrypted.sh` on the copy it
  ships, failing the build if any value is plaintext. The wrapper's `--strict` precheck needs the private
  key, which CI never has, so the CI smoke (app.yml and `scripts/build-image`) starts a dotenvx image with
  `--entrypoint /bin/sh ... /app/start.sh` instead of the wrapper. So the wrapper itself never runs in CI;
  `tests/dockerfiles/test_start_sh.py` pins its wiring (ENTRYPOINT wrapper, CMD `start.sh`) statically.
- With `prisma: true`: generator `prisma-client` with `output = "../generated/prisma"`, so the client
  lands in `/app/generated`, which the runtime copies (empty without Prisma).
- With `migrate: true` (needs `prisma: true`): migrations run at container start, before the server.
  - **Packaging.** `/opt/migrate` holds the Prisma CLI (and `dotenv`, if the lockfile has it), installed from
    the app's own `package-lock.json`: every package at its locked version, integrity-checked, and the build
    fails if any installed version differs (`next/migrate-pin.cjs`). No registry token reaches that install.
    Only `prisma/schema.prisma`, `prisma/migrations` and `prisma.config.ts` are copied in, never the rest of
    `prisma/`. Nothing is fetched at start. The lockfile must be version 2 or 3.
  - **`prisma.config.ts` may import only `prisma/config` and `dotenv`**: they are the only modules in
    `/opt/migrate`. It must read the URL from `DATABASE_URL`.
  - **Start.** `start.sh` runs `prisma migrate deploy` from `/opt/migrate`. A failed migration exits non-zero
    and the server never starts. A non-empty `SKIP_MIGRATIONS` skips the step.
  - **Credentials and environment.** The migration runs with `PATH`, `HOME`, `CHECKPOINT_DISABLE` and
    `DATABASE_URL` only, never the app's other secrets. Set `MIGRATE_DATABASE_URL` to give the migration a
    schema-owner role while the server's `DATABASE_URL` stays a least-privilege one; `start.sh` unsets it before
    the server starts. Without it, the server's own `DATABASE_URL` must be able to run DDL (a single role,
    as insta-web has).
  - **Logs.** Prisma's own output is not printed (it names hosts and databases). The log gets fixed `[start]`
    lines, the `Applying migration`, `No pending migrations` and `All migrations` lines, and on failure only
    the exit code and Prisma's error code (for example `P3009`).
  - Under `dotenvx` the ENTRYPOINT wrapper runs `start.sh`, so the migration sees the decrypted URL.
  - **Migrations must be backward-compatible with the release before them.** The new container migrates while
    the old one still serves. A dropped or renamed column, or a new `NOT NULL` without a default, breaks the
    old container until the swap. Expand first, contract in a later release.
  - **Time budget.** Coolify's check (start period 5 s, then 10 × 5 s) marks the new container unhealthy after
    about 55 s, and the migration runs inside that window. A longer migration needs a manual run first.
  - **A failed migration is not rolled back by itself.** The old container keeps serving, but Postgres DDL
    applied before the failure stays, and Prisma records the migration as failed. Every later start then
    stops on `P3009` until someone fixes the database and runs `prisma migrate resolve --rolled-back <name>`
    (or `--applied`) against it.
  - app.yml smokes the image with `SKIP_MIGRATIONS=1` (no database), and its "Migrate CLI loads" step runs
    `migrate status` against an unreachable URL and requires Prisma's `P1001`, which proves the CLI, the
    config and its imports load. `ci / integration` runs the migrations against a real Postgres.

## spa (`spa.Dockerfile`)

Build arg `NODE`. `npm run build` must produce `dist/`. nginx 1.31.8, user `nginx`, port 80.

The repo must provide `nginx.conf` (a `server` block, copied to `conf.d/default.conf`) with
`listen 80;` and `include /etc/nginx/snippets/healthz.conf;`. An optional `nginx/` directory is
copied to `/etc/nginx/snippets/`, but the shared `healthz.conf` is copied after it and always wins over an app file of that name. The shipped main `nginx.conf` has no `user` directive and keeps the
pid and temp files in `/tmp`; binding 80 as non-root relies on `ip_unprivileged_port_start=0`.

Two optional subdirectories of `nginx/` handle values known only at start (tilde's `/health` version):
- `nginx/entrypoint.d/*.sh` are copied to `/docker-entrypoint.d/` (mode 0755). The stock entrypoint runs
  them as `nginx` before nginx starts and **aborts, so nginx never starts, if one exits non-zero**: a hook
  must always `exit 0`. Every file there is copied executable, so put only hooks in it.
- **A hook writes config that nginx `include`s, so it must write only values it has validated**
  (an allow-list of characters, as tilde's `10-app-version.sh` does with `tr -cd 'A-Za-z0-9._+-'`):
  no quotes, semicolons, braces or newlines from the environment. `/etc/nginx/runtime/` is writable by
  the serving user in every spa image; this is the accepted cost of start-time values.
- `/etc/nginx/runtime/` is owned by `nginx` and is the only writable config path. `nginx/runtime/` holds
  its shipped defaults, so an `include /etc/nginx/runtime/<file>` never names a missing file. On a
  read-only root fs a hook's write fails and the default stays.

(Both also land under `/etc/nginx/snippets/` with the rest of `nginx/`; harmless.) The spa fixture
exercises both.

## go (`go.Dockerfile`)

Build args `GO` (major.minor, e.g. `1.27`), `GO_MAIN` (main package, e.g. `./cmd/x`), `GO_DIR`
(module directory, `.` or e.g. `server`). Distroless nonroot (`65532:65532`), no shell.

The app must:
- Listen on `PORT` (80 in the image). A listen address under any other name is not read.
- Serve `/healthz`.
- Implement `<binary> healthcheck`: GET `http://127.0.0.1:$PORT/healthz` with a 2 s timeout and no redirects
  followed, exit 0 on 200, 1 otherwise. The image `HEALTHCHECK` runs it in exec form (`/app healthcheck`, every 10 s, 3 s timeout,
  5 s start period, 3 retries), and the app.yml smoke requires it to exit 0 and the container to turn
  `healthy` within 30 s.
- Keep persistent files under `/data`. Every go image ships it empty and owned by `65532:65532`; a named
  (or anonymous) volume mounted there takes that ownership on first mount. A bind mount keeps the host
  directory's owner, so it must be chowned to `65532` on the host or the app cannot write.

## Not supported yet (step 3 extends the files if a pilot needs it)

- Prisma `prisma-client-js` (client inside `node_modules`): not supported, by decision (spec step 3). Switch
  the generator to `prisma-client` with `output = "../generated/prisma"`.
