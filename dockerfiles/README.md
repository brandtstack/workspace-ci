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
(`true`/`false`). Port 80, `HOSTNAME=0.0.0.0`, user `node`.

The repo must provide:
- `package.json`, `package-lock.json` and `.npmrc` at the app root.
- `output: 'standalone'` in `next.config`, and a `/healthz` route.
- `public/` is optional (the build creates an empty one).
- With `dotenvx`: a committed, encrypted `.env.production`. The wrapper `next/dotenvx-entry.sh` and
  the dotenvx binary come from this repo. The build removes `.env.production` before `next build`
  (every target), and the `runtime-dotenvx` stage runs `next/check-env-encrypted.sh` on the copy it
  ships, failing the build if any value is plaintext. The wrapper's `--strict` precheck needs the private
  key, which CI never has, so the CI smoke (app.yml and `scripts/build-image`) starts a dotenvx image with
  `--entrypoint node ... server.js` instead of the wrapper.
- With `prisma: true`: generator `prisma-client` with `output = "../generated/prisma"`, so the client
  lands in `/app/generated`, which the runtime copies (empty without Prisma).

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

- Prisma `prisma-client-js` (client inside `node_modules`); only the `generated/prisma` layout works.
- Prisma migrations at start (`prisma migrate deploy`: needs the Prisma CLI, `prisma/`, `prisma.config.ts`
  and an entrypoint that chains before `node server.js`, also under dotenvx); insta-web and brl-intraweb
  need it, and spec step 3 designs it.
