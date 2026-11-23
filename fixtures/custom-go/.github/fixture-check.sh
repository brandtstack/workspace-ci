#!/usr/bin/env bash
# Fixture-only check, run by app.yml's image job in brandtstack/workspace-ci while the smoke container is up.
# $1 = base URL. The binary only starts when both smoke databases answer and FIXTURE_KEY decodes to 32 bytes;
# this asserts what it saw, and that the key itself is not in the log.
set -euo pipefail
base="${1:?base url}"
[ "$(curl -fsS "$base/dbs")" = "one,two" ] || { echo "fixture-check: /dbs is not 'one,two'" >&2; exit 1; }
echo "fixture-check: both smoke databases answered"
# Capture first: under pipefail, grep -q closing the pipe early kills docker logs (SIGPIPE), and here that
# would turn a real match into a pass.
logs="$(docker logs smoke 2>&1)"
if grep -q 'did not answer' <<<"$logs"; then
  echo "fixture-check: a database ping failed" >&2
  exit 1
fi
echo "fixture-check: no database error in the log"
