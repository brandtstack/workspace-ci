#!/usr/bin/env bash
# Reads and validates a repo's .github/build.yml, the shared build contract.
#   contract/read.sh [<path-to-build.yml>]      (default .github/build.yml)
# Exit 0: writes key=value outputs to $GITHUB_OUTPUT (stdout when unset); multi-line values use the
# GitHub heredoc form. Exit 1: one `::error::build.yml: <key>: <reason>` line per problem on stderr.
# Needs bash, curl, jq (1.7+), coreutils. No Python, no YAML parser: it downloads a pinned mikefarah/yq.
# `dockerfile` output: next/spa/go -> absolute path of the shared Dockerfile in this repo's dockerfiles/;
# custom -> the contract's value verbatim (a path relative to the build context).
# For the shared families this output is informational only (absolute on the runner that read it, and the
# image job runs on another): app.yml and scripts/build-image compute their own Dockerfile path.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS_ROOT="$(dirname "$HERE")"
FILE="${1:-.github/build.yml}"

YQ_VERSION=4.47.2
# sha256 of the release binaries (release `checksums` file; each also hashed by hand when pinned)
yq_sha() { # bash 3.2 (macOS) has no associative arrays
  case "$1" in
    linux_amd64) echo 1bb99e1019e23de33c7e6afc23e93dad72aad6cf2cb03c797f068ea79814ddb0 ;;
    linux_arm64) echo 05df1f6aed334f223bb3e6a967db259f7185e33650c3b6447625e16fea0ed31f ;;
    darwin_amd64) echo b945c250a308f0dfcd3f034688e5e4a5275df95788b597f81a4ab450e74175d5 ;;
    darwin_arm64) echo 4ccc7f2f5f6f37804d70ad211a287b1b589f67024ecb77586c77106030424b9f ;;
  esac
}

die() { echo "::error::build.yml: $1" >&2; exit 1; }

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

ensure_yq() {
  local os arch plat want dir cached
  case "$(uname -s)" in Linux) os=linux ;; Darwin) os=darwin ;; *) die "<yq>: unsupported OS $(uname -s)" ;; esac
  case "$(uname -m)" in x86_64|amd64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; *) die "<yq>: unsupported arch $(uname -m)" ;; esac
  plat="${os}_${arch}"
  want="$(yq_sha "$plat")"
  if [ -n "${RUNNER_TOOL_CACHE:-}" ]; then dir="$RUNNER_TOOL_CACHE/yq/$YQ_VERSION"
  else dir="${XDG_CACHE_HOME:-$HOME/.cache}/workspace-ci/yq/$YQ_VERSION"; fi
  cached="$dir/yq"
  if [ ! -e "$cached" ]; then
    mkdir -p "$dir"
    local tmp="$dir/yq.tmp.$$"
    curl -fsSL --retry 3 -o "$tmp" "https://github.com/mikefarah/yq/releases/download/v$YQ_VERSION/yq_$plat" \
      || { rm -f "$tmp"; die "<yq>: download failed"; }
    mv "$tmp" "$cached"
  fi
  [ -f "$cached" ] || die "<yq>: $cached is not a regular file. Not executed"
  # The tool cache can be shared with other jobs, so checking it and then executing it would leave a window to
  # swap the file. Copy it into a private directory, verify the copy, and execute only the copy.
  YQ_DIR="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/yq.XXXXXX")" || die "<yq>: could not create a private directory"
  YQ="$YQ_DIR/yq"
  # This is the script's only EXIT trap. Anything else that needs one must extend it, or the copy is left behind.
  trap 'rm -f "$YQ"; rmdir "$YQ_DIR" 2>/dev/null || true' EXIT
  cp "$cached" "$YQ" || die "<yq>: could not copy $cached"
  if [ "$(sha256_of "$YQ")" != "$want" ]; then
    die "<yq>: checksum mismatch for $cached (expected the pinned v$YQ_VERSION $plat binary). Not executed. If it is a corrupt cache, delete $cached and rerun"
  fi
  chmod 700 "$YQ"
}

[ -f "$FILE" ] || die "<file>: $FILE not found"

ensure_yq

# YAML floats below the root (node: 24.0, go: 1.30) are wrapped in a {"!!float": n} object: yq's JSON output would
# otherwise normalise them (24.0 -> 24, 1.30 -> 1.3) and the type check could not see the difference.
JSON="$("$YQ" -o=json '(.. | select(tag == "!!float" and (path | length) > 0)) |= {"!!float": .}' "$FILE" 2>/dev/null)" || die "<file>: not valid YAML"
[ -n "$JSON" ] || JSON=null
[ "$(printf '%s' "$JSON" | jq -s length)" -le 1 ] || die "<file>: more than one YAML document (only one is allowed)"

# Text from build.yml reaches the runner's log parser only through this filter: `%` is escaped (the runner decodes
# %0A in ::error:: text), control characters are removed (no second log line), and `##[` commands are broken up.
# Kept identical to JQ_SAFE in .github/workflows/release.yml (tested).
JQ_SAFE='def safe: tostring | .[:300] | gsub("%"; "%25") | gsub("[[:cntrl:]]"; "") | gsub("##\\["; "# #[");'

# What a reader sees must be what the contract means. A merge key can override an earlier key, and yq drops or applies
# it before validate.jq runs, so anchors, aliases and merge keys are refused anywhere, on values and on keys.
SPECIAL="$("$YQ" '[(.. | select(kind == "alias" or anchor != "")),
  (.. | select(kind == "map") | keys | .[] | select(kind == "alias" or anchor != "" or tag == "!!merge" or . == "<<"))] | length' \
  "$FILE" 2>/dev/null)" || die "<file>: could not read the YAML structure"
case "$SPECIAL" in
  0) ;;
  *) die "<file>: YAML anchors, aliases and merge keys are not allowed" ;;
esac

# yq keeps both entries of a repeated key and jq would take the last, so a later line could silently override an
# earlier one. Every map is checked, selected by node kind so a custom tag cannot skip it. Keys are compared as text,
# which is what they become in the JSON: a plain TRUE (a boolean to yq) and a quoted "TRUE" are the same key there.
DUPES="$("$YQ" -o=json -I=0 '.. | select(kind == "map") | {"path": (path | join(".")), "keys": (keys | map(to_string))}' "$FILE" 2>/dev/null \
  | jq -rs "$JQ_SAFE"'
  .[] | .path as $p | .keys | group_by(.) | map(select(length > 1) | .[0] | tostring) | .[]
  | if $p == "" then "::error::build.yml: \(safe): duplicate top-level key (only one is allowed)"
    else "::error::build.yml: \("\($p).\(.)" | safe): duplicate key (only one is allowed)" end')" \
  || die "<file>: could not read the keys"
if [ -n "$DUPES" ]; then printf '%s\n' "$DUPES" >&2; exit 1; fi

# default go-main name: the repo name, else the directory that contains .github/
if [ -n "${GITHUB_REPOSITORY:-}" ]; then DEFNAME="${GITHUB_REPOSITORY##*/}"
else DEFNAME="$(basename "$(cd "$(dirname "$FILE")/.." && pwd)")"; fi

RESULT="$(printf '%s' "$JSON" | jq -c --arg ws "$WS_ROOT" --arg defname "$DEFNAME" -f "$HERE/validate.jq")"

if [ "$(printf '%s' "$RESULT" | jq 'has("errors")')" = true ]; then
  printf '%s' "$RESULT" | jq -r "$JQ_SAFE"'.errors[] | "::error::build.yml: " + safe' >&2
  exit 1
fi

DELIM="ghadelim_$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')"
KEYS='["family","dockerfile","node","go","prisma","dotenvx","integration","favicon","build-args","private-registry","coolify-app","smoke-args","go-main","go-dir","migrate","smoke-env-b64","smoke-postgres","smoke-random","skip-image-checks","allow-root-image"]'
RENDERED="$(printf '%s' "$RESULT" | jq -r --arg d "$DELIM" --argjson keys "$KEYS" '
  .outputs as $o | $keys[] | . as $k | $o[$k] as $v
  | if ($v | test("\n")) then "\($k)<<\($d)\n\($v)\n\($d)" else "\($k)=\($v)" end')"

if [ -n "${GITHUB_OUTPUT:-}" ]; then printf '%s\n' "$RENDERED" >> "$GITHUB_OUTPUT"
else printf '%s\n' "$RENDERED"; fi
