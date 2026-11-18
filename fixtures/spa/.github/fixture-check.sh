#!/usr/bin/env bash
# Fixture-only page check, run by app.yml's image job in brandtstack/workspace-ci while the smoke
# container is up. $1 = base URL. A SPA renders client-side, so the build-arg is checked where Vite
# inlined it: in the built JS bundle that index.html references.
set -euo pipefail
base="${1:?base url}"
html="$(curl -fsS "$base/")"
js="$(printf '%s' "$html" | grep -o 'src="/assets/[^"]*\.js"' | head -1 | sed 's/^src="//; s/"$//')"
[ -n "$js" ] || { echo "fixture-check: no /assets/*.js script in /" >&2; exit 1; }
bundle="$(curl -fsS "$base$js")"
# Vite inlines import.meta.env.VITE_FIXTURE as a string literal passed to fixtureValue(); the quote
# character depends on the minifier, hence [^a-z]. An unset arg would leave no literal here.
if printf '%s' "$bundle" | grep -Eq 'id:[^a-z]fixture[^a-z],children:[A-Za-z_$]+\([^a-z]1[^a-z]\)'; then
  echo "fixture-check: build-arg VITE_FIXTURE=1 inlined in $js"
else
  echo "fixture-check: VITE_FIXTURE=1 not found in $js" >&2
  exit 1
fi
