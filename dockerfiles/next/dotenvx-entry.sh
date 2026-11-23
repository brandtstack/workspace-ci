#!/bin/sh
# Wrapper (R45) for the ENTRYPOINT and for the Coolify scheduled tasks (they bypass the ENTRYPOINT).
# dotenvx's library parse() path prints the first characters of a private key in some wrong-key errors
# (the CLI run/get paths print key names only); this wrapper guards against that text ever reaching the
# CLI output, and a failing container's or task's output lands in durable Coolify logs. So: a silent precheck first, and only a
# passing precheck reaches the real run. Failure prints one fixed line of ours, nothing else.
# Both runs use the same flags: no cloud (Armor), OS keychain or password-manager key lookup, ever.
# Origin: an app repo's wrapper.
DX=/usr/local/bin/dotenvx

if ! "$DX" run --strict -f .env.production --no-armor --no-native --no-1password --no-bitwarden -- true >/dev/null 2>&1; then
  echo "dotenvx: precheck failed" >&2
  exit 1
fi
exec "$DX" run --strict -f .env.production --no-armor --no-native --no-1password --no-bitwarden -- "$@"
