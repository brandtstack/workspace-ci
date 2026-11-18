#!/bin/sh
# Fixture start hook: proves the shared spa image runs nginx/entrypoint.d/*.sh as nginx and that
# /etc/nginx/runtime/ is writable. Always exit 0: the stock entrypoint aborts on a failing hook.
printf 'set $fixture_hook "ran";\n' > /etc/nginx/runtime/fixture-hook.conf 2>/dev/null \
  || echo "50-fixture-hook.sh: could not write /etc/nginx/runtime/fixture-hook.conf" >&2
exit 0
