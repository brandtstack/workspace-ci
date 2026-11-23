#!/usr/bin/env bash
# Fixture-only page check, run by app.yml's image job in brandtstack/workspace-ci while the smoke
# container is up. $1 = base URL. Proves the contract build-args reached the build, and that the image
# started through start.sh with migrate: true (the smoke sets SKIP_MIGRATIONS=1).
set -euo pipefail
base="${1:?base url}"
body="$(curl -fsS "$base/")"
case "$body" in
  *'<span id="fixture">1</span>'*) echo "fixture-check: build-arg NEXT_PUBLIC_FIXTURE=1 rendered on /" ;;
  *) echo "fixture-check: / does not render NEXT_PUBLIC_FIXTURE=1" >&2; echo "$body" >&2; exit 1 ;;
esac
# Capture first: under pipefail, grep -q closing the pipe early kills docker logs (SIGPIPE) and reads as no match.
logs="$(docker logs smoke 2>&1)"
if grep -q '^\[start\] SKIP_MIGRATIONS set' <<<"$logs"; then
  echo "fixture-check: start.sh ran with migrate: true and honoured SKIP_MIGRATIONS"
else
  echo "fixture-check: no start.sh SKIP_MIGRATIONS line in the container log" >&2
  printf '%s\n' "$logs" >&2
  exit 1
fi
