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
| `app.yml` | the build contract: reads `.github/build.yml`, then verify · optional Postgres integration · build + smoke + publish image |
| `pr-title.yml` | conventional-commit check on the PR title |
| `pr-review.yml` | one advisory Claude review comment per PR, authenticated by workload identity federation (no API key) |
| `release.yml` | on merge to main: mint `vX.Y.Z`, promote the tested image, push the tag, create the GitHub Release, and (Coolify apps) deploy and mark it Latest once live |

## Inputs

Every `workflow_call` input, read from the workflow files (the source of truth). All are optional.
Booleans are real booleans in YAML; `runner` and the string inputs are quoted strings.

### `app.yml` (2)

Everything else comes from the caller's `.github/build.yml` (see *App contract*).

| Input | Type | Default | What it does |
| --- | --- | --- | --- |
| `runner` | string | `'"ubuntu-latest"'` | JSON for `runs-on` (see *Running on the self-hosted runner*) |
| `path` | string | `'.'` | The app root: its `.github/build.yml` is the contract, and it is the image build context |

For a Go app, after `make verify` the `verify` job runs **`govulncheck` (pinned `v1.9.0`) on every tracked
`go.mod`**, with the latest patch of the contract's Go version (`check-latest`). It deliberately does not use the
runner's cached patch: govulncheck judges the stdlib of the Go on `PATH`, and the images build
`FROM golang:<go>-alpine`, which is always the latest patch. A reachable vulnerability (exit 3)
or a scanner error **fails every event except a push**, and on a push only **warns**, so it never
strands a release (`release` needs `ci`). The same holds when the scanner cannot be set up at all (no
Go manifest, no module proxy): on a push both steps are `continue-on-error`. There is no key to
turn it off. The built binary is kept in the runner tool cache per scanner version + Go patch (a cold
build is ~35-100 s; a cache hit makes the whole step ~8 s). A cache hit is trusted by path: on a
self-hosted runner that volume is shared by every repo, which is accepted only because they all have
the same owner, as for the Go toolchain cached in the same volume. Offline tests:
`tests/app/test_govulncheck.py`.


### `pr-title.yml` (1)

| Input | Type | Default | What it does |
| --- | --- | --- | --- |
| `runner` | string | `'"ubuntu-latest"'` | JSON for `runs-on` |

### `pr-review.yml` (6)

| Input | Type | Default | What it does |
| --- | --- | --- | --- |
| `runner` | string | `'"ubuntu-latest"'` | JSON for `runs-on` |
| `model` | string | `'claude-sonnet-5-5'` | Model for the Messages call. Only `claude-sonnet-5-5` and `claude-haiku-5-5`; anything else logs a line and uses the default |
| `max-tokens` | number | `4096` | `max_tokens` for the review, at most `8192`; a cut-off review says so in the comment |
| `max-diff-bytes` | number | `153600` | Cap on the diff after lockfiles and generated files are left out, at most `307200`. Over it, the comment says "not reviewed" and no API call is made |
| `review-drafts` | boolean | `false` | Also review draft PRs |
| `review-renovate` | boolean | `false` | Also review PRs opened by `renovate[bot]` |

The bounds are enforced inside the workflow, because the caller is whatever a PR's branch says. A
value over its cap logs a line and uses the cap; a value that is not a number uses the default.

No secrets. The token exchange reads four **org-level Actions variables**, which are identifiers, not
credentials: `ANTHROPIC_FEDERATION_RULE_ID`, `ANTHROPIC_ORGANIZATION_ID`,
`ANTHROPIC_SERVICE_ACCOUNT_ID`, and `ANTHROPIC_WORKSPACE_ID` (sent only when set). See
*Automatic PR review*.

### `release.yml` (4 inputs, 3 secrets)

| Input | Type | Default | What it does |
| --- | --- | --- | --- |
| `publish-image` | boolean | `true` | `false` mints the git tag only, with no registry work. `app.yml` always publishes `:sha-<head>`, so its callers leave this on |
| `coolify-app-uuid` | string | `''` | Coolify application uuid. Empty means no deploy is attempted (see *Deploying to Coolify*). Only acts when `publish-image` is also `true` |
| `runner` | string | `'"ubuntu-latest"'` | JSON for `runs-on` |
| `contract` | boolean | `false` | Contract mode (callers of `app.yml`): the Coolify app comes from the caller's `.github/build.yml` (`coolify-app`), and before deploying the release writes the app's port and health check from the contract's family. `coolify-app-uuid` must then be empty |

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
    uses: brandtstack/workspace-ci/.github/workflows/app.yml@main
    permissions:
      contents: read
      packages: write        # required to publish to GHCR

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
`helpers:pinGitHubActionDigests`. The one exception is `pr-review.yml`, which stays on `@main`
(see *Automatic PR review*).

`renovate.json`:

```json
{ "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["github>brandtstack/workspace-ci"] }
```

### Automatic PR review

In its own file, `.github/workflows/pr-review.yml`:

```yaml
name: pr-review
on:
  pull_request:
    branches: [main]
    # ready_for_review: a draft marked ready is reviewed then, not at its next push.
    types: [opened, synchronize, reopened, ready_for_review]

jobs:
  review:
    if: github.event_name == 'pull_request'
    uses: brandtstack/workspace-ci/.github/workflows/pr-review.yml@main
    permissions: { contents: read, pull-requests: write, id-token: write }
    with:
      runner: '["self-hosted","priv-ci"]'
```

⚠️ **This one call stays on `@main`, not a SHA**, unlike every other `uses:` above. The federation
rule hands out a token only to `pr-review.yml` at `refs/heads/main` (see *The federation rule*). A
caller pinned to a SHA gets an opaque `401` and no review. The shared Renovate preset leaves this
file's `workspace-ci` ref alone for that reason; it matches on the file path.

**Migrating an existing caller:** on the `pr-review.yml` call, replace a pinned `@<sha> # main`
with `@main` by hand, and move the call to `.github/workflows/pr-review.yml` if it lives anywhere
else. Renovate never undoes a pin, and in any other file it would pin `@main` again.

The job asks GitHub for an OIDC token (audience `https://api.anthropic.com`), exchanges it at
`/v1/oauth/token` for an access token that lives about ten minutes, and makes one `/v1/messages`
call with **no tools**. The OIDC token and the access token are masked the moment they exist, and
no credential is ever a command-line argument: headers and bodies go to `curl` by file. The answer
is posted as one PR comment by `github-actions[bot]` that starts with `<!-- pr-review -->`; later
pushes edit that comment. A comment by anyone else, even one quoting the marker, is never touched.

**What the model sees:** the PR title and the diff, minus generated files (`*.min.js`, `*.map`,
`*_generated.go`, and `dist/`, `build/`, `vendor/`, `coverage/` at the repo root) and the lockfiles
listed next. The diff sits between tags with a random name per run, so a diff cannot close them.
The left-out files are listed at the bottom of the comment (the first 50, then "and N more").

**Lockfiles:** `package-lock.json`, `npm-shrinkwrap.json`, `yarn.lock` and `pnpm-lock.yaml` are
scanned instead of sent. On each added line the scan reads these keys, quoted with `"` or `'` or
not:

- `resolved` (npm, Yarn 1) and `tarball` (pnpm);
- `resolution` (Yarn Berry, `"name@source"`; `name@npm:<version>` is the registry and passes);
- `repo` and `directory` inside a pnpm `resolution: {…}` map (a git or local source; always
  flagged).

A value that does not start with `https://registry.npmjs.org/` (or `https://npm.pkg.github.com/`
for `@brandtstack`) puts a warning in the comment, whatever its scheme (`git+ssh:`, `github:`,
`file:`, scp-style). So does an npm workspace link (`"resolved": "packages/x"`), and any added line
with a JSON `\u` escape. A bare version or a single word under `resolved`, `tarball` or
`resolution` is not a source and is not flagged. Anything outside those keys is not checked.
`go.sum` (hashes only) and the binary `bun.lockb` are left out unscanned. Every other lockfile
(`Cargo.lock`, `uv.lock`, `poetry.lock`, `bun.lock`, `composer.lock`, `Gemfile.lock`) goes to the
model like source, and counts toward `max-diff-bytes`. Paths git quotes (tabs, non-ASCII) keep
their quotes.

**It never fails the PR once it runs:** a skip, missing variables, an unreadable diff, a denied
exchange (the API answers an opaque `401`) or an API error writes a warning to the log and the job
summary and exits 0. After an earlier review, a failure leaves that review up with a "Not
re-reviewed at `<sha>`" line on top; a push that leaves only lockfiles or generated files replaces
it with "nothing to review". The job times out after 10 minutes, and every `curl` and `gh` call has
its own limit. Never make it a required check.

⚠️ **A missing permission is a hard failure.** A caller that grants less than
`pull-requests: write` and `id-token: write` fails at workflow startup, before the job runs, and
that does show as a failed check.

The diff is untrusted input; the worst a hostile diff can do is a misleading comment. The model's
text is posted as is, except that every `@name` gets a zero-width space after the `@`, and the
HTML entities for `@` (`&#64;`, `&#x40;`, `&commat;`, any case) are turned into the same, so a
comment can never mention a person or a team. That includes code spans, so `@types/node` carries an
invisible character there too.

#### The federation rule

The rule that lets a run mint a token lives in the Claude Console, not here, and **it is the only
thing that decides which runs get a token**. The org `vars.ANTHROPIC_*` ids are not secret. Under
`pull_request` a PR's own copy of its workflows runs, so any workflow in any matching repo can ask
GitHub for an OIDC token with the `https://api.anthropic.com` audience, and so can a caller that
points at `pr-review.yml` on some other branch of this repo. Scope the rule to this workflow **as
merged to `main`**:

| Claim | Value |
| --- | --- |
| subject | `repo:brandtstack@316569120/*` |
| `repository_owner` | `brandtstack` |
| `repository_owner_id` | `316569120` |
| `repository_visibility` | `private` |
| `event_name` | `pull_request` |
| CEL | `claims.job_workflow_ref == "brandtstack/workspace-ci/.github/workflows/pr-review.yml@refs/heads/main"` |

Scope: `workspace:developer`, token lifetime 10 minutes. **Required:** a dedicated workspace with
a spend limit. The limit is what bounds a token that reaches the wrong code.

**Why an exact match on `main`.** A prefix such as `…/pr-review.yml@` accepts the file at any ref:
an unmerged branch of this public repo could carry a `pr-review.yml` that sends the token anywhere.
`main` is the one ref that only changes through a pull request. Its ruleset requires a pull request
(squash only) and allows no force-push, no deletion and no bypass. It requires **no approving
review and no status checks**, so anyone who can write to this repo can merge their own change. The
rule pins the ref; it does not make what is on it reviewed.

Because callers use `@main`, a change merged here reaches every caller at its next PR run. The
other workflows in this repo reach a caller only when that caller merges its Renovate digest bump;
this one has no such second look.

⚠️ **The subject is in GitHub's id form, not `repo:brandtstack/…`.** For a PR run the `sub` claim
looks like `repo:brandtstack@316569120/<repo>@<repo id>:pull_request` (owner and repo each
followed by `@<numeric id>`). A subject of `repo:brandtstack/*` never matches: the exchange fails
with `match_subject_prefix`, which the job only sees as an opaque `401`.

Tests: `python3 tests/pr-review/test_pr_review.py` (fake `gh`, stub API on `127.0.0.1`, `curl`
and `jq` wrapped to catch a credential in argv; nothing leaves the machine). `tests.yml` runs them
on every PR, on GitHub-hosted runners only.

### Running on the self-hosted runner (private repos only)

All four workflows take a **`runner:`** input — JSON for `runs-on`, default `'"ubuntu-latest"'`
(GitHub-hosted). Private brandtstack repos opt in to the one-job, Sysbox-isolated self-hosted runners
by passing it to **every** job, `release` included:

```yaml
    with:
      runner: '["self-hosted","priv-ci"]'
```

⚠️ **Never set it from a public repo** — a public repo's PRs would run untrusted code on home
hardware. This repo is public and never sets it; the launcher also refuses any job from a public repo.
Fallback if the runner host is down: set it back to `'"ubuntu-latest"'` (spends Actions minutes).
Operations are in the private runner docs.

⚠️ **`release` promotes the `:sha-<head>` image that `ci / image` published.** `app.yml` always publishes it, so a
caller leaves `release`'s `publish-image` input on.

⚠️ Keep `.dockerignore` at the **repo root** — it is honored for a build whose context is the repo
root even when the Dockerfile is in a subdirectory. A `deploy/.dockerignore` is silently ignored.

## App contract (`app.yml`)

Every caller uses this workflow. The older `node-ci.yml` and `go-ci.yml` are gone from `main`; a caller still pinned
to a commit that has them keeps working, but must not take a digest bump past their removal.

One file, `.github/build.yml` in the app repo, is the whole build configuration. `app.yml` reads it with
`contract/read.sh` and runs `verify`, `integration` and `image` from it. The keys and their rules are in
[`contract/schema.json`](contract/schema.json); what each family's shared Dockerfile expects from the app
is in [`dockerfiles/README.md`](dockerfiles/README.md). The only workflow inputs are `runner` and `path`.

The caller's `.github/workflows/ci.yml` (name the job `ci` so the checks come out as `ci / verify`,
`ci / integration`, `ci / image`; release stays in the caller):

```yaml
name: ci
on:
  push: { branches: [main] }
  pull_request: { branches: [main] }
jobs:
  ci:
    permissions:
      contents: read
      packages: write
    uses: brandtstack/workspace-ci/.github/workflows/app.yml@<40-hex> # main
    with:
      runner: '["self-hosted","priv-ci"]'
```

Release (on push to main) stays in the caller, in **contract mode**: `release.yml` reads the Coolify app
from `build.yml`'s `coolify-app` (exactly one, for now), and before deploying writes the app's
`ports_exposes` (80) and health check from the family: next/spa get Coolify's http check on
`127.0.0.1:80/healthz`; go keeps Coolify's check off and relies on the image's own `HEALTHCHECK`
(distroless has no shell for Coolify's `CMD-SHELL` check). That a disabled Coolify check keeps the image
`HEALTHCHECK` on the deployed container is source-read only; the
first go pilot measures it with `docker inspect` on the host. The values are read back before the deploy;
hand edits in Coolify are overwritten on every release. The sync runs before the image is promoted
and the tag pushed, so a refused or mismatched sync leaves nothing minted and a re-run retries it.
`coolify-app` must be a Coolify uuid (8-64 lowercase letters and digits).

```yaml
  release:
    if: github.event_name == 'push'
    needs: [ci]
    uses: brandtstack/workspace-ci/.github/workflows/release.yml@<40-hex> # main
    permissions: { contents: write, packages: write, pull-requests: read }
    with:
      runner: '["self-hosted","priv-ci"]'
      contract: true          # no coolify-app-uuid: build.yml names the app
    secrets:
      COOLIFY_TOKEN:           ${{ secrets.COOLIFY_TOKEN }}
      CF_ACCESS_CLIENT_ID:     ${{ secrets.CF_ACCESS_CLIENT_ID }}
      CF_ACCESS_CLIENT_SECRET: ${{ secrets.CF_ACCESS_CLIENT_SECRET }}
```

Rolling back across a port change (an app's first contract release moved it to 80) is not a plain tag
swap: PATCH `ports_exposes` and the health check back to the old values first.

Repo-specific image probes do not carry over: if a required `headers` job builds the repo's own
Dockerfile, so on adoption it must move to pulling `:sha-<head>` after `ci / image` instead (spec step 3
handles it).

### Custom family and smoke keys

`family: custom` keeps the repo's own Dockerfile, with exactly one of `node` or `go`. The release writes the Coolify
settings of that toolchain (node: next/spa's http check; go: check off, image `HEALTHCHECK` required). Three optional
keys make an image that needs env, random keys or a database bootable in the smoke: `smoke-env` (a map, passed as an
`--env-file`, so values may contain spaces), `smoke-random` (a map of name to byte count, each set per run to fresh
random base64) and `smoke-postgres` (database names created in a throwaway Postgres at
`postgresql://smoke:smoke@smoke-pg:5432/<name>?sslmode=disable`). They are smoke-only. Rules, the image gates and a
wrapper-ENTRYPOINT recipe: [`dockerfiles/README.md`](dockerfiles/README.md#custom).

`skip-image-checks: true` is for a `custom` image that cannot boot in CI and is not deployed by Coolify. The `image` job
still builds it, publishes `:sha-<head>` and fails a root image, but skips the port check, the boot and the health
checks. It is refused together with `coolify-app` or any smoke key. The image carries the label
`workspace-ci.image-checks-skipped=true`, and `release.yml` refuses to deploy a labelled image to Coolify, before it
changes anything (an image whose labels cannot be read also stops the release). That refusal lives in `release.yml`, so
a repo adopting the key must move its `app.yml` and `release.yml` pins together. Such a repo releases tag-only:
`contract: false` and no `coolify-app-uuid` (a contract-mode release needs a `coolify-app`). `allow-root-image: true`
(requires `skip-image-checks`) lets a root image pass, with a warning in the log. The run summary records both. Each
repo opts in from its own `build.yml`, so review a change to either key like a change to the workflow, for example with
a CODEOWNERS entry on `.github/build.yml`.

### Building it locally

`scripts/build-image [--no-verify] [--no-smoke] [<repo dir>]` builds what the `image` job builds, with
podman only: it reads the repo's `build.yml`, runs `npm run verify` (or `make verify` for go), builds
`localhost/<repo>:dev` with the same Dockerfile, target and build args (a `next` build with `migrate: true`
also passes `--security-opt label=disable`, because the podman VM's SELinux denies its `prisma/` bind mount;
this turns off SELinux separation for every build step, dependency install and app build included, so do not
rely on that build to isolate untrusted code), then boots it with `-p 127.0.0.1:8080:80` and polls `/healthz` for 60 s. It exits non-zero on any failure and prints the
container log. A contract with `private-registry: true` needs `NPM_TOKEN` exported. `BUILD_IMAGE_PORT`
changes the host port and `BUILD_IMAGE_SMOKE_TIMEOUT` the 60 s wait. It applies `smoke-env`, `smoke-random` and `smoke-postgres` the way app.yml does (one env file, a throwaway `docker.io/library/postgres:16.15-alpine` on a per-run podman network, reachable as `smoke-pg`), and removes them afterwards. Tests (fake podman): `tests/build-image/test_build_image.py`.

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

### GitHub Releases: "Latest" is what runs in production

Every new tag gets a GitHub Release whose notes are the PR titles since the previous `v*` tag. Its
first line says where that version stands:

| First line | Meaning | Latest? |
| --- | --- | --- |
| `⏳ deploying` | tag minted, Coolify deploy queued | no |
| `✅ live in production <UTC>` | Coolify reported the deployment `finished` and the app still points at this tag | **yes** |
| `❌ deploy failed (<status>)` | Coolify ended `failed` / `cancelled-by-user` / `error`; the job fails | no |
| `❌ deploy not started` | a Coolify step before the wait failed (set env, point, `/deploy`); the job fails | no |
| `❔ deploy state unknown (timeout)` | no terminal status within 10 min; the job fails | no |
| `❔ created on re-run; deploy state not checked` | a re-run found the tag but no Release | no |
| `⏸ superseded by <tag>` | the deploy ended (any status) after a newer merge had already moved the app on | no |

So the repo page's **Latest** badge is the version Coolify confirmed. A tag-only caller (no
`coolify-app-uuid`) has nothing to wait for; its Release is Latest at once, with no state line.

- **The Release step runs after `Deploy`**, so a GitHub API hiccup can never stop a deploy. It
  still runs when a Coolify step failed, so that failure is labelled `❌ deploy not started`.
- **A re-run never deploys.** Redeploying on "Re-run jobs" would undo a manual tag-swap rollback
  (the rolled-back version is still the highest tag). A re-run only creates a missing Release, as
  `❔ created on re-run; deploy state not checked`. After a failed deploy, fix the cause, run
  `/deploy` by hand, and set the state line with `gh release edit`.
- A `⏸ superseded` mark also covers an older deploy that Coolify cancelled or failed after a newer
  merge had already pointed the app elsewhere.
- Waiting holds the runner for the deploy's length (~70 s for a Next.js app, at most 10 min). The
  runner host has a fixed number of job slots, so each release holds one while it waits.
- ⚠️ `finished` is Coolify's word. Whether it implies the container's healthcheck passed is **not
  verified yet**.
- **Rollback (tag swap)** bypasses this workflow, so move the badge by hand:
  `gh release edit vOLD --latest`, and change its first line to `↩ rolled back to (from vNEW)`.
- Everything stays inside the `contents: write` callers already grant. GitHub Deployments would need
  `deployments: write`, and a reusable workflow cannot request more than its caller grants.

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
  wherever the contract has `integration: false`, so require it only on repos
  that actually run it. This repo runs only its own `tests` workflow on
  GitHub-hosted runners, and its ruleset requires a PR with **no** status check.
- Branch protection needs GitHub Team on private repos; Free enforces rulesets
  on public repos only.

## The script contract

Each repo defines what it can run, so the workflow and Dockerfile stay identical everywhere:

- `verify` — everything runnable with no external services (lint / typecheck / test, whichever exist)
- `verify:integration` — optional; only where tests need a real service

The shared Dockerfiles do not run `npm run verify` (see
[`dockerfiles/README.md`](dockerfiles/README.md)). `ci / verify` runs it and gates the commit;
`ci / image` and `release` need it, so a failed `verify` fails the PR and no image is ever
published. Make `ci / verify` and `ci / image` required checks: nothing else gates the image.

## Coolify builds nothing (since 2026-09-01)

Every application is `build_pack: dockerimage` with auto-deploy **off**, pulling
the `vX.Y.Z` tag this pipeline promoted. CI is the only thing that deploys, and a
rollback is a tag swap.

`release.yml` upserts `APP_VERSION` on the application (runtime, not build-time —
build-time vars are rendered as Docker `ARG`s into the public build log) before
pointing it at the new tag, so the running container reports the minted version
rather than a `SOURCE_COMMIT` short SHA.

## Renovate grouping

The shared preset (`default.json`) folds `digest`, `pin` and `pinDigest` updates into the weekly `all non-major` group alongside `minor` and `patch`, so action pins and `workspace-ci` digest bumps no longer open a PR each. The group has `automerge: true`, so these bumps now merge on their own once the required checks are green (before this they waited for a manual merge): a `workspace-ci` change reaches each caller through its weekly group PR, gated only by that repo's CI, and the merge deploys. The `pr-review.yml` exclusion rule sits after the group rule and still wins. `lockFileMaintenance` stays on but runs `before 6am on the first day of the month` instead of weekly. `rebaseWhen: conflicted` is deliberately not set: the rulesets sampled have `strict_required_status_checks_policy: true`, so automerge needs up-to-date branches and the rebase re-runs stay.

## Parked majors in the preset

`default.json` disables or caps these; each rule's `description` says when to unpark it.

| Package | Rule | Why |
| --- | --- | --- |
| `typescript` | `<7` | typescript-eslint throws on any TS 7.x |
| `eslint` | `<10` where already on 9 | eslint-plugin-react errors on ESLint 10 |
| `@babel/plugin-transform-runtime` | no major | consumers pin 7.29.7 via `overrides`; v8 drops that guard, then the next install ERESOLVEs |

This repo's own `renovate.json` also parks the `postgres` docker major (`app.yml`'s integration service
stays on 16 to match the production database's major version).
