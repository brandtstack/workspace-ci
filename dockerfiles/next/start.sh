#!/bin/sh
# CMD of every shared next image (both runtimes; under dotenvx the ENTRYPOINT wrapper runs this, so the
# environment is already decrypted). With contract `migrate: true` the image has /opt/migrate/prisma.config.ts:
# apply pending migrations first, and refuse to start the server if that fails. SKIP_MIGRATIONS (any non-empty
# value) skips them: the CI smoke has no database. Without `migrate`, /opt/migrate is empty and this only execs.
set -e
if [ -f /opt/migrate/prisma.config.ts ]; then
  if [ -n "${SKIP_MIGRATIONS:-}" ]; then
    echo "[start] SKIP_MIGRATIONS set: starting without migrations"
  else
    echo "[start] applying database migrations"
    if ! (cd /opt/migrate && CHECKPOINT_DISABLE=1 node node_modules/prisma/build/index.js migrate deploy); then
      echo "[start] migration failed: refusing to start the server" >&2
      exit 1
    fi
    echo "[start] migrations applied"
  fi
fi
exec node /app/server.js
