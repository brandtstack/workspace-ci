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
           "build-args", "private-registry", "coolify-app", "smoke-args", "go-main", "go-dir", "migrate"]


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

    def test_checkout_moved_out_of_workspace(self):
        for job in ("verify", "image"):
            ss = steps(job)
            i = [k for k, s in enumerate(ss) if s.get("with", {}).get("path") == ".ws-ci"][0]
            mv = ss[i + 1]["run"]
            self.assertIn('mv "$GITHUB_WORKSPACE/.ws-ci" "$RUNNER_TEMP/ws-ci"', mv)
            self.assertIn('-e "$RUNNER_TEMP/ws-ci"', mv)
            for s in ss[i + 2:]:
                self.assertNotIn(".ws-ci", str(s), job)

    def test_no_template_expression_in_run_scripts(self):
        for job in JOBS:
            for s in steps(job):
                self.assertNotIn("${{", s.get("run", ""), s)

    def test_build_step(self):
        b = find("image", "docker/build-push-action@")
        self.assertEqual(len(b), 1)
        w = b[0]["with"]
        self.assertIn("runner.temp", w["file"])
        self.assertIn("ws-ci/dockerfiles/", w["file"])
        self.assertIn("inputs.path", w["file"])
        self.assertNotIn("inputs.dockerfile", TEXT)
        self.assertIn("PUBLIC_BUILD_ENV_B64", w["build-args"])
        self.assertIn("steps.bargs.outputs.b64", w["build-args"])
        self.assertNotIn("needs.verify.outputs.build-args", w["build-args"])
        enc = [s for s in steps("image") if s.get("id") == "bargs"]
        self.assertEqual(len(enc), 1)
        self.assertNotIn("${{", enc[0]["run"])
        self.assertIn("base64 -w0", enc[0]["run"])
        self.assertEqual(enc[0]["env"]["BUILD_ARGS"], "${{ needs.verify.outputs.build-args }}")
        self.assertIn("needs.verify.outputs.private-registry", w["secret-envs"])
        self.assertIn("npm_token=NPM_TOKEN", w["secret-envs"])
        self.assertEqual(w["context"], "${{ inputs.path }}")
        for fam, arg in (("next", "NODE="), ("spa", "NODE="), ("go", "GO=")):
            self.assertIn(arg, w["build-args"])
        for a in ("PRISMA=", "GO_MAIN=", "GO_DIR="):
            self.assertIn(a, w["build-args"])
        self.assertEqual(w["build-contexts"], "ws=${{ runner.temp }}/ws-ci/dockerfiles")
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

    def test_dotenvx_smoke_bypasses_the_entrypoint(self):
        smoke = [s for s in steps("image") if s.get("name") == "Boot and smoke test"][0]
        self.assertEqual(smoke["env"]["FAMILY"], "${{ needs.verify.outputs.family }}")
        self.assertEqual(smoke["env"]["DOTENVX"], "${{ needs.verify.outputs.dotenvx }}")
        run = smoke["run"]
        self.assertNotIn("${{", run)
        self.assertIn('[ "$FAMILY" = next ] && [ "$DOTENVX" = true ]', run)
        runs_ = [l.strip() for l in run.splitlines() if l.strip().startswith("docker run")]
        self.assertEqual(len(runs_), 2)
        bypass = [l for l in runs_ if "--entrypoint /bin/sh" in l]
        self.assertEqual(len(bypass), 1)
        self.assertTrue(bypass[0].endswith("--entrypoint /bin/sh ci-image:local /app/start.sh"), bypass[0])
        plain = [l for l in runs_ if "--entrypoint" not in l][0]
        self.assertTrue(plain.endswith("$SMOKE_ARGS ci-image:local"), plain)

    def test_custom_gets_the_public_build_env(self):
        b = find("image", "docker/build-push-action@")[0]["with"]["build-args"]
        line = [l for l in b.splitlines() if "PUBLIC_BUILD_ENV_B64" in l and "format(" in l][0]
        self.assertIn('["next","spa","custom"]', line)
        node = [l for l in b.splitlines() if "format('NODE=" in l][0]
        self.assertNotIn("custom", node)

    def test_govulncheck_script_identical_to_go_ci(self):
        # Until go-ci.yml is deleted, the two copies of the scan script must not drift.
        with open(os.path.join(HERE, "..", "..", ".github", "workflows", "go-ci.yml")) as f:
            go_ci = yaml.safe_load(f)
        a = [s for s in go_ci["jobs"]["verify"]["steps"] if s.get("name") == "govulncheck"]
        b = [s for s in steps("verify") if s.get("name") == "govulncheck"]
        self.assertEqual((len(a), len(b)), (1, 1))
        self.assertEqual(a[0]["run"], b[0]["run"])

    def test_internal_smoke_skips_go_images(self):
        # wget runs only in node images; go images exec their own healthcheck subcommand instead.
        wget = [s for s in steps("image") if "docker exec smoke wget" in s.get("run", "")]
        self.assertEqual(len(wget), 1)
        self.assertIn("needs.verify.outputs.node", wget[0]["if"])

    def test_migrate_cli_step(self):
        mig = [s for s in steps("image") if s.get("name") == "Migrate CLI loads"]
        self.assertEqual(len(mig), 1)
        self.assertEqual(mig[0]["if"], "needs.verify.outputs.migrate == 'true'")
        self.assertIn("migrate status", mig[0]["run"])
        self.assertIn("Error: P1001: Can't reach database server at", mig[0]["run"])
        self.assertIn("127.0.0.1:1", mig[0]["run"])
        names = [s.get("name", "") for s in steps("image")]
        self.assertLess(names.index("Migrate CLI loads"), names.index("Tear down"))
        boot = [s for s in steps("image") if s.get("name") == "Boot and smoke test"][0]
        self.assertIn("SKIP_MIGRATIONS=1", boot["run"])
        self.assertEqual(boot["env"]["MIGRATE"], "${{ needs.verify.outputs.migrate }}")
        # A dotenvx smoke still goes through start.sh, never straight to server.js.
        self.assertIn("--entrypoint /bin/sh ci-image:local /app/start.sh", boot["run"])
        self.assertNotIn("server.js", boot["run"])

    def test_go_smoke_runs_healthcheck_and_waits_for_healthy(self):
        go = [s for s in steps("image") if "docker exec smoke /app healthcheck" in s.get("run", "")]
        self.assertEqual(len(go), 1)
        self.assertEqual(go[0]["if"], "needs.verify.outputs.family == 'go'")
        self.assertIn(".State.Health.Status", go[0]["run"])
        names = [s.get("name", "") for s in steps("image")]
        # After the boot step (the container exists), before the teardown.
        self.assertLess(names.index("Boot and smoke test"), names.index(go[0]["name"]))
        self.assertLess(names.index(go[0]["name"]), names.index("Tear down"))

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

    def test_ref_check_after_checkout_in_both_jobs(self):
        for job in ("verify", "image"):
            ss = steps(job)
            i = [k for k, x in enumerate(ss) if x.get("with", {}).get("path") == ".ws-ci"][0]
            chk = ss[i + 2]
            self.assertEqual(chk["name"], "Check the workflow ref")
            self.assertNotIn("${{", chk["run"])
            for k in ("WF_REPO", "WF_SHA", "RUN_REPO", "RUN_SHA"):
                self.assertIn(k, chk["env"])
            self.assertEqual(chk["env"]["WF_SHA"], "${{ job.workflow_sha }}")
            self.assertEqual(chk["env"]["RUN_SHA"], "${{ github.sha }}")
            self.assertIn('"$WF_SHA" != "$RUN_SHA"', chk["run"])
            self.assertIn("exit 1", chk["run"])
            self.assertIn("echo", chk["run"].split("if ")[0])

    def test_image_contract_step(self):
        ss = steps("image")
        names = [s.get("name", "") for s in ss]
        i = [k for k, n in enumerate(names) if n.startswith("Image contract")]
        self.assertEqual(len(i), 1)
        build = [k for k, s in enumerate(ss) if s.get("uses", "").startswith("docker/build-push-action@")][0]
        smoke = names.index("Boot and smoke test")
        self.assertTrue(build < i[0] < smoke)
        run = ss[i[0]]["run"]
        self.assertIn("ci-image:local", run)
        self.assertIn("Config.User", run)
        self.assertIn('0:*', run)
        self.assertIn("root", run)
        self.assertIn('\'{"80/tcp":{}}\'', run)
        self.assertNotIn("if", ss[i[0]])  # every caller, not fixture-only

    def test_fixture_check_is_workspace_ci_only(self):
        ss = steps("image")
        fx = [s for s in ss if s.get("name") == "Fixture page check"]
        self.assertEqual(len(fx), 1)
        self.assertEqual(fx[0]["if"], "github.repository == 'brandtstack/workspace-ci'")
        self.assertIn("fixture-check.sh", fx[0]["run"])
        self.assertIn("http://127.0.0.1:8080", fx[0]["run"])
        self.assertNotIn("${{", fx[0]["run"])
        names = [s.get("name", "") for s in ss]
        self.assertTrue(names.index("Boot and smoke test") < names.index("Fixture page check") < names.index("Tear down"))


if __name__ == "__main__":
    unittest.main()
