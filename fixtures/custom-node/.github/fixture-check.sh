#!/usr/bin/env bash
# Fixture-only check, run by app.yml's image job in brandtstack/workspace-ci while the smoke container is up.
# $1 = base URL. Proves smoke-env reached the container with its space intact, and that the smoke really
# bypassed the image's ENTRYPOINT wrapper (smoke-args), which would have exited 1 without WRAPPER_OK.
set -euo pipefail
base="${1:?base url}"
[ "$(curl -fsS "$base/greeting")" = "hello world" ] || { echo "fixture-check: /greeting is not 'hello world'" >&2; exit 1; }
echo "fixture-check: smoke-env GREETING reached the container"
[ "$(docker image inspect --format '{{json .Config.Entrypoint}}' ci-image:local)" = '["/app/entrypoint.sh"]' ] \
  || { echo "fixture-check: the image ENTRYPOINT is not the wrapper" >&2; exit 1; }
[ "$(docker inspect --format '{{json .Config.Entrypoint}}' smoke)" = '["/app/start.sh"]' ] \
  || { echo "fixture-check: the smoke container did not bypass the wrapper" >&2; exit 1; }
echo "fixture-check: image ENTRYPOINT is the wrapper, the smoke bypassed it"
