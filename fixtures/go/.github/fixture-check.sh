#!/usr/bin/env bash
# Fixture-only check, run by app.yml's image job in brandtstack/workspace-ci while the smoke container
# is up. $1 = base URL. Proves the go-dir/go-main subdirectory build serves /healthz.
set -euo pipefail
base="${1:?base url}"
[ "$(curl -fsS "$base/healthz")" = "ok" ] || { echo "fixture-check: /healthz body is not ok" >&2; exit 1; }
echo "fixture-check: /healthz ok"
