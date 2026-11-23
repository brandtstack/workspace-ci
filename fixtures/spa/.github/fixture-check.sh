#!/usr/bin/env bash
# Fixture-only page check, run by app.yml's image job in brandtstack/workspace-ci while the smoke
# container is up. $1 = base URL. A SPA renders client-side, so the build-arg is checked where Vite
# inlined it: in the built JS bundle that index.html references.
set -euo pipefail
base="${1:?base url}"
# The start hook (nginx/entrypoint.d/) ran as nginx and wrote into /etc/nginx/runtime/.
hook="$(curl -fsS "$base/hook")"
if [ "$hook" != "ran" ]; then
  echo "fixture-check: /hook says '$hook', want 'ran': the entrypoint.d hook did not run or could not write /etc/nginx/runtime/" >&2
  exit 1
fi
echo "fixture-check: entrypoint.d hook ran and wrote the runtime snippet"
html="$(curl -fsS "$base/")"
js="$(printf '%s' "$html" | grep -o 'src="/assets/[^"]*\.js"' | head -1 | sed 's/^src="//; s/"$//')"
[ -n "$js" ] || { echo "fixture-check: no /assets/*.js script in /" >&2; exit 1; }
bundle="$(curl -fsS "$base$js")"
# Vite inlines import.meta.env.VITE_FIXTURE as a string literal ("1") next to the element id "fixture".
# The quote character and the exact shape depend on the minifier, so accept a quoted 1 within a short
# window of the fixture id, in either order. If VITE_FIXTURE never reached the build, no literal 1 is there.
q='["'"'"'`]'
# A here-string, not a pipe: under pipefail, grep -q closing the pipe early on a large multi-line bundle reads as no match.
if grep -Eq "fixture${q}.{0,40}${q}1${q}|${q}1${q}.{0,40}fixture${q}" <<<"$bundle"; then
  echo "fixture-check: build-arg VITE_FIXTURE=1 inlined in $js"
else
  echo "fixture-check: this check looks for the value of VITE_FIXTURE (1) in the built bundle $js, next to the id 'fixture'; not found." >&2
  echo "fixture-check: either the build-arg did not reach the build, or a Vite/minifier update changed the output shape (then fix this regex)." >&2
  exit 1
fi
