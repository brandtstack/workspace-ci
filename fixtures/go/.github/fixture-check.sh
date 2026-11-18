#!/usr/bin/env bash
# Fixture-only check, run by app.yml's image job in brandtstack/workspace-ci while the smoke container
# is up. $1 = base URL. Proves the go-dir/go-main subdirectory build serves /healthz, that the image
# user could write /data at start, and that the healthcheck subcommand fails when nothing answers.
set -euo pipefail
base="${1:?base url}"
[ "$(curl -fsS "$base/healthz")" = "ok" ] || { echo "fixture-check: /healthz body is not ok" >&2; exit 1; }
echo "fixture-check: /healthz ok"
[ "$(curl -fsS "$base/data-check")" = "ok" ] || { echo "fixture-check: /data write not visible" >&2; exit 1; }
echo "fixture-check: /data writable"
if docker exec -e PORT=1 smoke /app healthcheck; then
  echo "fixture-check: healthcheck exited 0 with nothing on the port" >&2
  exit 1
fi
echo "fixture-check: healthcheck fails closed"
