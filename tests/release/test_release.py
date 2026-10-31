#!/usr/bin/env python3
"""Offline rows for the GitHub Release steps of .github/workflows/release.yml.

Runs each step's own `run:` block, read from the YAML, under `bash -eo pipefail` like the runner,
against a fake `gh` (tests/release/fake_gh.py) and a stub on 127.0.0.1 that plays the Coolify API.
The hardcoded Coolify host is rewritten to the stub when a block is extracted. `curl` and `jq` are
wrapped to record their argv. Nothing leaves the machine.

Run: python3 tests/release/test_release.py     (needs python3 with PyYAML, bash, curl, jq, git)
"""
import http.server
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(HERE, "..", "..", ".github", "workflows", "release.yml")
FAKE_GH = os.path.join(HERE, "fake_gh.py")
HOST = "https://coolify.brandtstack.com"
REAL = {n: shutil.which(n) for n in ("curl", "jq")}
# Generated at runtime so no credential-shaped literal sits in this public repo.
FX = {k: secrets.token_hex(8) for k in ("GH_TOKEN", "BEARER", "CFID", "CFSEC", "PLANTED")}
VER = "v1.2.3"
SHIM = """#!/usr/bin/env bash
printf '%%s\\0' "$@" >> "$ARGV_LOG"; printf '\\n' >> "$ARGV_LOG"
exec %s "$@"
"""


class Stub(http.server.BaseHTTPRequestHandler):
    """script: {"deployments": [(code, status), ...] served in order (last one repeats),
                "app_tag": str, "deploy": dict}"""
    script = {}
    polls = 0

    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        s = Stub.script
        if self.path.startswith("/api/v1/deployments/"):
            seq = s.get("deployments", [(200, "finished")])
            code, status = seq[min(Stub.polls, len(seq) - 1)]
            Stub.polls += 1
            return self.reply(code, {"status": status, "deployment_uuid": "dep-1",
                                     "application": {"envs": [{"key": "X", "value": FX["PLANTED"]}]}})
        if self.path.startswith("/api/v1/applications/"):
            return self.reply(200, {"docker_registry_image_tag": s.get("app_tag", VER), "planted": FX["PLANTED"]})
        if self.path.startswith("/api/v1/deploy"):
            return self.reply(200, s.get("deploy", {"deployments": [{"message": "queued", "deployment_uuid": "dep-1"}]}))
        self.reply(404, {})

    do_POST = do_GET


def step(name, port):
    steps = yaml.safe_load(open(WORKFLOW))["jobs"]["release"]["steps"]
    found = [s for s in steps if s.get("name") == name]
    assert len(found) == 1, "step %r not found exactly once in release.yml" % name
    return found[0]["run"].replace(HOST, "http://127.0.0.1:%d" % port)


def git_repo(base, tag=None):
    repo = os.path.join(base, "repo")
    os.makedirs(repo)
    g = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True, text=True).stdout.strip()
    g("init", "-q")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "feat: a change")
    if tag:
        g("-c", "user.name=t", "-c", "user.email=t@t", "tag", "-a", tag, "-m", tag)
    return repo, g("rev-parse", "HEAD")


def run(port, name, *, env=None, releases=None, fail=None, script=None, tag=None):
    Stub.script, Stub.polls = dict(script or {}), 0
    base = tempfile.mkdtemp()
    try:
        state = os.path.join(base, "gh-state.json")
        json.dump({"releases": releases or {}, "fail": fail or [], "calls": []}, open(state, "w"))
        bindir = os.path.join(base, "bin")
        os.makedirs(bindir)
        shutil.copy(FAKE_GH, os.path.join(bindir, "gh"))
        os.chmod(os.path.join(bindir, "gh"), 0o755)
        argv_log = os.path.join(base, "argv.log")
        open(argv_log, "w").close()
        for n, real in REAL.items():
            with open(os.path.join(bindir, n), "w") as f:
                f.write(SHIM % real)
            os.chmod(os.path.join(bindir, n), 0o755)
        repo, sha = git_repo(base, tag)
        rt = os.path.join(base, "rt")
        os.makedirs(rt)
        out_file = os.path.join(base, "gh-output")
        open(out_file, "w").close()
        e = {"PATH": bindir + os.pathsep + os.environ["PATH"], "HOME": base, "ARGV_LOG": argv_log,
             "FAKE_GH_STATE": state, "RUNNER_TEMP": rt, "GITHUB_OUTPUT": out_file, "GITHUB_SHA": sha,
             "GH_TOKEN": FX["GH_TOKEN"], "VER": VER, "WAIT": "true", "UUID": "app-1", "DEP": "dep-1",
             "BEARER": FX["BEARER"], "CFID": FX["CFID"], "CFSEC": FX["CFSEC"],
             "POLL_INTERVAL": "0", "POLL_TIMEOUT": "5"}
        e.update(env or {})
        e = {k: v for k, v in e.items() if v is not None}
        path = os.path.join(base, "step.sh")
        open(path, "w").write(step(name, port))
        r = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", path],
                           env=e, capture_output=True, text=True, cwd=repo, timeout=60)
        st = json.load(open(state))
        return {"rc": r.returncode, "out": r.stdout + r.stderr, "calls": st["calls"], "rel": st["releases"],
                "argv": open(argv_log).read(), "output": open(out_file).read()}
    finally:
        shutil.rmtree(base)


def creates(r):
    return [c for c in r["calls"] if c[:2] == ["release", "create"]]


def edits(r):
    return [c for c in r["calls"] if c[:2] == ["release", "edit"]]


def rows(port):
    checks = []

    def add(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    C = "Create GitHub Release"
    r = run(port, C)
    c = creates(r)
    add("coolify-deploying: create with --verify-tag --generate-notes --latest=false, notes start ⏳ deploying",
        r["rc"] == 0 and len(c) == 1 and {"--verify-tag", "--generate-notes", "--latest=false"} <= set(c[0])
        and r["rel"].get(VER, {}).get("body", "").startswith("⏳ deploying") and not r["rel"][VER]["latest"],
        json.dumps(r["calls"]) + r["out"])

    r = run(port, C, env={"WAIT": "false"})
    c = creates(r)
    add("no-coolify-latest: create marks --latest, no state line",
        r["rc"] == 0 and len(c) == 1 and "--latest" in c[0] and "--latest=false" not in c[0]
        and r["rel"][VER]["latest"] and not r["rel"][VER]["body"].startswith(("⏳", "❔")),
        json.dumps(r["calls"]) + r["out"])

    r = run(port, C, fail=["create"])
    add("gh-fails: a failed create fails the step", r["rc"] != 0, r["out"])

    R = "Ensure Release on re-run"
    r = run(port, R, tag=VER, env={"VER": None})
    c = creates(r)
    add("rerun-missing-release: creates it, not latest, with the ❔ line",
        r["rc"] == 0 and len(c) == 1 and c[0][2] == VER and "--latest=false" in c[0]
        and r["rel"][VER]["body"].startswith("❔ created on re-run; deploy state not checked"),
        json.dumps(r["calls"]) + r["out"])

    r = run(port, R, tag=VER, env={"VER": None}, releases={VER: {"body": "x", "latest": True}})
    add("rerun-existing-release: leaves it alone", r["rc"] == 0 and not creates(r) and not edits(r),
        json.dumps(r["calls"]) + r["out"])

    return checks


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        checks = rows(server.server_address[1])
    finally:
        server.shutdown()
    failed = 0
    for name, ok, detail in checks:
        print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok or not detail else " -- " + detail[:400]))
        failed += not ok
    print("%d rows, %d failed" % (len(checks), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
