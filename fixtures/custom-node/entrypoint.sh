#!/bin/sh
# The image ENTRYPOINT: a stand-in for a real wrapper (dotenvx and the like) that needs something CI never
# has. app.yml's smoke bypasses it with `--entrypoint /app/start.sh` (contract smoke-args).
[ -n "${WRAPPER_OK:-}" ] || { echo "entrypoint: WRAPPER_OK is not set" >&2; exit 1; }
exec /app/start.sh
