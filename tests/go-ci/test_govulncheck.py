#!/usr/bin/env python3
"""Offline rows for the govulncheck steps of .github/workflows/go-ci.yml.

Two halves:
- the step wiring, read from the YAML (order, ids, `if:`, `continue-on-error`, ENFORCE), which is
  what makes the policy "fail anything but a push, only warn on a push" hold for infrastructure
  failures as well as for the scanner's own exit code;
- the `govulncheck` step's own `run:` block, read from the YAML and run under `bash -e` like the
  runner, against a fake `go` and a fake scanner in a throwaway git repo with two modules (root and
  server/, the cc-usage shape). Nothing leaves the machine.

Run: python3 tests/go-ci/test_govulncheck.py     (needs python3 with PyYAML, bash, git)
"""
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(HERE, "..", "..", ".github", "workflows", "go-ci.yml")
VERSION = "v1.9.0"
GOVERSION = "go1.27.3"

# `go env GOVERSION` -> GOVERSION; `go install` -> copies the fake scanner into $GOBIN, or fails
# when FAKE_INSTALL_FAIL=1 (after creating a file, like a half-done build would).
FAKE_GO = """#!/bin/sh
if [ "$1" = env ]; then echo %s; exit 0; fi
if [ "$1" = install ]; then
  echo install >> "$FAKE_LOG"
  if [ "${FAKE_INSTALL_FAIL:-0}" = 1 ]; then : > "$GOBIN/partial"; exit 1; fi
  cp "$FAKE_SCANNER" "$GOBIN/govulncheck"; exit 0
fi
exit 2
""" % GOVERSION

# Exit code per module dir name from RC_<basename>; the root module's dir is the repo dir "repo".
FAKE_SCANNER = """#!/bin/sh
[ "$1" = -version ] && { echo fake; exit 0; }
cat >/dev/null
d=$(basename "$PWD"); eval "rc=\\${RC_$d:-0}"
echo "scanned $d" >> "$FAKE_LOG"
exit $rc
"""


def steps():
    return yaml.safe_load(open(WORKFLOW))["jobs"]["verify"]["steps"]


def write_exe(path, text):
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, 0o755)


def git_repo(path, modules):
    os.makedirs(path)
    subprocess.run(["git", "init", "-q", path], check=True)
    for m in modules:
        os.makedirs(os.path.join(path, os.path.dirname(m)), exist_ok=True)
        open(os.path.join(path, m), "w").close()
    if modules:
        subprocess.run(["git", "-C", path, "add", "."], check=True)


class World:
    def __init__(self, base):
        self.base = base
        self.fake = os.path.join(base, "fake")
        os.makedirs(self.fake)
        write_exe(os.path.join(self.fake, "go"), FAKE_GO)
        self.scanner = os.path.join(base, "scanner")
        write_exe(self.scanner, FAKE_SCANNER)
        self.cache = os.path.join(base, "toolcache")
        self.tmp = os.path.join(base, "runner-temp")
        os.makedirs(self.cache)
        os.makedirs(self.tmp)
        self.log = os.path.join(base, "log")
        self.script = os.path.join(base, "step.sh")
        s = next(x for x in steps() if x.get("name") == "govulncheck")
        with open(self.script, "w") as f:
            f.write(s["run"])
        git_repo(os.path.join(base, "repo"), ["go.mod", "server/go.mod"])
        git_repo(os.path.join(base, "empty"), [])

    def run(self, enforce, repo="repo", **rc):
        open(self.log, "w").close()
        env = dict(os.environ)
        env.update(
            PATH=self.fake + os.pathsep + env["PATH"],
            ENFORCE=enforce,
            GOVULNCHECK_VERSION=VERSION,
            RUNNER_TOOL_CACHE=self.cache,
            RUNNER_TEMP=self.tmp,
            FAKE_LOG=self.log,
            FAKE_SCANNER=self.scanner,
        )
        env.update({k: str(v) for k, v in rc.items()})
        p = subprocess.run(["bash", "-e", self.script], cwd=os.path.join(self.base, repo),
                           env=env, capture_output=True, text=True)
        return {"rc": p.returncode, "out": p.stdout + p.stderr, "log": open(self.log).read().split("\n")}

    def cached(self):
        return os.path.join(self.cache, "govulncheck", "%s-%s" % (VERSION, GOVERSION))

    def leftovers(self):
        d = os.path.join(self.cache, "govulncheck")
        return [n for n in os.listdir(d) if n.startswith(".build.")] if os.path.isdir(d) else []


def rows():
    checks = []

    def add(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    # ---- wiring, read from the YAML ----
    wf = steps()
    names = [x.get("name", "") for x in wf]
    by = {x.get("name", ""): x for x in wf}
    ix = lambda prefix: next(i for i, n in enumerate(names) if n.startswith(prefix))
    setup = wf[ix("Set up the latest Go")]
    skip = wf[ix("govulncheck skipped")]
    scan = by["govulncheck"]
    make = next(i for i, x in enumerate(wf) if x.get("run") == "make verify")
    add("order: make verify -> latest-Go setup -> skip warning -> govulncheck",
        make < ix("Set up the latest Go") < ix("govulncheck skipped") < names.index("govulncheck"), str(names))
    add("setup uses check-latest and the go-version input",
        setup.get("with", {}).get("check-latest") is True and "inputs.go-version" in str(setup["with"]["go-version"]))
    add("setup has id vulngo", setup.get("id") == "vulngo")
    push_only = "${{ github.event_name == 'push' }}"
    add("setup: continue-on-error only on a push", setup.get("continue-on-error") == push_only, str(setup.get("continue-on-error")))
    add("scan: continue-on-error only on a push", scan.get("continue-on-error") == push_only, str(scan.get("continue-on-error")))
    add("scan: ENFORCE is true for every event but a push",
        scan["env"]["ENFORCE"] == "${{ github.event_name != 'push' }}", scan["env"]["ENFORCE"])
    add("scan runs only after a successful setup", scan.get("if") == "steps.vulngo.outcome == 'success'", str(scan.get("if")))
    add("skip warning runs only after a failed setup", skip.get("if") == "steps.vulngo.outcome == 'failure'", str(skip.get("if")))
    add("no ${{ }} expression inside any new run: block",
        all("${{" not in x.get("run", "") for x in (skip, scan)))
    add("scanner version is exact-pinned", scan["env"]["GOVULNCHECK_VERSION"] == VERSION, scan["env"]["GOVULNCHECK_VERSION"])

    # ---- the govulncheck run: block ----
    with tempfile.TemporaryDirectory() as base:
        w = World(base)

        r = w.run("true")
        add("clean: exit 0, both modules scanned", r["rc"] == 0 and "scanned repo" in r["log"] and "scanned server" in r["log"], r["out"])
        add("first run builds once into the cache", r["log"].count("install") == 1 and os.access(w.cached(), os.X_OK), r["out"])

        r = w.run("true")
        add("cache hit: no second build", r["log"].count("install") == 0 and r["rc"] == 0, r["out"])

        r = w.run("true", RC_server=3)
        add("enforce + vuln in server/: exit 1 with ::error::", r["rc"] == 1 and "::error::govulncheck: reachable vulnerabilities in module server" in r["out"], r["out"])

        r = w.run("true", RC_repo=3)
        add("enforce + vuln in root: still scans server/ and fails",
            r["rc"] == 1 and "scanned server" in r["log"] and "module ." in r["out"], r["out"])

        r = w.run("true", RC_repo=1)
        add("enforce + scanner error: exit 1 with ::error::", r["rc"] == 1 and "::error::govulncheck: scanner error (exit 1)" in r["out"], r["out"])

        r = w.run("false", RC_repo=3)
        add("push + vuln: exit 0 with ::warning::", r["rc"] == 0 and "::warning::" in r["out"] and "::error::" not in r["out"], r["out"])

        r = w.run("false", RC_repo=1, RC_server=3)
        add("push + scanner error and vuln: exit 0, two warnings", r["rc"] == 0 and r["out"].count("::warning::") == 2, r["out"])

        r = w.run("true", repo="empty")
        add("no tracked go.mod: exit 1 with ::error::", r["rc"] == 1 and "no go.mod tracked" in r["out"], r["out"])

        os.remove(w.cached())
        r = w.run("true", FAKE_INSTALL_FAIL=1)
        add("failed build: non-zero exit, no binary cached", r["rc"] != 0 and not os.path.exists(w.cached()), r["out"])
        add("failed build: no .build.* dir left behind", w.leftovers() == [], str(w.leftovers()))

    return checks


def main():
    checks = rows()
    failed = 0
    for name, ok, detail in checks:
        print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok or not detail else " -- " + detail[:400]))
        failed += not ok
    print("%d rows, %d failed" % (len(checks), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
