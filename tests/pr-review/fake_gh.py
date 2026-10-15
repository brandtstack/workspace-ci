#!/usr/bin/env python3
"""A stand-in for the four `gh` calls pr-review.yml makes. State lives in the JSON file $FAKE_GH_STATE:
{"diff": str, "diff_fails": bool, "comments": [{"id": int, "body": str}], "calls": [[argv...]]}
"""
import json
import os
import subprocess
import sys

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
        sys.stderr.write("HTTP 406: diff too large\n")
        sys.exit(1)
    sys.stdout.write(state["diff"])
elif argv[:2] == ["pr", "comment"]:
    with open(arg("--body-file")) as f:
        body = f.read()
    state["comments"].append({"id": 1000 + len(state["comments"]), "body": body})
    save()
elif argv[0] == "api" and "PATCH" in argv:
    cid = int([a for a in argv if a.startswith("repos/")][0].rsplit("/", 1)[1])
    body = json.load(sys.stdin)["body"]
    for c in state["comments"]:
        if c["id"] == cid:
            c["body"] = body
    save()
elif argv[0] == "api" and "--paginate" in argv:
    save()
    r = subprocess.run(["jq", "-r", arg("--jq")], input=json.dumps(state["comments"]),
                       capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    sys.exit(r.returncode)
else:
    save()
    sys.stderr.write("fake gh: unexpected call %r\n" % argv)
    sys.exit(2)
