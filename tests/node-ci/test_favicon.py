#!/usr/bin/env python3
"""Offline rows for the `favicon` input and tabcheck step of .github/workflows/node-ci.yml.

Read from the YAML only; nothing runs. Run: python3 tests/node-ci/test_favicon.py (needs PyYAML)
"""
import os
import re
import sys
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(HERE, "..", "..", ".github", "workflows", "node-ci.yml")

with open(WORKFLOW) as f:
    WF = yaml.safe_load(f)

# PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
ON = WF.get("on", WF.get(True))
STEPS = WF["jobs"]["verify"]["steps"]
TABCHECK = re.compile(r"^brandtstack/tabcheck@[0-9a-f]{40}$")


def tabcheck_index():
    for i, s in enumerate(STEPS):
        if TABCHECK.match(s.get("uses", "")):
            return i
    return None


def verify_index():
    for i, s in enumerate(STEPS):
        if s.get("run", "").strip() == "npm run verify":
            return i
    return None


class FaviconTests(unittest.TestCase):
    def test_input_declared(self):
        inp = ON["workflow_call"]["inputs"].get("favicon")
        self.assertIsNotNone(inp, "favicon input missing")
        self.assertEqual(inp["type"], "string")
        self.assertEqual(inp["default"], "")

    def test_step_guarded(self):
        i = tabcheck_index()
        self.assertIsNotNone(i, "no tabcheck@<40-hex> step in the verify job")
        step = STEPS[i]
        self.assertEqual(step.get("if"), "inputs.favicon != ''")
        self.assertEqual(step["with"]["path"], "${{ inputs.favicon }}")

    def test_step_after_verify(self):
        t, v = tabcheck_index(), verify_index()
        self.assertIsNotNone(v, "npm run verify step not found")
        self.assertIsNotNone(t, "tabcheck step not found")
        self.assertGreater(t, v)

    def test_playwright_env(self):
        t = tabcheck_index()
        self.assertIsNotNone(t, "tabcheck step not found")
        want = "/opt/hostedtoolcache/ms-playwright"
        step_env = STEPS[t].get("env", {})
        job_env = WF["jobs"]["verify"].get("env", {})
        got = step_env.get("PLAYWRIGHT_BROWSERS_PATH", job_env.get("PLAYWRIGHT_BROWSERS_PATH"))
        self.assertEqual(got, want)

    def test_working_directory_independent(self):
        t = tabcheck_index()
        self.assertIsNotNone(t, "tabcheck step not found")
        self.assertNotIn("working-directory", STEPS[t])


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
