# workspace-ci

Shared CI configuration for the priv workspace. **Public** because GitHub Free
restricts reusing workflows from private repos.

## Consuming it

`.github/workflows/ci.yml` in each repo:

```yaml
name: ci
on:
  push: { branches: [main] }
  pull_request: { branches: [main] }
jobs:
  ci:
    uses: adrianbrandt/workspace-ci/.github/workflows/node-ci.yml@main
    with:
      smoke-port: '80'
      smoke-path: '/'
```

`renovate.json`:

```json
{ "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["github>adrianbrandt/workspace-ci"] }
```

## The script contract

Each repo defines what it can run, so the workflow and Dockerfile stay identical everywhere:

- `verify` — everything runnable with no external services (lint / typecheck / test, whichever exist)
- `verify:integration` — optional; only where tests need a real service

The Dockerfile builder stage runs `npm run verify` before `npm run build`.
A failed Coolify build never swaps the running container — that is the deploy gate.
