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
            if status == "<raw>":
                data = b"<html>not json</html>"
                self.send_response(code); self.send_header("content-length", str(len(data))); self.end_headers()
                return self.wfile.write(data)
            return self.reply(code, {"status": status, "deployment_uuid": "dep-1",
                                     "application": {"envs": [{"key": "X", "value": FX["PLANTED"]}]}})
        if self.path.startswith("/api/v1/applications/"):
            return self.reply(s.get("app_code", 200), {"docker_registry_image_tag": s.get("app_tag", VER), "planted": FX["PLANTED"]})
        if self.path.startswith("/api/v1/deploy"):
            return self.reply(200, s.get("deploy", {"deployments": [{"message": "queued", "deployment_uuid": "dep-1"}]}))
        self.reply(404, {})

    do_POST = do_GET


def step(name, port):
    steps = yaml.safe_load(open(WORKFLOW))["jobs"]["release"]["steps"]
    found = [s for s in steps if s.get("name") == name]
    assert len(found) == 1, "step %r not found exactly once in release.yml" % name
    return found[0]["run"].replace(HOST, "http://127.0.0.1:%d" % port)


def git_repo(base, tag=None, newer=None):
    repo = os.path.join(base, "repo")
    os.makedirs(repo)
    g = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True, text=True).stdout.strip()
    g("init", "-q")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "feat: a change")
    if tag:
        g("-c", "user.name=t", "-c", "user.email=t@t", "tag", "-a", tag, "-m", tag)
    sha = g("rev-parse", "HEAD")
    if newer:
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "fix: later")
        g("-c", "user.name=t", "-c", "user.email=t@t", "tag", "-a", newer, "-m", newer)
    return repo, sha


def run(port, name, *, env=None, releases=None, fail=None, script=None, tag=None, newer=None):
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
        repo, sha = git_repo(base, tag, newer)
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
        if "release-state.sh" in step(name, port):
            open(path, "w").write(step("Write the Release state helper", port))
            subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", path], env=e, check=True, cwd=repo)
        open(path, "w").write(step(name, port))
        r = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", path],
                           env=e, capture_output=True, text=True, cwd=repo, timeout=60)
        st = json.load(open(state))
        return {"rc": r.returncode, "out": r.stdout + r.stderr, "calls": st["calls"], "rel": st["releases"],
                "argv": open(argv_log).read(), "output": open(out_file).read(),
                "left": sorted(os.listdir(rt))}
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

    # ---- order and gating, read from the YAML ----
    wf = yaml.safe_load(open(WORKFLOW))["jobs"]["release"]["steps"]
    names = [x.get("name", "") for x in wf]
    steps = {x.get("name", ""): x for x in wf}
    ix = lambda prefix: next(i for i, n in enumerate(names) if n.startswith(prefix))
    add("order: tag -> helper -> Coolify -> Deploy -> Create Release -> Wait -> Mark",
        ix("Push git tag") < ix("Write the Release state helper") < ix("Set APP_VERSION") < ix("Deploy")
        < ix("Create the GitHub Release") < ix("Wait for") < ix("Mark a Release"), " | ".join(names))
    co = [n for n in names if n.startswith(("Set APP_VERSION", "Point the Coolify", "Deploy", "Wait for"))]
    add("re-runs never deploy: every Coolify step is gated on guard skip == 'false'",
        len(co) == 4 and all("steps.guard.outputs.skip == 'false'" in steps[n]["if"] for n in co), str(co))
    cr = steps["Create the GitHub Release"]["if"]
    add("Create runs whenever the tag exists, even after a Coolify failure",
        "!cancelled()" in cr and "steps.tag.outcome == 'success'" in cr and "steps.guard.outputs.skip == 'true'" in cr, cr)
    mk = next(steps[n]["if"] for n in names if n.startswith("Mark a Release"))
    add("Mark only fires when Wait was skipped (not when Wait itself failed)",
        "failure()" in mk and "steps.wait.outcome == 'skipped'" in mk, mk)
    add("helper step always runs", steps["Write the Release state helper"]["if"] == "always()", "")

    # ---- Resolve the version to release ----
    V = "Resolve the version to release"
    r = run(port, V, env={"SKIP": "false", "NEW": "v1.3.0"}, tag="v1.2.3")
    add("resolve: first run releases the derived version", "version=v1.3.0" in r["output"], r["output"])
    r = run(port, V, env={"SKIP": "true", "NEW": ""}, tag=VER, newer="v1.2.4")
    add("resolve: a re-run releases the commit's own tag", "version=%s" % VER in r["output"], r["output"])

    # ---- Create the GitHub Release ----
    C = "Create the GitHub Release"
    first_run = {"SKIP": "false"}
    r = run(port, C, env=first_run)
    c = creates(r)
    add("coolify-deploying: create with --verify-tag --generate-notes --latest=false, notes start ⏳ deploying",
        r["rc"] == 0 and len(c) == 1 and {"--verify-tag", "--generate-notes", "--latest=false"} <= set(c[0])
        and r["rel"].get(VER, {}).get("body", "").startswith("⏳ deploying") and not r["rel"][VER]["latest"],
        json.dumps(r["calls"]) + r["out"])
    r = run(port, C, env=dict(first_run, WAIT="false"))
    c = creates(r)
    add("no-coolify-latest: create marks --latest, no state line",
        r["rc"] == 0 and len(c) == 1 and "--latest" in c[0] and "--latest=false" not in c[0]
        and r["rel"][VER]["latest"] and not r["rel"][VER]["body"].startswith(("⏳", "❔")),
        json.dumps(r["calls"]) + r["out"])
    r = run(port, C, env=first_run, fail=["create"])
    add("gh-fails: a failed create fails the step", r["rc"] != 0, r["out"])
    r = run(port, C, env={"SKIP": "true"})
    add("rerun-missing-release: created as ❔, not Latest",
        r["rc"] == 0 and r["rel"][VER]["body"].startswith("❔ created on re-run; deploy state not checked")
        and not r["rel"][VER]["latest"], json.dumps(r["rel"]) + r["out"])
    r = run(port, C, env={"SKIP": "true"}, releases={VER: {"body": "✅ live\n", "latest": True}})
    add("rerun-existing-release: left alone", r["rc"] == 0 and not creates(r) and not edits(r), json.dumps(r["calls"]))

    # ---- Mark a Release whose deploy never started ----
    M = "Mark a Release whose deploy never started"
    r = run(port, M, releases={VER: {"body": "⏳ deploying\n## What's Changed\n", "latest": False}})
    add("never-started: ⏳ deploying becomes ❌ deploy not started, notes kept",
        r["rc"] == 0 and r["rel"][VER]["body"].startswith("❌ deploy not started\n## What's Changed"), json.dumps(r["rel"]) + r["out"])
    r = run(port, M, releases={VER: {"body": "❌ deploy failed (failed)\n", "latest": False}})
    add("never-started: a Release in any other state is left alone", r["rc"] == 0 and not edits(r), json.dumps(r["calls"]))
    r = run(port, M)
    add("never-started: no Release (create itself failed) is a no-op", r["rc"] == 0 and not edits(r) and not creates(r), r["out"])

    # ---- the helper: replace a state line, or prepend one ----
    r = run(port, M, releases={VER: {"body": "⏳ deploying\n## What's Changed\n* x\n", "latest": False}})
    add("helper: replaces the state line instead of stacking a second one",
        sum(l.startswith(("⏳", "✅", "❌", "❔", "⏸", "↩")) for l in r["rel"][VER]["body"].splitlines()) == 1
        and "* x" in r["rel"][VER]["body"], json.dumps(r["rel"]))

    W = "Wait for the deployment and mark the Release"
    deploying = {VER: {"body": "⏳ deploying\n## What's Changed\n* feat: a change\n", "latest": False}}

    def first(r):
        return r["rel"][VER]["body"].split("\n", 1)[0]

    def kept(r):
        # `gh … -q .body` adds a trailing newline; only the content after the state line matters
        rest = r["rel"][VER]["body"].split("\n", 1)[1:]
        return [x.strip() for x in rest] == ["## What's Changed\n* feat: a change"]

    r = run(port, W, releases=deploying, script={"deployments": [(200, "in_progress"), (200, "finished")]})
    add("finished: Latest, ✅ live line, generated notes kept",
        r["rc"] == 0 and r["rel"][VER]["latest"] and first(r).startswith("✅ live in production 20") and kept(r),
        json.dumps(r["calls"]) + r["out"])

    r = run(port, W, releases=deploying, script={"deployments": [(200, "in_progress"), (200, "failed")]})
    add("failed: ❌ line, not Latest, step fails",
        r["rc"] != 0 and not r["rel"][VER]["latest"] and first(r) == "❌ deploy failed (failed)" and kept(r),
        json.dumps(r["calls"]) + r["out"])

    r = run(port, W, releases=deploying, script={"deployments": [(200, "in_progress")]},
            env={"POLL_TIMEOUT": "2", "POLL_INTERVAL": "1"})
    add("timeout: ❔ deploy state unknown (timeout), not Latest, step fails",
        r["rc"] != 0 and not r["rel"][VER]["latest"] and first(r) == "❔ deploy state unknown (timeout)",
        json.dumps(r["calls"]) + r["out"])

    r = run(port, W, releases=deploying, script={"deployments": [(403, "-"), (502, "-"), (200, "finished")]})
    add("transient-then-finished: 403/502 mid-poll are retried, then live",
        r["rc"] == 0 and r["rel"][VER]["latest"] and first(r).startswith("✅ live"), json.dumps(r["calls"]) + r["out"])

    r = run(port, W, releases=deploying, script={"deployments": [(200, "finished")], "app_tag": "v9.9.9"})
    add("superseded: a newer tag is on the app, so not Latest",
        r["rc"] == 0 and not r["rel"][VER]["latest"] and first(r) == "⏸ superseded by v9.9.9",
        json.dumps(r["calls"]) + r["out"])

    r = run(port, W, releases=deploying, script={"deployments": [(200, "in_progress"), (200, "finished")]})
    blob = r["out"] + r["argv"] + json.dumps(r["calls"]) + json.dumps(r["rel"])
    add("no-body-leak: nothing from the Coolify bodies reaches output, argv or the Release",
        FX["PLANTED"] not in blob, "planted value found")

    for st in ("error", "cancelled-by-user"):
        r = run(port, W, releases=deploying, script={"deployments": [(200, st)]})
        add("terminal %s: ❌ deploy failed (%s), step fails" % (st, st),
            r["rc"] != 0 and first(r) == "❌ deploy failed (%s)" % st and not r["rel"][VER]["latest"], json.dumps(r["rel"]) + r["out"])

    r = run(port, W, releases=deploying, script={"deployments": [(200, "cancelled-by-user")], "app_tag": "v1.2.4"})
    add("cancelled because a newer merge took over: ⏸ superseded, step passes",
        r["rc"] == 0 and first(r) == "⏸ superseded by v1.2.4" and not r["rel"][VER]["latest"], json.dumps(r["rel"]) + r["out"])

    r = run(port, W, releases=deploying, script={"deployments": [(200, "finished")], "app_code": 500})
    add("app-record-unreadable: finished deploy is still marked live + Latest, with a warning",
        r["rc"] == 0 and r["rel"][VER]["latest"] and first(r).startswith("✅ live") and "::warning::" in r["out"],
        json.dumps(r["rel"]) + r["out"])

    r = run(port, W, releases=deploying, script={"deployments": [(200, "<raw>"), (200, "finished")]})
    add("non-JSON 200 mid-poll is retried, not a crash", r["rc"] == 0 and first(r).startswith("✅ live"), r["out"])

    r = run(port, W, releases=deploying, script={"deployments": [(200, "failed")]})
    add("response file never outlives the step (failure path)", "coolify.json" not in r["left"], str(r["left"]))
    r = run(port, W, releases=deploying, script={"deployments": [(200, "finished")]})
    add("response file never outlives the step (success path)", "coolify.json" not in r["left"], str(r["left"]))

    r = run(port, "Deploy", script={"deploy": {"deployments": [{"message": "queued"}]}})
    add("missing-uuid: Deploy fails when /deploy returns no deployment_uuid", r["rc"] != 0, r["out"])

    r = run(port, "Deploy")
    add("deploy-output: Deploy writes deployment_uuid to GITHUB_OUTPUT",
        r["rc"] == 0 and "deployment_uuid=dep-1" in r["output"], r["out"] + r["output"])

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
