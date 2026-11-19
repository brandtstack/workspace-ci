#!/usr/bin/env bash
# Fixture-only check, run by app.yml's image job in brandtstack/workspace-ci while the smoke container is up.
# $1 = base URL. Proves a multi-file prisma/schema/ reached /opt/migrate (both files, and no stray
# schema.prisma), and that the image went through start.sh with migrate: true.
set -euo pipefail
base="${1:?base url}"
curl -fsS "$base/healthz" >/dev/null
got="$(docker exec smoke ls /opt/migrate/prisma/schema | sort | tr '\n' ' ')"
[ "$got" = "base.prisma item.prisma " ] || { echo "fixture-check: /opt/migrate/prisma/schema holds '${got}', want 'base.prisma item.prisma '" >&2; exit 1; }
echo "fixture-check: both schema files are in /opt/migrate/prisma/schema"
if docker exec smoke test -e /opt/migrate/prisma/schema.prisma; then
  echo "fixture-check: a single-file schema.prisma was copied beside the directory" >&2
  exit 1
fi
docker exec smoke ls /opt/migrate/prisma/migrations >/dev/null
echo "fixture-check: migrations present, no schema.prisma"
if docker logs smoke 2>&1 | grep -q '^\[start\] SKIP_MIGRATIONS set'; then
  echo "fixture-check: start.sh ran with migrate: true and honoured SKIP_MIGRATIONS"
else
  echo "fixture-check: no start.sh SKIP_MIGRATIONS line in the container log" >&2
  docker logs smoke >&2 || true
  exit 1
fi
