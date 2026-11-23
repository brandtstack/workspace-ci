#!/bin/sh
# A generic script shared from an app repo; only this header and the usage line changed.
# Refuses an env file that holds a plaintext secret. Fail closed: every line must be blank, a comment, or
# `NAME=value` (optionally `export NAME=value`) with NAME matching [A-Za-z_][A-Za-z0-9_]*; any other line
# is refused by line number. Every non-empty value must start with `encrypted:` (dotenvx), except
# DOTENV_PUBLIC_KEY / DOTENV_PUBLIC_KEY_<ENV>, whose value must be a 66-hex-digit public key. A quoted value
# that does not close on its own line (multi-line) is refused, and its continuation lines are skipped unread:
# ciphertext is always one line, and a continuation line must never be mistaken for a name. Run in the
# next.Dockerfile's runtime-dotenvx stage, against the .env.production it copies in. Prints variable NAMES and line
# numbers only, never a value.
#
#   sh check-env-encrypted.sh <env-file>
set -eu

[ $# -eq 1 ] || { echo "usage: $0 <env-file>" >&2; exit 2; }
file=$1
[ -f "$file" ] || { echo "check-env-encrypted: $file not found" >&2; exit 1; }

cr=$(printf '\r')
bad=0
n=0
open=''
while IFS= read -r line || [ -n "$line" ]; do
  n=$((n + 1))
  line=${line%"$cr"}
  if [ -n "$open" ]; then
    # inside a multi-line value: skip until a line ends with the opening quote
    case "$(printf '%s' "$line" | sed -e 's/[[:space:]]*$//')" in *"$open") open='' ;; esac
    continue
  fi
  trimmed=$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//')
  case "$trimmed" in '' | \#*) continue ;; esac
  case "$trimmed" in export[[:space:]]*) trimmed=$(printf '%s' "${trimmed#export}" | sed -e 's/^[[:space:]]*//') ;; esac
  name=$(printf '%s' "$trimmed" | sed -n -e 's/^\([A-Za-z_][A-Za-z0-9_]*\)[[:space:]]*=.*$/\1/p')
  if [ -z "$name" ]; then
    echo "check-env-encrypted: line $n is not NAME=value" >&2
    bad=1
    continue
  fi
  value=${trimmed#*=}
  value=$(printf '%s' "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
  for q in '"' "'" '`'; do
    case "$value" in
      "$q"*"$q") [ "${#value}" -ge 2 ] && break ;;
      "$q"*) open=$q; break ;;
    esac
  done
  if [ -n "$open" ]; then
    echo "check-env-encrypted: $name has a multi-line value (line $n)" >&2
    bad=1
    continue
  fi
  case "$value" in
    \"*\") value=${value#\"}; value=${value%\"} ;;
    \'*\') value=${value#\'}; value=${value%\'} ;;
  esac
  case "$name" in
    DOTENV_PUBLIC_KEY | DOTENV_PUBLIC_KEY_*)
      if ! printf '%s' "$value" | grep -Eqx '[0-9a-fA-F]{66}'; then
        echo "check-env-encrypted: $name is not a public key" >&2
        bad=1
      fi
      continue
      ;;
  esac
  [ -n "$value" ] || continue
  case "$value" in
    encrypted:*) ;;
    *) echo "check-env-encrypted: $name is not encrypted" >&2; bad=1 ;;
  esac
done < "$file"

if [ "$bad" -ne 0 ]; then
  echo "check-env-encrypted: run: dotenvx encrypt -f $file" >&2
  exit 1
fi
