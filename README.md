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
  value deadlocks), required checks `verify` / `image` / `pr-title`, block
  force pushes.
- Branch protection needs GitHub Team on private repos; Free enforces rulesets
  on public repos only.

## The script contract

Each repo defines what it can run, so the workflow and Dockerfile stay identical everywhere:

- `verify` — everything runnable with no external services (lint / typecheck / test, whichever exist)
- `verify:integration` — optional; only where tests need a real service

The Dockerfile builder stage runs `npm run verify` before `npm run build`.
A failed Coolify build never swaps the running container — that is the deploy
gate. **It stays even with branch protection:** protection gates the *merge*,
the Dockerfile gates the *deploy*, including a Coolify redeploy of an older
commit.

## Not yet done (Phase 2)

Coolify still builds its own image from source, with no `.git`, so
`APP_VERSION` falls back to the runtime `SOURCE_COMMIT` short SHA. The version
only reaches the running container once Coolify deploys the GHCR image
instead of building. That is a separate change.
