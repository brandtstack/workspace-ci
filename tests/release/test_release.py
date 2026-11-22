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
    app = {}        # what the stub "stores" from settings PATCHes, served back on GET
    patches = []    # every PATCH body received, decoded

    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def raw(self, code, data):
        self.send_response(code)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        s = Stub.script
        if self.path.startswith("/api/v1/deployments/"):
            seq = s.get("deployments", [(200, "finished")])
            code, status = seq[min(Stub.polls, len(seq) - 1)]
            Stub.polls += 1
            if isinstance(status, bytes):
                return self.raw(code, status)
            if status == "<raw>":
                data = b"<html>not json</html>"
                self.send_response(code); self.send_header("content-length", str(len(data))); self.end_headers()
                return self.wfile.write(data)
            return self.reply(code, {"status": status, "deployment_uuid": "dep-1",
                                     "application": {"envs": [{"key": "X", "value": FX["PLANTED"]}]}})
        if self.path.startswith("/api/v1/applications/"):
            if "app_raw" in s:
                return self.raw(200, s["app_raw"])
            return self.reply(s.get("app_code", 200), dict(Stub.app, docker_registry_image_tag=s.get("app_tag", VER),
                                                           planted=FX["PLANTED"], **s.get("app_override", {})))
        if self.path.startswith("/api/v1/deploy"):
            if "deploy_code" in s:
                return self.raw(s["deploy_code"], s["fail_body"])
            return self.reply(200, s.get("deploy", {"deployments": [{"message": "queued", "deployment_uuid": "dep-1"}]}))
        self.reply(404, {})

    do_POST = do_GET

    def do_PATCH(self):
        s = Stub.script
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        Stub.patches.append(body)
        code = s.get("patch_code", 200)
        if code != 200 and "fail_body" in s:
            return self.raw(code, s["fail_body"])
        if code == 200:
            for k, v in body.items():
                if k in s.get("drop", ()):
                    continue
                if s.get("as_numbers") and isinstance(v, bool):
                    v = int(v)
                elif s.get("as_numbers") and k.endswith("port") and isinstance(v, str):
                    v = int(v)
                Stub.app[k] = v
        return self.reply(code, {"message": "nope"} if code != 200 else {"uuid": "app-1", "planted": FX["PLANTED"]})


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


def run(port, name, *, env=None, releases=None, fail=None, fail_once=None, script=None, tag=None, newer=None):
    Stub.script, Stub.polls, Stub.app, Stub.patches = dict(script or {}), 0, {}, []
    base = tempfile.mkdtemp()
    try:
        state = os.path.join(base, "gh-state.json")
        json.dump({"releases": releases or {}, "fail": fail or [], "fail_once": fail_once or [], "calls": []}, open(state, "w"))
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
             "POLL_INTERVAL": "0", "POLL_TIMEOUT": "5", "RETRY_SLEEP": "0"}
        e.update(yaml.safe_load(open(WORKFLOW))["jobs"]["release"].get("env", {}))   # as the runner would
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
    wt = steps["Wait for the deployment and mark the Release"]["if"]
    add("Wait does not depend on Create: !cancelled() + Deploy succeeded",
        "!cancelled()" in wt and "steps.deploy.outcome == 'success'" in wt, wt)

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
    r = run(port, C, env=first_run, fail_once=["create"])
    add("create retries a transient failure", r["rc"] == 0 and len(creates(r)) == 2 and VER in r["rel"], r["out"])
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
    r = run(port, M, env={"QUEUED": "true"}, releases={VER: {"body": "⏳ deploying\n## What's Changed\n", "latest": False}})
    add("queued-but-no-uuid: ❔ deploy queued; state not confirmed (not ❌ not started)",
        r["rc"] == 0 and r["rel"][VER]["body"].startswith("❔ deploy queued; state not confirmed\n"), json.dumps(r["rel"]))
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

    r = run(port, W, script={"deployments": [(200, "finished")]})
    add("Create failed earlier: Wait creates the Release late, live + Latest",
        r["rc"] == 0 and r["rel"].get(VER, {}).get("latest") and r["rel"][VER]["body"].startswith("✅ live"), json.dumps(r["rel"]) + r["out"])

    r = run(port, W, releases={VER: {"body": "⏳ deploying\n## What's Changed\n* x\n\n\n", "latest": False}},
            script={"deployments": [(200, "finished")]})
    add("state edits never accumulate trailing blank lines", not r["rel"][VER]["body"].endswith("\n\n"), repr(r["rel"][VER]["body"]))

    r = run(port, W, releases=deploying, script={"deployments": [(200, "failed")]}, fail=["edit"])
    add("the deploy outcome is logged before any gh call", "ended: failed" in r["out"], r["out"])

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

    # ---- contract mode: target and Coolify sync (spec D6) ----
    add("order: contract read -> target -> guard; head -> Sync -> promote -> tag -> APP_VERSION -> Point -> Deploy (sync failure leaves nothing minted)",
        ix("Read the build contract") < ix("Resolve the Coolify target") < ix("Skip if this commit")
        and ix("Resolve the PR head SHA") < ix("Sync Coolify from build.yml") < ix("Promote the tested image") < ix("Push git tag")
        < ix("Set APP_VERSION") < ix("Point the Coolify") < ix("Deploy"),
        " | ".join(names))
    sy = steps["Sync Coolify from build.yml"]["if"]
    add("Sync is gated on contract mode, guard skip == 'false', publish-image and a target",
        all(x in sy for x in ("inputs.contract", "steps.guard.outputs.skip == 'false'", "inputs.publish-image",
                              "steps.target.outputs.uuid != ''")), sy)
    for n in ("Require the workflow's own ref", "Check out workspace-ci at its own ref", "Move workspace-ci out of the workspace", "Read the build contract"):
        add("contract-only step: %s" % n, steps[n].get("if") == "inputs.contract", str(steps[n].get("if")))
    add("target step always runs (legacy callers read it too)", "if" not in steps["Resolve the Coolify target"], "")
    raw = open(WORKFLOW).read().split("jobs:", 1)[1]
    add("no Coolify step reads the coolify-app-uuid input directly (only the target step does)",
        raw.count("inputs.coolify-app-uuid") == 1, str(raw.count("inputs.coolify-app-uuid")))

    # The step list is the legacy path too: custom support adds no step and removes none.
    # (the two "" are the checkout and login-action steps, which have `uses` and no name)
    add("step list unchanged by custom support (legacy path included)", names == [
        "", "Require the workflow's own ref",
        "Check out workspace-ci at its own ref", "Move workspace-ci out of the workspace", "Read the build contract",
        "Resolve the Coolify target", "Skip if this commit is already version-tagged", "Derive version from the squash commit title",
        "Resolve the version to release", "Resolve the PR head SHA this merge came from", "Sync Coolify from build.yml", "",
        "Promote the tested image to ${{ steps.ver.outputs.version }}", "Push git tag", "Write the Release state helper",
        "Set APP_VERSION on the Coolify application", "Point the Coolify application at ${{ steps.v.outputs.version }}", "Deploy",
        "Create the GitHub Release", "Wait for the deployment and mark the Release", "Mark a Release whose deploy never started"],
        " | ".join(names))
    add("target step reads the contract's node and go; sync reads the target's toolchain",
        steps["Resolve the Coolify target"]["env"].get("NODE") == "${{ steps.contract.outputs.node }}"
        and steps["Resolve the Coolify target"]["env"].get("GO") == "${{ steps.contract.outputs.go }}"
        and steps["Sync Coolify from build.yml"]["env"].get("TOOLCHAIN") == "${{ steps.target.outputs.toolchain }}", "")

    T = "Resolve the Coolify target"
    r = run(port, T, env={"CONTRACT": "false", "INPUT_UUID": "legacy-1", "APPS": "", "FAMILY": ""})
    add("target legacy: the input, unchanged", r["rc"] == 0 and "uuid=legacy-1" in r["output"], r["out"] + r["output"])
    r = run(port, T, env={"CONTRACT": "false", "INPUT_UUID": "", "APPS": "", "FAMILY": ""})
    add("target legacy without uuid: empty (tag-only caller)", r["rc"] == 0 and "uuid=\n" in r["output"], r["output"])
    r = run(port, T, env={"CONTRACT": "true", "INPUT_UUID": "", "APPS": "winnfosay9q4zcm1iisu2rgm", "FAMILY": "spa", "NODE": "24", "GO": ""})
    add("target contract: build.yml's app and family", r["rc"] == 0 and "uuid=winnfosay9q4zcm1iisu2rgm" in r["output"] and "family=spa" in r["output"], r["out"] + r["output"])
    add("target legacy writes no toolchain", "toolchain" not in run(port, T, env={"CONTRACT": "false", "INPUT_UUID": "x", "APPS": "", "FAMILY": ""})["output"], "")
    # family table: (family, contract node, contract go) -> toolchain. next/spa carry node, go carries go,
    # custom takes whichever of the two the contract names (validate.jq allows exactly one).
    for fam, node, go, want in (("next", "24", "", "node"), ("spa", "24", "", "node"), ("go", "", "1.27", "go"),
                                ("custom", "24", "", "node"), ("custom", "", "1.27", "go")):
        r = run(port, T, env={"CONTRACT": "true", "INPUT_UUID": "", "APPS": "winnfosay9q4zcm1iisu2rgm", "FAMILY": fam, "NODE": node, "GO": go})
        add("target contract: %s with node=%r go=%r -> toolchain=%s" % (fam, node, go, want),
            r["rc"] == 0 and ("toolchain=%s\n" % want) in r["output"] and ("family=%s\n" % fam) in r["output"], r["out"] + r["output"])
    for label, env in (("input also set", {"INPUT_UUID": "x", "APPS": "winnfosay9q4zcm1iisu2rgm", "FAMILY": "spa"}),
                       ("two apps", {"INPUT_UUID": "", "APPS": "a\nb", "FAMILY": "next"}),
                       ("no app", {"INPUT_UUID": "", "APPS": "", "FAMILY": "go"}),
                       ("custom with neither node nor go", {"INPUT_UUID": "", "APPS": "winnfosay9q4zcm1iisu2rgm", "FAMILY": "custom", "NODE": "", "GO": ""}),
                       ("unknown family", {"INPUT_UUID": "", "APPS": "winnfosay9q4zcm1iisu2rgm", "FAMILY": "nope", "NODE": "24"}),
                       ("path-shaped app", {"INPUT_UUID": "", "APPS": "../servers/abcdefgh", "FAMILY": "spa"}),
                       ("query-shaped app", {"INPUT_UUID": "", "APPS": "abcdefgh1234&force=true", "FAMILY": "spa"})):
        r = run(port, T, env=dict(env, CONTRACT="true"))
        add("target contract refuses: %s" % label, r["rc"] != 0 and "uuid=" not in r["output"], r["out"] + r["output"])

    S = "Sync Coolify from build.yml"
    r = run(port, S, env={"FAMILY": "next"})
    p = Stub.patches[0] if Stub.patches else {}
    add("sync next: one PATCH with port 80 and the full http check on 127.0.0.1:80/healthz",
        r["rc"] == 0 and len(Stub.patches) == 1 and p.get("ports_exposes") == "80" and p.get("health_check_enabled") is True
        and p.get("health_check_path") == "/healthz" and p.get("health_check_host") == "127.0.0.1"
        and p.get("health_check_port") == "80" and p.get("health_check_type") == "http", r["out"] + json.dumps(Stub.patches))
    add("sync: bearer and Access pair sent, nothing from the bodies printed",
        FX["BEARER"] in r["argv"] and FX["CFID"] in r["argv"] and FX["PLANTED"] not in r["out"], r["out"])
    add("sync: response file removed", "coolify-sync.json" not in r["left"], str(r["left"]))
    r = run(port, S, env={"FAMILY": "spa"})
    add("sync spa: same as next", r["rc"] == 0 and Stub.patches and Stub.patches[0].get("health_check_enabled") is True, r["out"])
    r = run(port, S, env={"FAMILY": "go"})
    add("sync go: port 80, Coolify check OFF, no http fields sent",
        r["rc"] == 0 and Stub.patches == [{"ports_exposes": "80", "health_check_enabled": False}], r["out"] + json.dumps(Stub.patches))
    r = run(port, S, env={"FAMILY": "next"}, script={"as_numbers": True})
    add("sync read-back: Coolify storing 1/0 and numeric ports still matches", r["rc"] == 0, r["out"])
    r = run(port, S, env={"FAMILY": "next"}, script={"drop": ["health_check_host"]})
    add("sync read-back mismatch fails, names the field, leaks nothing",
        r["rc"] != 0 and "health_check_host: want 127.0.0.1" in r["out"] and FX["PLANTED"] not in r["out"]
        and "coolify-sync.json" not in r["left"], r["out"])
    r = run(port, S, env={"FAMILY": "next"}, script={"patch_code": 403})
    add("sync PATCH refused (403): fails with the token hint, no read-back",
        r["rc"] != 0 and "403" in r["out"] and "cannot write applications" in r["out"] and "nope" in r["out"] and "a re-run retries" in r["out"], r["out"])
    r = run(port, S, env={"FAMILY": "next"}, script={"app_code": 500})
    add("sync read-back unreadable: fails", r["rc"] != 0 and "could not read the app back" in r["out"], r["out"])
    r = run(port, S, env={"FAMILY": "next", "BEARER": ""})
    add("sync without secrets: fails before any call", r["rc"] != 0 and not Stub.patches, r["out"])
    # custom takes its toolchain's settings (spec C4): node -> the next/spa check, go -> check off.
    r = run(port, S, env={"FAMILY": "custom", "TOOLCHAIN": "node"})
    p = Stub.patches[0] if Stub.patches else {}
    add("sync custom + node: port 80 and the full http check, same as next",
        r["rc"] == 0 and len(Stub.patches) == 1 and p.get("ports_exposes") == "80" and p.get("health_check_enabled") is True
        and p.get("health_check_path") == "/healthz" and p.get("health_check_host") == "127.0.0.1"
        and p.get("health_check_port") == "80", r["out"] + json.dumps(Stub.patches))
    r = run(port, S, env={"FAMILY": "custom", "TOOLCHAIN": "go"})
    add("sync custom + go: port 80, Coolify check OFF, no http fields sent",
        r["rc"] == 0 and Stub.patches == [{"ports_exposes": "80", "health_check_enabled": False}], r["out"] + json.dumps(Stub.patches))
    r = run(port, S, env={"FAMILY": "next", "TOOLCHAIN": "node"})
    nx = list(Stub.patches)
    r = run(port, S, env={"FAMILY": "custom", "TOOLCHAIN": "node"})
    add("sync custom + node sends exactly the body next sends", Stub.patches == nx and nx, json.dumps(Stub.patches))
    for label, env in (("custom with no toolchain", {"FAMILY": "custom", "TOOLCHAIN": ""}),
                       ("custom with an unknown toolchain", {"FAMILY": "custom", "TOOLCHAIN": "ruby"}),
                       ("unknown family", {"FAMILY": "nope", "TOOLCHAIN": "node"})):
        r = run(port, S, env=env)
        add("sync %s: fails before any call" % label, r["rc"] != 0 and not Stub.patches, r["out"])

    r = run(port, "Deploy", script={"deploy": {"deployments": [{"message": "queued"}]}})
    add("missing-uuid: Deploy fails, but records that it queued", r["rc"] != 0 and "queued=true" in r["output"], r["out"] + r["output"])

    r = run(port, "Deploy")
    add("deploy-output: Deploy writes deployment_uuid to GITHUB_OUTPUT",
        r["rc"] == 0 and "deployment_uuid=dep-1" in r["output"], r["out"] + r["output"])

    # A failing Coolify call prints the HTTP code and its message, never the body.
    planted = FX["PLANTED"]
    bodies = {
        "json": (json.dumps({"message": "refused-msg", "data": [{"key": "X", "value": planted}]}).encode(), "refused-msg"),
        "not-json": (("<html>" + planted + "</html>").encode(), None),
        "message-is-object": (json.dumps({"message": {"value": planted}}).encode(), None),
        "top-level-array": (json.dumps([{"value": planted}]).encode(), None),
    }
    calls = {
        "env": ("Set APP_VERSION on the Coolify application", "patch_code"),
        "tag": ("Point the Coolify application at ${{ steps.v.outputs.version }}", "patch_code"),
        "deploy": ("Deploy", "deploy_code"),
    }
    for call, (name, key) in calls.items():
        for kind, (body, msg) in bodies.items():
            r = run(port, name, script={key: 422, "fail_body": body})
            add("no-raw-body: %s on a %s failure prints the code%s and no body" % (call, kind, " and message" if msg else ""),
                r["rc"] != 0 and "422" in r["out"] and planted not in r["out"] and (msg is None or msg in r["out"]), r["out"])
            add("no-raw-body: %s leaves no response file after a %s failure" % (call, kind),
                not [f for f in r["left"] if f.endswith(".json")], str(r["left"]))
        r = run(port, name, script={key: 422, "fail_body": json.dumps({"message": "first\n::warning::injected"}).encode()})
        add("no-raw-body: %s keeps a multi-line message on one log line" % call,
            r["rc"] != 0 and "\n::warning::" not in r["out"], r["out"])
        r = run(port, name, script={key: 422, "fail_body": json.dumps({"message": "first%0A::warning::x%0Dy"}).encode()})
        add("no-raw-body: %s escapes percent-encoded line breaks" % call,
            r["rc"] != 0 and "%0A" not in r["out"] and "%0D" not in r["out"] and "first%250A" in r["out"], r["out"])
        for label, text in (("mid-line", "x ##[stop-commands]tok"), ("split by a newline", "x #\n#[add-mask]tok"),
                            ("after a hash", "x ###[add-mask]tok")):
            r = run(port, name, script={key: 422, "fail_body": json.dumps({"message": text}).encode()})
            add("no-runner-command: %s neutralises a %s ##[ command" % (call, label),
                r["rc"] != 0 and "422" in r["out"] and "##[" not in r["out"], r["out"])
        r = run(port, name, script={key: 422, "fail_body": json.dumps({"message": "é" * 400}).encode()})
        add("message-cap: %s caps a message at 300 characters without splitting one" % call,
            r["rc"] != 0 and "é" * 300 in r["out"] and "é" * 301 not in r["out"] and "�" not in r["out"], r["out"][:200])
        add("timeout: %s sets a curl --max-time" % call, "--max-time 30" in step(name, port))

    # Every other Coolify-supplied value that reaches a log line, a Release or GITHUB_OUTPUT.
    hostile = "v ##[add-mask]tok %0A::warning::x\n::notice::y"
    clean = lambda out: "##[" not in out and "%0A" not in out and "\n::warning::" not in out and "\n::notice::" not in out
    r = run(port, "Sync Coolify from build.yml", env={"FAMILY": "go", "TOOLCHAIN": "go"},
            script={"app_override": {"ports_exposes": hostile}})
    add("safe-log: a hostile read-back value is sanitised", r["rc"] != 0 and "ports_exposes" in r["out"] and clean(r["out"]), r["out"])
    r = run(port, "Wait for the deployment and mark the Release", releases={VER: {"body": "⏳ deploying"}}, tag=VER,
            script={"deployments": [(200, hostile), (200, "finished")]})
    add("safe-log: a hostile polled status is sanitised", r["rc"] == 0 and clean(r["out"]), r["out"])
    r = run(port, "Wait for the deployment and mark the Release", releases={VER: {"body": "⏳ deploying"}}, tag=VER,
            script={"app_tag": hostile})
    body = r["rel"].get(VER, {}).get("body", "")
    add("safe-log: a hostile image tag is sanitised in the log and the Release",
        r["rc"] == 0 and clean(r["out"]) and clean(body) and "superseded" in body, r["out"] + body)
    for label, tag in (("a control character", VER + "\u0007"), ("a trailing newline", VER + "\n"),
                       ("a C1 control", VER + "\u0085"), ("a 300-character prefix", VER + "x" * 400)):
        r = run(port, "Wait for the deployment and mark the Release", releases={VER: {"body": "⏳ deploying"}}, tag=VER,
                script={"app_tag": tag})
        body = r["rel"].get(VER, {}).get("body", "")
        add("live-check: a tag differing from the version by %s is not marked live" % label,
            r["rc"] == 0 and "superseded" in body and "✅" not in body and not [c for c in edits(r) if "--latest" in c],
            r["out"] + body)
    r = run(port, "Wait for the deployment and mark the Release", releases={VER: {"body": "⏳ deploying"}}, tag=VER)
    add("live-check: the exact version is still marked live", r["rc"] == 0 and "✅" in r["rel"][VER]["body"], r["out"])
    r = run(port, "Wait for the deployment and mark the Release", releases={VER: {"body": "⏳ deploying"}}, tag=VER,
            script={"app_code": 500})
    add("live-check: an unreadable app after a finished deploy still warns and marks live",
        r["rc"] == 0 and "could not read the app's image tag" in r["out"] and "✅" in r["rel"][VER]["body"], r["out"])

    # A body holding several JSON documents must not print one line per document or decide the release state.
    for call, (name, key) in calls.items():
        r = run(port, name, script={key: 422, "fail_body": b'{"message":"a"}{"message":"::add-mask::tok"}'})
        add("multi-doc: %s prints no line per document" % call,
            r["rc"] != 0 and "422" in r["out"] and "\n::add-mask::" not in r["out"], r["out"])
    r = run(port, "Wait for the deployment and mark the Release", releases={VER: {"body": "⏳ deploying"}}, tag=VER,
            script={"deployments": [(200, b'{"status":"running"}{"status":"::warning::injected"}'), (200, "finished")]})
    add("multi-doc: a polled status prints no line per document", r["rc"] == 0 and "\n::warning::" not in r["out"], r["out"])
    r = run(port, "Sync Coolify from build.yml", env={"FAMILY": "go", "TOOLCHAIN": "go"},
            script={"app_raw": b'{"ports_exposes":"80","health_check_enabled":false}{"ports_exposes":"::warning::x"}'})
    add("multi-doc: a settings read-back is refused", r["rc"] != 0 and "\n::warning::" not in r["out"], r["out"])
    r = run(port, "Wait for the deployment and mark the Release", releases={VER: {"body": "⏳ deploying"}}, tag=VER,
            script={"app_raw": b'{"docker_registry_image_tag":"v9.9.9"}{"docker_registry_image_tag":"v9.9.9"}'})
    body = r["rel"].get(VER, {}).get("body", "")
    add("multi-doc: an app record never marks the version live or Latest",
        r["rc"] != 0 and "✅" not in body and "unknown" in body and not [c for c in edits(r) if "--latest" in c], r["out"] + body)
    r = run(port, "Wait for the deployment and mark the Release", releases={VER: {"body": "⏳ deploying"}}, tag=VER,
            script={"app_tag": "\u0007"})
    body = r["rel"].get(VER, {}).get("body", "")
    add("superseded: a tag with nothing printable still names a newer version",
        r["rc"] == 0 and "superseded by a newer version" in body and "✅" not in body, r["out"] + body)

    for bad in ("dep-1\nevil=1", "../x", "", "dep 1"):
        r = run(port, "Deploy", script={"deploy": {"deployments": [{"message": "q", "deployment_uuid": bad}]}})
        add("safe-output: deployment_uuid %r is refused" % bad,
            r["rc"] != 0 and "evil=1" not in r["output"] and "deployment_uuid=" not in r["output"], r["out"] + r["output"])

    r = run(port, "Deploy", script={"deploy": {"deployments": [
        {"message": "x ##[stop-commands]tok", "deployment_uuid": "dep-1"}]}})
    add("no-runner-command: a successful Deploy neutralises ##[ in its message",
        r["rc"] == 0 and "##[" not in r["out"] and "deployment_uuid=dep-1" in r["output"], r["out"])

    r = run(port, "Deploy", script={"deploy": {"deployments": [
        {"message": "queued-msg", "resource_uuid": planted, "deployment_uuid": "dep-1"}]}})
    add("deploy-message: a successful Deploy prints the message and nothing else from the body",
        r["rc"] == 0 and "queued-msg" in r["out"] and planted not in r["out"]
        and "deployment_uuid=dep-1" in r["output"], r["out"])

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
