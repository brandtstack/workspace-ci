#!/usr/bin/env python3
"""A stand-in for the four `gh` calls pr-review.yml makes. State lives in the JSON file $FAKE_GH_STATE:
{"diff": str, "diff_fails": bool, "diff_err": str, "comments": [{"id": int, "user": {"login": str}, "body": str}],
 "patch_fails": bool, "calls": [[argv...]]}
Comment lists are served the way `gh api --paginate` prints them: one JSON array per page, two
comments per page, so the workflow has to merge the pages.
"""
import json
import os
import sys

BOT = {"login": "github-actions[bot]"}
path = os.environ["FAKE_GH_STATE"]
with open(path) as f:
    state = json.load(f)
argv = sys.argv[1:]
state["calls"].append(argv)


def save():
    with open(path, "w") as f:
        json.dump(state, f)


def arg(flag):
    return argv[argv.index(flag) + 1]


if argv[:2] == ["pr", "diff"]:
    save()
    if state.get("diff_fails"):
        sys.stderr.write(state.get("diff_err", "HTTP 406: diff too large\n"))
        sys.exit(1)
    sys.stdout.write(state["diff"])
elif argv[:2] == ["pr", "comment"]:
    with open(arg("--body-file")) as f:
        body = f.read()
    state["comments"].append({"id": 1000 + len(state["comments"]), "user": BOT, "body": body})
    save()
elif argv[0] == "api" and "PATCH" in argv:
    cid = int([a for a in argv if a.startswith("repos/")][0].rsplit("/", 1)[1])
    body = json.load(sys.stdin)["body"]
    save()
    target = [c for c in state["comments"] if c["id"] == cid]
    if not target or target[0]["user"] != BOT or state.get("patch_fails"):
        sys.stderr.write("HTTP 403: Resource not accessible by integration\n")
        sys.exit(1)
    target[0]["body"] = body
    save()
elif argv[0] == "api" and "--paginate" in argv:
    save()
    c = state["comments"]
    pages = [c[i:i + 2] for i in range(0, len(c), 2)] or [[]]
    sys.stdout.write("".join(json.dumps(p) for p in pages))
else:
    save()
    sys.stderr.write("fake gh: unexpected call %r\n" % argv)
    sys.exit(2)
