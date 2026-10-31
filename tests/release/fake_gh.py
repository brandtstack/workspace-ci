#!/usr/bin/env python3
"""A stand-in for the `gh release` calls release.yml makes. State lives in the JSON file $FAKE_GH_STATE:
{"releases": {tag: {"body": str, "latest": bool}}, "fail": [subcommand...], "calls": [[argv...]]}
`create` with --generate-notes appends a generated block after the --notes-file text, the way gh
documents it ("Additional release notes can be prepended").
"""
import json
import os
import sys

GENERATED = "## What's Changed\n* feat: a change by @someone in #7\n"
path = os.environ["FAKE_GH_STATE"]
with open(path) as f:
    state = json.load(f)
argv = sys.argv[1:]
state["calls"].append(argv)
rel = state.setdefault("releases", {})


def save():
    with open(path, "w") as f:
        json.dump(state, f)


def notes():
    if "--notes-file" in argv:
        with open(argv[argv.index("--notes-file") + 1]) as f:
            return f.read()
    return ""


if argv[:1] == ["release"] and len(argv) >= 3:
    sub, tag = argv[1], argv[2]
    if sub in state.get("fail", []):
        save()
        sys.stderr.write("HTTP 500: fake failure\n")
        sys.exit(1)
    if sub == "view":
        save()
        if tag not in rel:
            sys.stderr.write("release not found\n")
            sys.exit(1)
        if "--json" in argv:
            print(rel[tag]["body"])
        sys.exit(0)
    if sub == "create":
        text = notes()
        if "--generate-notes" in argv:
            text = (text + "\n" if text else "") + GENERATED
        rel[tag] = {"body": text, "latest": "--latest" in argv}
        save()
        sys.exit(0)
    if sub == "edit":
        if tag not in rel:
            save()
            sys.exit(1)
        if "--notes-file" in argv:
            rel[tag]["body"] = notes()
        if "--latest" in argv:
            rel[tag]["latest"] = True
        save()
        sys.exit(0)
save()
sys.stderr.write("fake gh: unhandled %r\n" % argv)
sys.exit(2)
