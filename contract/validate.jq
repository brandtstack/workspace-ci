# Validates the parsed .github/build.yml (as JSON on stdin).
# Args: --arg ws <workspace-ci root>  --arg defname <repo name, for the go-main default>
# Output: {"errors": [...]} or {"outputs": {...}}; every problem is reported, not just the first.
# The key list below is mirrored in schema.json (the documentation of the contract).

def isbool: type == "boolean";
def isstr: type == "string";
def nonempty: isstr and length > 0;
def badpath: isstr and (startswith("/") or test("(\\A|/)\\.\\.(/|\\z)"));

def keys_allowed: ["family","dockerfile","node","go","prisma","dotenvx","integration",
  "favicon","build-args","private-registry","coolify-app","smoke-args","go-main","go-dir","migrate"];
def bools: ["prisma","dotenvx","integration","private-registry","migrate"];

. as $c
| if $c == null then {errors: ["<file>: file is empty"]}
  elif ($c | type) != "object" then {errors: ["<root>: top level must be a map of keys"]}
  else
    ($c.family // null) as $fam
    | (if ($fam | type) == "string" then ["next","spa","go","custom"] | index($fam) else null end) as $famok
    # (input `.` stays the whole document, so has("k") below tests top-level keys)
    | ([
      # unknown keys
      ($c | keys_unsorted[] | select(. as $k | keys_allowed | index($k) | not)
        | "\(.): unknown key"),
      # family
      (if (has("family") | not) then "family: required"
       elif $famok == null then "family: must be one of next, spa, go, custom (a plain string)"
       else empty end),
      # dockerfile
      (if has("dockerfile") then
         (if $fam == "custom" then
            (if ($c.dockerfile | nonempty | not) then "dockerfile: must be a non-empty path string"
             elif ($c.dockerfile | badpath) then "dockerfile: must be relative to the repo root, no .."
             else empty end)
          elif $famok != null then "dockerfile: only allowed with family custom"
          else empty end)
       elif $fam == "custom" then "dockerfile: required with family custom"
       else empty end),
      # node / go
      (if has("node") then
         (if $c.node == "24" or (($c.node | type) == "number" and ($c.node | tojson) == "24") then empty
          else "node: must be \"24\"" end)
       else empty end),
      (if has("go") then
         (if ($c.go | type) == "string" and ($c.go | test("\\A1\\.[0-9]+\\z")) then empty
          else "go: must be a quoted string like \"1.27\" (an unquoted 1.30 would be read as 1.3)" end)
       else empty end),
      (if has("node") and $famok != null and $fam == "go" then "node: not allowed with family go" else empty end),
      (if has("go") and ($fam == "next" or $fam == "spa") then "go: only allowed with family go or custom" else empty end),
      (if $fam == "custom" and ((has("node") | not) and (has("go") | not)) then "node: custom needs exactly one of node or go"
       elif $fam == "custom" and has("node") and has("go") then "go: custom needs exactly one of node or go, not both"
       else empty end),
      # booleans
      (bools[] as $k | select(has($k) and ($c[$k] | isbool | not)) | "\($k): must be a boolean (true/false), not a string"),
      (("prisma","dotenvx") as $k | select(has($k) and ($fam == "spa" or $fam == "go")) | "\($k): only allowed with family next or custom"),
      # migrate: next only (the shared image's start.sh runs it), and it needs the Prisma layout
      (if has("migrate") and $famok != null and $fam != "next" then "migrate: only allowed with family next"
       elif $c.migrate == true and $c.prisma != true then "migrate: requires prisma: true"
       else empty end),
      # lists and maps
      (if has("favicon") then
         (if ($c.favicon | type) != "array" or ($c.favicon | all(nonempty) | not)
          then "favicon: must be a list of non-empty strings (an empty list means none)" else empty end)
       else empty end),
      (if has("build-args") then
         (if $fam == "go" then "build-args: not allowed with family go (the go image takes no build args)"
          elif ($c["build-args"] | type) != "object" then "build-args: must be a map of string to string"
          else ($c["build-args"] | to_entries[]
            | (if (.key | test("\\A(NEXT_PUBLIC|VITE)_[A-Z0-9_]+\\z") | not)
                 then "build-args: \(.key): name must match ^(NEXT_PUBLIC|VITE)_[A-Z0-9_]+$ (public values only, never secrets)"
               elif (.value | isstr | not) or (.value | test("[\n\r]")) then "build-args: \(.key): value must be a single-line string"
               else empty end))
          end)
       else empty end),
      (if has("coolify-app") then
         (if ($c["coolify-app"] | nonempty) or (($c["coolify-app"] | type) == "array" and ($c["coolify-app"] | length) > 0 and ($c["coolify-app"] | all(nonempty)))
          then
            # Coolify uuids (lowercase alphanumerics). The value is interpolated into authenticated
            # Coolify API URLs by release.yml, so nothing path- or query-shaped may pass.
            ([$c["coolify-app"]] | flatten | map(select(test("\\A[a-z0-9]{8,64}\\z") | not)) | .[]
             | "coolify-app: \(.) is not a Coolify uuid (8-64 lowercase letters and digits)")
          else "coolify-app: must be a string or a non-empty list of strings" end)
       else empty end),
      (if has("smoke-args") and ($c["smoke-args"] | isstr | not) then "smoke-args: must be a string" else empty end),
      # go-only keys
      (("go-main","go-dir") as $k | select(has($k)) |
         (if $fam != "go" then "\($k): only allowed with family go"
          elif ($c[$k] | nonempty | not) then "\($k): must be a non-empty string"
          elif ($k == "go-dir" and ($c[$k] | badpath)) then "go-dir: must be relative to the repo root, no .."
          else empty end))
    ]) as $errs
    | if ($errs | length) > 0 then {errors: $errs}
      else
        def lst($k): if has($k) then [$c[$k]] | flatten | join("\n") else "" end;
        def bl($k): if has($k) then ($c[$k] | tostring) else "false" end;
        {outputs: {
          family: $fam,
          # next/spa/go: the shared Dockerfile in workspace-ci; custom: the repo's own path, verbatim
          dockerfile: (if $fam == "custom" then $c.dockerfile else "\($ws)/dockerfiles/\($fam).Dockerfile" end),
          node: (if has("node") then ($c.node | tostring) elif $fam == "next" or $fam == "spa" then "24" else "" end),
          go: (if has("go") then ($c.go | tostring) elif $fam == "go" then "1.27" else "" end),
          prisma: bl("prisma"),
          dotenvx: bl("dotenvx"),
          migrate: bl("migrate"),
          integration: bl("integration"),
          favicon: lst("favicon"),
          "build-args": (if has("build-args") then [$c["build-args"] | to_entries[] | "\(.key)=\(.value)"] | join("\n") else "" end),
          "private-registry": bl("private-registry"),
          "coolify-app": lst("coolify-app"),
          "smoke-args": ($c["smoke-args"] // ""),
          "go-main": (if $fam == "go" then ($c["go-main"] // "./cmd/\($defname)") else "" end),
          "go-dir": (if $fam == "go" then ($c["go-dir"] // ".") else "" end)
        }}
      end
  end
