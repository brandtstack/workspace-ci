# workspace-ci

Shared CI configuration for the priv workspace.

**Public by choice, not by constraint.** Sharing reusable workflows from a
private repo has been GA since Dec 2022 (it needs an Actions access policy on
the called repo). This one stays public because it is the workspace's only
public code artifact — the one piece of engineering practice a stranger can
actually read.

## Workflows

| Workflow | Purpose |
| --- | --- |
| `node-ci.yml` | verify · optional Postgres integration · build + smoke + publish image |
| `go-ci.yml` | same shape for Go services (no in-container smoke — distroless has no shell) |
| `pr-title.yml` | conventional-commit check on the PR title |
| `release.yml` | on merge to main: mint `vX.Y.Z`, promote the tested image, push the tag |

## Inputs

Every `workflow_call` input, read from the workflow files (the source of truth). All are optional.
Booleans are real booleans in YAML; `runner` and the string inputs are quoted strings.

### `node-ci.yml` (14)

| Input | Type | Default | What it does |
| --- | --- | --- | --- |
| `node-version` | string | `'24'` | Node version for `setup-node` in `verify` and `integration` |
| `working-directory` | string | `'.'` | Directory for `npm ci`/`verify`/`verify:integration`, the lockfile cache path, and the Docker build context |
| `needs-postgres` | boolean | `false` | Run the `integration` job: a `postgres:16.15-alpine` service, `DATABASE_URL` set, then `npm run verify:integration`. When `false` the job is skipped |
| `postgres-db` | string | `'test'` | Database name created in the Postgres service and used in `DATABASE_URL` |
| `smoke` | boolean | `true` | Boot the built image and curl `smoke-path` on the published port before pushing |
| `smoke-port` | string | `'80'` | Container port the app listens on (published to host `8080`) |
| `smoke-path` | string | `'/'` | Path requested by the smoke checks |
| `smoke-internal` | boolean | `true` | Also `wget` `127.0.0.1:<smoke-port>` from inside the container (catches a server bound only to the container IP). Only runs when `smoke` is `true` |
| `smoke-run-args` | string | `''` | Extra arguments to `docker run` for the smoke container (e.g. `-e` vars) |
| `docker-build-args` | string | `''` | Newline-separated `build-args` for the image build |
| `dockerfile` | string | `''` | Dockerfile path relative to the checkout root. Empty falls back to `{working-directory}/Dockerfile` (see *A non-default Dockerfile*) |
| `private-registry` | boolean | `false` | Wire the `@brandtstack` GitHub Packages registry into `npm ci` (all jobs) and the image build (as a BuildKit secret, not a build-arg), using the caller's `GITHUB_TOKEN`. No PAT |
| `publish-image` | boolean | `true` | Run the `image` job (PR only): build, smoke, push `ghcr.io/<repo>:sha-<head>`. Set `false` for repos that do not deploy a registry image |
| `runner` | string | `'"ubuntu-latest"'` | JSON for `runs-on` (see *Running on the self-hosted runner*) |

### `go-ci.yml` (11)

Runs `make verify` (and `make verify-integration` when `needs-postgres`) instead of npm scripts.

| Input | Type | Default | What it does |
| --- | --- | --- | --- |
| `go-version` | string | `'1.27'` | Go version for `setup-go` |
| `node-version` | string | `'24'` | Node version installed alongside Go in `verify` and `integration` |
| `needs-postgres` | boolean | `false` | Run the `integration` job against a Postgres service (`DATABASE_URL` carries `?sslmode=disable`) |
| `postgres-db` | string | `'test'` | Database name for that service |
| `smoke` | boolean | `true` | Boot the image and curl `smoke-path` on the published port. No in-container check (distroless has no shell) |
| `smoke-port` | string | `'80'` | Container port the app listens on (published to host `8080`) |
| `smoke-path` | string | `'/'` | Path requested by the smoke check |
| `smoke-run-args` | string | `''` | Extra arguments to `docker run` for the smoke container |
| `docker-build-args` | string | `''` | Newline-separated `build-args` for the image build |
| `publish-image` | boolean | `true` | Run the `image` job (PR only): build, smoke, push `:sha-<head>`. Build context is always `.` and the Dockerfile is `./Dockerfile` |
| `runner` | string | `'"ubuntu-latest"'` | JSON for `runs-on` |

### `pr-title.yml` (1)

| Input | Type | Default | What it does |
| --- | --- | --- | --- |
| `runner` | string | `'"ubuntu-latest"'` | JSON for `runs-on` |

### `release.yml` (3 inputs, 3 secrets)

| Input | Type | Default | What it does |
| --- | --- | --- | --- |
| `publish-image` | boolean | `true` | Must match the caller's `node-ci`/`go-ci` setting. `false` mints the git tag only, with no registry work |
| `coolify-app-uuid` | string | `''` | Coolify application uuid. Empty means no deploy is attempted (see *Deploying to Coolify*). Only acts when `publish-image` is also `true` |
| `runner` | string | `'"ubuntu-latest"'` | JSON for `runs-on` |

Secrets (all optional, but needed **together** to deploy): `COOLIFY_TOKEN`, `CF_ACCESS_CLIENT_ID`,
`CF_ACCESS_CLIENT_SECRET`.

## Consuming it

`.github/workflows/ci.yml`:

```yaml
name: ci
on:
  push: { branches: [main] }
  pull_request: { branches: [main] }

# Cancel superseded PR runs; never cancel a main run mid-release.
concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  ci:
    uses: brandtstack/workspace-ci/.github/workflows/node-ci.yml@main
    permissions:
      contents: read
      packages: write        # required to publish to GHCR
    with:
      smoke-port: '80'
      smoke-path: '/'

  pr-title:
    if: github.event_name == 'pull_request'
    uses: brandtstack/workspace-ci/.github/workflows/pr-title.yml@main

  release:
    if: github.event_name == 'push'
    needs: ci
    uses: brandtstack/workspace-ci/.github/workflows/release.yml@main
    permissions:
      contents: write        # push the git tag
      packages: write        # retag in GHCR
      pull-requests: read    # resolve the merged PR's head SHA
```

Pin `@main` to a commit SHA in real repos — Renovate keeps it current via
`helpers:pinGitHubActionDigests`.

`renovate.json`:

```json
{ "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["github>brandtstack/workspace-ci"] }
```

### A non-default Dockerfile

`node-ci.yml` takes a **`dockerfile:`** input (path relative to the checkout root). Leave it unset
and the build falls back to `{context}/Dockerfile` as before — the empty default is not passed
through, so callers that do not set it are unaffected.

```yaml
    with:
      publish-image: true
      dockerfile: deploy/Dockerfile
```

Added 2026-09-01 for `access-panel`, whose Dockerfile lives at `deploy/Dockerfile`. Without it there
was no way to point CI at a non-default path, so that repo could not publish an image and was the
last app Coolify still built from source.

### Running on the self-hosted runner (private repos only)

All four workflows take a **`runner:`** input — JSON for `runs-on`, default `'"ubuntu-latest"'`
(GitHub-hosted). Private brandtstack repos opt in to the one-job, Sysbox-isolated runners on the Acer
by passing it to **every** job, `release` included:

```yaml
    with:
      runner: '["self-hosted","priv-ci"]'
```

⚠️ **Never set it from a public repo** — a public repo's PRs would run untrusted code on home
hardware. This repo is public and never sets it; the launcher also refuses any job from a public repo.
Fallback if the Acer is down: set it back to `'"ubuntu-latest"'` (spends Actions minutes).
Operations: `priv/infra/reference/ci-runner.md`.

⚠️ **`publish-image` must match between the `ci` and `release` jobs.** `ci` alone leaves `release`
with no artifact to promote; `release` alone fails after CI has already passed.

⚠️ Keep `.dockerignore` at the **repo root** — it is honored for a build whose context is the repo
root even when the Dockerfile is in a subdirectory. A `deploy/.dockerignore` is silently ignored.

## Versioning

There is **no version in `package.json`** and no version commit on `main`. The
git tag is the version.

On merge, `release.yml` derives the bump from the squash commit title —
which is the PR title, hence `pr-title.yml` being a required check:

| Title | Bump |
| --- | --- |
| `feat!: …` or `BREAKING CHANGE` in body | major |
| `feat: …` | minor |
| anything else (`fix`, `chore`, `docs`, …) | patch |

Base is the highest existing `v*` tag, so a repo with existing tags continues
from where it was.

### Order is load-bearing

```
PR:     build image → smoke → push ghcr.io/<repo>:sha-<head>
merge:  derive vX.Y.Z → retag sha-<head> → vX.Y.Z + latest → push git tag LAST
```

The image is **never rebuilt on main**. `release.yml` recovers the branch head
SHA via the commit→PR association (squash discards the branch SHA) and
promotes that exact manifest with `docker buildx imagetools create` — a
registry-side copy. What ships is bit-for-bit what was smoke-tested.

The git tag goes last so a tag never exists without the image it names.

⚠️ Tags pushed with `GITHUB_TOKEN` do **not** trigger further workflows
(GitHub loop prevention). Any future tag-triggered deploy needs a PAT or app
token.

### Deploying to Coolify from `release.yml`

Pass `coolify-app-uuid` and the three Coolify secrets to have a merge deploy the image that CI
already built and smoke-tested, instead of letting Coolify rebuild from source:

```yaml
  release:
    if: github.event_name == 'push'
    needs: ci
    uses: brandtstack/workspace-ci/.github/workflows/release.yml@<sha>
    permissions: { contents: write, packages: write, pull-requests: read }
    with:
      coolify-app-uuid: <application uuid>
    secrets:
      COOLIFY_TOKEN:           ${{ secrets.COOLIFY_TOKEN }}
      CF_ACCESS_CLIENT_ID:     ${{ secrets.CF_ACCESS_CLIENT_ID }}
      CF_ACCESS_CLIENT_SECRET: ${{ secrets.CF_ACCESS_CLIENT_SECRET }}
```

**Omit `coolify-app-uuid` and the workflow behaves exactly as before** — tag only, no deploy. That
guard is what allows apps to migrate one at a time.

Prerequisites, one-time per application: `build_pack` set to `dockerimage`, the GHCR image name set,
and **`is_auto_deploy_enabled` set to `false`** — otherwise the GitHub-App webhook and this workflow
race on every merge and the webhook deploys the *previous* tag.

## Repo settings this assumes

- Squash merge only; **"Default to PR title for squash merge commits"** on —
  without it the squash title is a commit list and the bump is wrong.
- Ruleset on `main`: require a PR, **0 required approvals** (solo — any other
  value deadlocks), block force pushes, and set
  `require_extra_approval_for_unattributed_changes: false` (it defaults **true**
  and deadlocks a solo repo).
- Required checks are the **job-prefixed** names — `ci / verify`, `ci / image`,
  `pr-title / lint` — not `verify` / `image` / `pr-title`. A name that never
  reports blocks every PR permanently.
  ⚠️ **Never require a check that can skip.** `ci / integration` is skipped
  wherever `needs-postgres: false`, and a skipped job never reports — require it only
  on repos that actually run it. Likewise `ci / image` on a repo with
  `publish-image: false`. A repo with no CI at all (this one) requires a PR and
  **no** status check.
- Branch protection needs GitHub Team on private repos; Free enforces rulesets
  on public repos only.

## The script contract

Each repo defines what it can run, so the workflow and Dockerfile stay identical everywhere:

- `verify` — everything runnable with no external services (lint / typecheck / test, whichever exist)
- `verify:integration` — optional; only where tests need a real service

The Dockerfile builder stage runs `npm run verify` before `npm run build`. That
`RUN` was the deploy gate while Coolify built from source; since it stopped
(below) the same `RUN` fires in the `ci / image` job instead, so a failed
`verify` fails the PR and no image is ever published. **The box has no
independent gate left** — which is why `ci / verify` and `ci / image` are
required checks, and why the `RUN` stays in the Dockerfile rather than being
trusted to CI alone.

## Coolify builds nothing (since 2026-09-01)

Every application is `build_pack: dockerimage` with auto-deploy **off**, pulling
the `vX.Y.Z` tag this pipeline promoted. CI is the only thing that deploys, and a
rollback is a tag swap.

`release.yml` upserts `APP_VERSION` on the application (runtime, not build-time —
build-time vars are rendered as Docker `ARG`s into the public build log) before
pointing it at the new tag, so the running container reports the minted version
rather than a `SOURCE_COMMIT` short SHA.

## Parked majors in the preset

`default.json` disables or caps these; each rule's `description` says when to unpark it.

| Package | Rule | Why |
| --- | --- | --- |
| `typescript` | `<7` | typescript-eslint throws on any TS 7.x |
| `eslint` | `<10` where already on 9 | eslint-plugin-react errors on ESLint 10 |
| `@babel/plugin-transform-runtime` | no major | consumers pin 7.29.7 via `overrides`; v8 drops that guard, then the next install ERESOLVEs |

This repo's own `renovate.json` also parks the `postgres` docker major (go-ci integration service
stays on 16 to match prod `personal-postgres`, per `infra/reference/kvm4-platform.md`, a doc, not
the live container).
