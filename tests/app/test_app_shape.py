#!/usr/bin/env python3
"""Offline shape rows for .github/workflows/app.yml, the contract workflow. Read from the YAML only;
nothing runs. Run: python3 tests/app/test_app_shape.py   (needs PyYAML)
"""
import os
import re
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(HERE, "..", "..", ".github", "workflows", "app.yml")

with open(WORKFLOW) as f:
    TEXT = f.read()
WF = yaml.safe_load(TEXT)
# PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
ON = WF.get("on", WF.get(True))
JOBS = WF["jobs"]
PIN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40}$")
OUTPUTS = ["family", "dockerfile", "node", "go", "prisma", "dotenvx", "integration", "favicon",
           "build-args", "private-registry", "coolify-app", "smoke-args", "go-main", "go-dir"]


def steps(job):
    return JOBS[job]["steps"]


def find(job, uses_prefix):
    return [s for s in steps(job) if s.get("uses", "").startswith(uses_prefix)]


def runs(job):
    return [s.get("run", "") for s in steps(job)]


class AppShape(unittest.TestCase):
    def test_job_ids(self):
        self.assertEqual(set(JOBS), {"verify", "integration", "image"})

    def test_inputs_are_runner_and_path_only(self):
        inputs = ON["workflow_call"]["inputs"]
        self.assertEqual(set(inputs), {"runner", "path"})
        self.assertEqual(inputs["path"]["default"], ".")
        self.assertEqual(inputs["runner"]["default"], '"ubuntu-latest"')
        self.assertNotIn("secrets", ON["workflow_call"])

    def test_permissions(self):
        self.assertEqual(WF["permissions"], {"contents": "read"})
        self.assertEqual(JOBS["verify"]["permissions"], {"contents": "read", "packages": "read"})
        self.assertEqual(JOBS["integration"]["permissions"], {"contents": "read", "packages": "read"})
        self.assertEqual(JOBS["image"]["permissions"], {"contents": "read", "packages": "write"})

    def test_verify_reexports_every_contract_output(self):
        self.assertEqual(set(JOBS["verify"]["outputs"]), set(OUTPUTS))
        for k in OUTPUTS:
            self.assertEqual(JOBS["verify"]["outputs"][k], "${{ steps.contract.outputs.%s }}" % k)
        self.assertEqual(len([s for s in steps("verify") if s.get("id") == "contract"]), 1)

    def test_integration_gated_by_contract_output(self):
        j = JOBS["integration"]
        self.assertIn("needs.verify.outputs.integration", j["if"])
        self.assertEqual(j["needs"], "verify")

    def test_image_gating(self):
        j = JOBS["image"]
        self.assertEqual(j["needs"], "verify")
        self.assertIn("github.event_name == 'pull_request'", j["if"])

    def test_own_ref_checkout_guarded(self):
        for job in ("verify", "image"):
            ss = steps(job)
            idx = [i for i, s in enumerate(ss) if s.get("with", {}).get("path") == ".ws-ci"]
            self.assertEqual(len(idx), 1, job)
            co = ss[idx[0]]
            self.assertEqual(co["with"]["repository"], "${{ job.workflow_repository }}")
            self.assertEqual(co["with"]["ref"], "${{ job.workflow_sha }}")
            self.assertIs(co["with"]["persist-credentials"], False)
            guard = ss[idx[0] - 1]
            self.assertIn("::error::", guard["run"])
            self.assertIn("job.workflow_sha", str(guard))
            self.assertIn("job.workflow_repository", str(guard))
        self.assertNotRegex(TEXT, r"ref:\s*main")

    def test_build_step(self):
        b = find("image", "docker/build-push-action@")
        self.assertEqual(len(b), 1)
        w = b[0]["with"]
        self.assertIn("needs.verify.outputs.dockerfile", w["file"])
        self.assertNotIn("inputs.dockerfile", TEXT)
        self.assertIn("needs.verify.outputs.build-args", w["build-args"])
        self.assertIn("needs.verify.outputs.private-registry", w["secret-envs"])
        self.assertIn("npm_token=NPM_TOKEN", w["secret-envs"])
        self.assertEqual(w["context"], "${{ inputs.path }}")
        for fam, arg in (("next", "NODE="), ("spa", "NODE="), ("go", "GO=")):
            self.assertIn(arg, w["build-args"])
        for a in ("PRISMA=", "GO_MAIN=", "GO_DIR="):
            self.assertIn(a, w["build-args"])
        self.assertIn("runtime-dotenvx", w["target"])
        self.assertIn("runtime-plain", w["target"])

    def test_no_secret_as_build_arg(self):
        self.assertNotRegex(TEXT, r"(?i)build-args:.*(token|secret)")

    def test_no_verify_in_dockerfile_path(self):
        for job in JOBS:
            for s in steps(job):
                if "file" in s.get("with", {}):
                    self.assertNotIn("npm run verify", str(s))

    def test_smoke_literals(self):
        smoke = "\n".join(runs("image"))
        self.assertIn("-p 8080:80", smoke)
        self.assertIn("http://127.0.0.1:8080/healthz", smoke)
        self.assertIn("http://127.0.0.1:80/healthz", smoke)
        self.assertIn("needs.verify.outputs.smoke-args", TEXT)
        for s in steps("image"):
            if "run" in s and "docker run" in s["run"]:
                self.assertNotIn("inputs.", s["run"])
        code = [l for l in TEXT.splitlines() if not l.lstrip().startswith("#")]
        self.assertFalse([l for l in code if "localhost" in l], "localhost dialled")

    def test_internal_smoke_skips_go_images(self):
        internal = [s for s in steps("image") if "docker exec smoke" in s.get("run", "")]
        self.assertEqual(len(internal), 1)
        self.assertIn("needs.verify.outputs.node", internal[0]["if"])

    def test_publish_guard(self):
        pub = [s for s in steps("image") if s.get("uses", "").startswith("docker/login-action@")
               or "docker push" in s.get("run", "")]
        self.assertEqual(len(pub), 2)
        for s in pub:
            self.assertIn("github.repository != 'brandtstack/workspace-ci'", s["if"])

    def test_every_uses_is_a_digest_with_version_comment(self):
        found = 0
        for line in TEXT.splitlines():
            m = re.match(r"^\s*(?:- )?uses:\s*(\S+)(.*)$", line)
            if m:
                found += 1
                self.assertRegex(m.group(1), PIN, line)
                self.assertRegex(m.group(2), r"#\s*v\d", line)
        self.assertGreater(found, 5)
        for job in JOBS.values():
            for s in job["steps"]:
                if "uses" in s:
                    self.assertRegex(s["uses"], PIN)

    def test_integration_dials_127(self):
        env = JOBS["integration"]["env"]["DATABASE_URL"]
        self.assertIn("@127.0.0.1:5432/", env)

    def test_working_directory_is_path(self):
        for job in ("verify", "integration"):
            self.assertEqual(JOBS[job]["defaults"]["run"]["working-directory"], "${{ inputs.path }}")


if __name__ == "__main__":
    unittest.main()
