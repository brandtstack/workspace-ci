#!/bin/sh
# CMD of every shared next image (both runtimes; under dotenvx the ENTRYPOINT wrapper runs this, so the
# environment is already decrypted). With contract `migrate: true` the image has /opt/migrate/prisma.config.ts:
# apply pending migrations first, and refuse to start the server if that fails. SKIP_MIGRATIONS=1 or true skips
# them (the CI smoke has no database unless the contract sets smoke-postgres); unset or empty migrates; any other
# value refuses to start, so a `false` or `0` can never skip by accident. The value is not printed. Without
# `migrate`, /opt/migrate is empty and this only execs.
#
# The migration runs with a minimal environment: PATH, HOME, CHECKPOINT_DISABLE and DATABASE_URL only, never
# the app's other (decrypted) secrets. DATABASE_URL there is MIGRATE_DATABASE_URL when set (a schema-owner
# role), else DATABASE_URL. MIGRATE_DATABASE_URL is unset before the server starts, so the server never holds
# the DDL credential. Prisma's own output is not printed: it names hosts and databases, and its error text is
# not ours to vouch for. Only fixed lines, migration names and the Prisma error code reach the log.
# Overridable for tests only: START_MIGRATE_DIR, START_SERVER.
set -e
dir="${START_MIGRATE_DIR:-/opt/migrate}"
server="${START_SERVER:-/app/server.js}"

if [ -f "$dir/prisma.config.ts" ]; then
  case "${SKIP_MIGRATIONS:-}" in
    (1|true) skip=1 ;;
    ("") skip= ;;
    (*) echo "[start] SKIP_MIGRATIONS must be 1, true or empty: refusing to start the server" >&2; exit 1 ;;
  esac
  if [ -n "$skip" ]; then
    echo "[start] SKIP_MIGRATIONS set: starting without migrations"
  else
    echo "[start] applying database migrations"
    rc=0
    out="$(
      url="${MIGRATE_DATABASE_URL:-${DATABASE_URL:-}}"
      for v in $(env | sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p'); do
        case "$v" in (PATH|HOME) ;; (*) unset "$v" 2>/dev/null || true ;; esac
      done
      export DATABASE_URL="$url" CHECKPOINT_DISABLE=1
      cd "$dir" && node node_modules/prisma/build/index.js migrate deploy 2>&1
    )" || rc=$?
    printf '%s\n' "$out" | grep -E '^(Applying migration `[^`]*`|No pending migrations to apply\.|All migrations have been successfully applied\.|[0-9]+ migrations? found in prisma/migrations|Migration name: [A-Za-z0-9_]+)$' | sed 's/^/[start] prisma: /' || true
    if [ "$rc" -ne 0 ]; then
      code="$(printf '%s\n' "$out" | grep -oE 'P[0-9]{4}' | head -1 || true)"
      echo "[start] migration failed (exit ${rc}${code:+, Prisma ${code}}): refusing to start the server" >&2
      exit 1
    fi
    echo "[start] migrations applied"
  fi
fi
unset MIGRATE_DATABASE_URL
exec node "$server"
