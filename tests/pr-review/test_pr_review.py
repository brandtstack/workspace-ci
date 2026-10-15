#!/usr/bin/env python3
"""Offline rows for .github/workflows/pr-review.yml.

Runs the workflow's own `run:` block, read from the YAML, under `bash -eo pipefail` like the runner,
against a fake `gh` and a stub server on 127.0.0.1 that plays GitHub's OIDC endpoint and the
Anthropic token exchange and Messages API. Nothing leaves the machine.

Run: python3 tests/pr-review/test_pr_review.py     (needs python3 with PyYAML, bash, curl, jq, awk)
"""
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(HERE, "..", "..", ".github", "workflows", "pr-review.yml")
FAKE_GH = os.path.join(HERE, "fake_gh.py")
# Built at runtime so no token-shaped literal sits in this public repo.
TOKEN = "sk-ant-" + "oat01-" + "fixture-access-token"
JWT = "eyJ" + "fixture.oidc.jwt"
OIDC_BEARER = "fixture-request-token"
MARKER = "<!-- pr-review -->"


def file_diff(path, body="+x\n"):
    return "diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n@@ -0,0 +1 @@\n%s" % (path, path, path, path, body)


DIFF = (file_diff("src/app.ts", "+export const a = 1;\n")
        + file_diff("package-lock.json", "+lockfile-content\n")
        + file_diff("go.sum", "+gosum-content\n")
        + file_diff("web/dist/bundle.js", "+bundle-content\n")
        + file_diff("api/types_generated.go", "+generated-content\n")
        + file_diff("lib/util.go", "+func util() {}\n"))


class Stub(http.server.BaseHTTPRequestHandler):
    mode = {}
    seen = []

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
        Stub.seen.append(("GET", self.path, dict(self.headers), None))
        if self.path.startswith("/oidc") and self.headers.get("Authorization") == "Bearer " + OIDC_BEARER:
            return self.reply(200, {"value": JWT})
        self.reply(403, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        Stub.seen.append(("POST", self.path, dict(self.headers), body))
        m = Stub.mode
        if self.path == "/v1/oauth/token":
            if m.get("exchange", 200) != 200:
                return self.reply(m["exchange"], {"type": "error", "error": {
                    "type": "authentication_error", "message": "Authentication failed"}})
            return self.reply(200, {"access_token": TOKEN, "expires_in": 600, "token_type": "bearer"})
        if self.path == "/v1/messages":
            if self.headers.get("authorization") != "Bearer " + TOKEN:
                return self.reply(401, {"type": "error", "error": {"type": "authentication_error"}})
            if m.get("messages", 200) != 200:
                return self.reply(m["messages"], {"type": "error", "error": {"type": "api_error"}})
            return self.reply(200, {
                "content": [{"type": "text", "text": m.get("text", "**Verdict:** no findings.")}],
                "stop_reason": m.get("stop_reason", "end_turn"),
                "usage": {"input_tokens": 1234, "output_tokens": 56}})
        self.reply(404, {})


def script():
    with open(WORKFLOW) as f:
        return yaml.safe_load(f)["jobs"]["review"]["steps"][0]["run"]


class Run:
    def __init__(self, port, base, *, diff=DIFF, comments=None, env=None, mode=None, api_port=None):
        Stub.mode, Stub.seen[:] = dict(mode or {}), []
        self.base = base
        self.state_path = os.path.join(base, "gh-state.json")
        with open(self.state_path, "w") as f:
            json.dump({"diff": diff, "diff_fails": (env or {}).pop("_diff_fails", False),
                       "comments": comments or [], "calls": []}, f)
        bindir = os.path.join(base, "bin")
        os.makedirs(bindir, exist_ok=True)
        shutil.copy(FAKE_GH, os.path.join(bindir, "gh"))
        os.chmod(os.path.join(bindir, "gh"), 0o755)
        self.runner_temp = os.path.join(base, "runner-temp")
        os.makedirs(self.runner_temp, exist_ok=True)
        self.summary = os.path.join(base, "summary.md")
        open(self.summary, "w").close()
        e = {
            "PATH": bindir + os.pathsep + os.environ["PATH"], "HOME": base,
            "FAKE_GH_STATE": self.state_path, "RUNNER_TEMP": self.runner_temp,
            "GITHUB_STEP_SUMMARY": self.summary,
            "PR_REVIEW_API_BASE": "http://127.0.0.1:%d" % (api_port or port),
            "ACTIONS_ID_TOKEN_REQUEST_URL": "http://127.0.0.1:%d/oidc?api-version=2.0" % port,
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": OIDC_BEARER,
            "GH_TOKEN": "fixture-gh-token", "REPO": "acme/thing", "PR": "7",
            "HEAD_SHA": "0123456789abcdef", "TITLE": "feat: a change", "DRAFT": "false",
            "AUTHOR": "someone", "MODEL": "claude-sonnet-5-5", "MAX_TOKENS": "4096",
            "MAX_DIFF_BYTES": "153600", "REVIEW_DRAFTS": "false", "REVIEW_RENOVATE": "false",
            "FEDERATION_RULE_ID": "rule-fixture", "ORGANIZATION_ID": "org-fixture",
            "SERVICE_ACCOUNT_ID": "sa-fixture", "WORKSPACE_ID": "",
        }
        e.update(env or {})
        path = os.path.join(base, "step.sh")
        with open(path, "w") as f:
            f.write(script())
        r = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", path],
                           env=e, capture_output=True, text=True, cwd=base, timeout=60)
        self.code, self.out, self.err = r.returncode, r.stdout, r.stderr
        with open(self.state_path) as f:
            self.gh = json.load(f)
        with open(self.summary) as f:
            self.summary_text = f.read()
        self.seen = list(Stub.seen)

    def posts(self, path):
        return [s for s in self.seen if s[0] == "POST" and s[1] == path]

    def leaks(self):
        """Places the access token or the OIDC JWT shows up, other than the add-mask lines."""
        found = []
        for where, text in (("stdout", self.out), ("stderr", self.err), ("summary", self.summary_text)):
            for line in text.splitlines():
                if line.startswith("::add-mask::"):
                    continue
                if TOKEN in line or JWT in line or "sk-ant-oat01" in line:
                    found.append(where)
        for c in self.gh["comments"]:
            if "sk-ant-oat01" in c["body"] or JWT in c["body"]:
                found.append("comment")
        return found

    def masked_first(self):
        lines = self.out.splitlines()
        hits = [i for i, l in enumerate(lines) if TOKEN in l]
        return bool(hits) and lines[hits[0]] == "::add-mask::" + TOKEN

    def temp_clean(self):
        return os.listdir(self.runner_temp) == []


def rows(port, dead_port, tmp):
    checks = []
    n = [0]

    def fresh():
        n[0] += 1
        d = os.path.join(tmp, "case%d" % n[0])
        os.makedirs(d)
        return d

    def add(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    # Happy path: first review posts one comment.
    r = Run(port, fresh())
    req = r.posts("/v1/messages")
    sent = req[0][3] if req else {}
    prompt = sent.get("messages", [{}])[0].get("content", "")
    add("happy: exit 0", r.code == 0, r.err)
    add("happy: one comment posted with the marker", len(r.gh["comments"]) == 1 and r.gh["comments"][0]["body"].startswith(MARKER))
    add("happy: comment carries the model text", "**Verdict:** no findings." in r.gh["comments"][0]["body"] if r.gh["comments"] else False)
    add("happy: comment carries usage", "1234 in / 56 out" in (r.gh["comments"][0]["body"] if r.gh["comments"] else ""))
    add("happy: usage logged", 'pr-review: usage {"input_tokens":1234,"output_tokens":56}' in r.out, r.out)
    add("happy: OIDC asked for the anthropic audience", any("audience=https://api.anthropic.com" in s[1] for s in r.seen if s[0] == "GET"))
    ex = r.posts("/v1/oauth/token")
    exb = ex[0][3] if ex else {}
    add("exchange: jwt-bearer grant with the JWT", exb.get("grant_type") == "urn:ietf:params:oauth:grant-type:jwt-bearer" and exb.get("assertion") == JWT)
    add("exchange: rule, org and service account sent",
        (exb.get("federation_rule_id"), exb.get("organization_id"), exb.get("service_account_id")) == ("rule-fixture", "org-fixture", "sa-fixture"))
    add("exchange: empty workspace id left out", "workspace_id" not in exb, json.dumps(exb))
    add("messages: bearer access token, version header",
        req and req[0][2].get("authorization") == "Bearer " + TOKEN and req[0][2].get("anthropic-version") == "2023-06-01")
    add("messages: model and max_tokens from inputs", sent.get("model") == "claude-sonnet-5-5" and sent.get("max_tokens") == 4096)
    add("messages: no tools", "tools" not in sent and "tool_choice" not in sent)
    add("diff filter: source files kept", "export const a = 1;" in prompt and "func util() {}" in prompt)
    for word, what in (("lockfile-content", "package-lock.json"), ("gosum-content", "go.sum"),
                       ("bundle-content", "dist/"), ("generated-content", "_generated.go")):
        add("diff filter: %s left out" % what, word not in prompt)
    add("diff filter: count logged", "4 files left out" in r.out, r.out)
    add("token: add-mask comes before any other appearance", r.masked_first(), r.out)
    add("token: in no log, summary or comment", not r.leaks(), str(r.leaks()))
    add("token: RUNNER_TEMP cleaned up", r.temp_clean(), str(os.listdir(r.runner_temp)))

    # Workspace id is sent when set.
    r = Run(port, fresh(), env={"WORKSPACE_ID": "ws-fixture"})
    ex = r.posts("/v1/oauth/token")
    add("exchange: workspace id sent when set", ex and ex[0][3].get("workspace_id") == "ws-fixture")

    # Upsert: an existing marker comment is edited, others are left alone.
    others = [{"id": 1, "body": "a human comment"}, {"id": 2, "body": MARKER + "\nold review"}]
    r = Run(port, fresh(), comments=others, mode={"text": "**Verdict:** 1 finding(s)"})
    bodies = {c["id"]: c["body"] for c in r.gh["comments"]}
    add("upsert: still two comments", len(r.gh["comments"]) == 2, json.dumps(r.gh["comments"]))
    add("upsert: marker comment edited in place", "1 finding(s)" in bodies.get(2, "") and "old review" not in bodies.get(2, ""))
    add("upsert: other comment untouched", bodies.get(1) == "a human comment")
    add("upsert: no new comment call", not any(c[:2] == ["pr", "comment"] for c in r.gh["calls"]))
    add("upsert: logs the update", "updated comment 2" in r.out, r.out)

    # Model text with shell metacharacters lands literally, nothing runs.
    canary = os.path.join(tmp, "canary")
    evil = "$(touch %s) `touch %s` ${HOME} \"'; touch %s" % (canary, canary, canary)
    r = Run(port, fresh(), mode={"text": evil}, env={"TITLE": "fix: $(touch %s)" % canary})
    add("body: shell text posted literally", r.gh["comments"] and evil in r.gh["comments"][0]["body"])
    add("body: nothing in model text or title executed", not os.path.exists(canary))

    # Cut off at max_tokens.
    r = Run(port, fresh(), mode={"stop_reason": "max_tokens"})
    add("max_tokens: cut-off noted", "Cut off at max-tokens (4096)" in r.gh["comments"][0]["body"] if r.gh["comments"] else False)

    # Size cap: over the cap, a short comment, no exchange.
    big = file_diff("src/big.ts", "+" + "y" * 2000 + "\n")
    r = Run(port, fresh(), diff=big, env={"MAX_DIFF_BYTES": "1000"})
    add("cap: exit 0", r.code == 0, r.err)
    add("cap: 'not reviewed' comment with the marker",
        len(r.gh["comments"]) == 1 and r.gh["comments"][0]["body"].startswith(MARKER) and "not reviewed" in r.gh["comments"][0]["body"])
    add("cap: no OIDC, no exchange, no messages", not r.seen)
    add("cap: warning", "::warning::pr-review: diff too large" in r.out, r.out)
    r = Run(port, fresh(), diff=big, env={"MAX_DIFF_BYTES": "1000"}, comments=[{"id": 5, "body": MARKER + "\nold"}])
    add("cap: replaces an earlier review", len(r.gh["comments"]) == 1 and "not reviewed" in r.gh["comments"][0]["body"])
    add("cap: lockfile bytes do not count", Run(port, fresh(), diff=DIFF + file_diff("package-lock.json", "+" + "z" * 5000 + "\n"),
                                                env={"MAX_DIFF_BYTES": "1000"}).posts("/v1/messages"))

    # Exchange denied: exit 0, warning and summary, no messages call, no comment.
    r = Run(port, fresh(), mode={"exchange": 401})
    add("401 exchange: exit 0", r.code == 0, r.err)
    add("401 exchange: warning names the HTTP code", "::warning::pr-review: token exchange failed (HTTP 401)" in r.out, r.out)
    add("401 exchange: job summary written", "token exchange failed (HTTP 401)" in r.summary_text)
    add("401 exchange: no messages call, no comment", not r.posts("/v1/messages") and not r.gh["comments"])
    add("401 exchange: no token or JWT in output", not r.leaks(), str(r.leaks()))
    add("401 exchange: RUNNER_TEMP cleaned up", r.temp_clean())

    # Exchange unreachable.
    r = Run(port, fresh(), api_port=dead_port)
    add("unreachable API: exit 0 with HTTP 000", r.code == 0 and "(HTTP 000)" in r.out, r.out + r.err)

    # Messages API error.
    r = Run(port, fresh(), mode={"messages": 529})
    add("messages error: exit 0", r.code == 0, r.err)
    add("messages error: warning with code and type", "HTTP 529 (api_error)" in r.out, r.out)
    add("messages error: no comment", not r.gh["comments"])
    add("messages error: no token in output", not r.leaks(), str(r.leaks()))

    # Not configured: the pilot's first run.
    r = Run(port, fresh(), env={"FEDERATION_RULE_ID": "", "ORGANIZATION_ID": "", "SERVICE_ACCOUNT_ID": ""})
    add("no vars: exit 0", r.code == 0, r.err)
    add("no vars: warning names the variables", "vars.ANTHROPIC_FEDERATION_RULE_ID" in r.out, r.out)
    add("no vars: no OIDC request, no comment", not r.seen and not r.gh["comments"])
    r = Run(port, fresh(), env={"ACTIONS_ID_TOKEN_REQUEST_URL": ""})
    add("no id-token permission: exit 0 with a hint", r.code == 0 and "id-token: write" in r.out, r.out)

    # Skips.
    r = Run(port, fresh(), env={"DRAFT": "true"})
    add("draft: skipped, exit 0", r.code == 0 and "draft PR" in r.out and not r.gh["calls"])
    r = Run(port, fresh(), env={"DRAFT": "true", "REVIEW_DRAFTS": "true"})
    add("draft with review-drafts: reviewed", len(r.gh["comments"]) == 1)
    r = Run(port, fresh(), env={"AUTHOR": "renovate[bot]"})
    add("renovate: skipped, exit 0", r.code == 0 and "Renovate PR" in r.out and not r.gh["calls"])
    r = Run(port, fresh(), env={"AUTHOR": "renovate[bot]", "REVIEW_RENOVATE": "true"})
    add("renovate with review-renovate: reviewed", len(r.gh["comments"]) == 1)

    # Nothing left after the filter, and a diff that cannot be read.
    r = Run(port, fresh(), diff=file_diff("package-lock.json") + file_diff("go.sum"))
    add("only lockfiles: exit 0, nothing sent", r.code == 0 and "nothing left to review" in r.out and not r.seen)
    r = Run(port, fresh(), env={"_diff_fails": True})
    add("diff unreadable: exit 0 with the error", r.code == 0 and "could not read the diff: HTTP 406" in r.out, r.out)
    return checks


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    dead = http.server.HTTPServer(("127.0.0.1", 0), Stub)
    dead_port = dead.server_address[1]
    dead.server_close()  # a port nothing listens on
    tmp = tempfile.mkdtemp()
    try:
        checks = rows(server.server_address[1], dead_port, tmp)
    finally:
        server.shutdown()
        shutil.rmtree(tmp)
    failed = 0
    for name, ok, detail in checks:
        print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok or not detail else " -- " + detail[:400]))
        failed += not ok
    print("%d rows, %d failed" % (len(checks), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
