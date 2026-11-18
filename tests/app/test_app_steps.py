#!/usr/bin/env python3
"""Runs app.yml's smoke-support steps for real, under `bash -eo pipefail` like the runner, with a fake `docker` on PATH.
Covers: "Prepare the smoke env file" (real files), "Start the smoke Postgres" and "Custom go image HEALTHCHECK" (the
fake docker answers `image inspect` / `inspect` / `exec` from a script and records every call).
Run: python3 tests/app/test_app_steps.py   (needs PyYAML, bash, base64)
"""
import base64
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(HERE, "..", "..", ".github", "workflows", "app.yml")
with open(WORKFLOW) as f:
    IMAGE_STEPS = {s["name"]: s for s in yaml.safe_load(f)["jobs"]["image"]["steps"] if "name" in s}

# Fake docker: logs argv; behaviour comes from files in $FAKE (HC_TEST: `image inspect` output; HEALTH: successive
# `inspect` statuses; PG_READY_AFTER: pg_isready fails that many times first).
FAKE_DOCKER = r"""#!/usr/bin/env bash
echo "$*" >> "$FAKE/calls"
case "$1 $2" in
  "image inspect") cat "$FAKE/hc_test" 2>/dev/null; exit 0 ;;
esac
case "$1" in
  inspect)
    case "$*" in
      *json*) echo '{"Status":"starting"}' ;;
      *) n=$(cat "$FAKE/polls" 2>/dev/null || echo 0); echo $((n+1)) > "$FAKE/polls"
         sed -n "$((n+1))p;" "$FAKE/health" | grep . || tail -n1 "$FAKE/health" ;;
    esac ;;
  exec)
    case "$*" in
      *pg_isready*) n=$(cat "$FAKE/pgpolls" 2>/dev/null || echo 0); echo $((n+1)) > "$FAKE/pgpolls"
                    [ "$n" -ge "$(cat "$FAKE/pg_after" 2>/dev/null || echo 0)" ]; exit $? ;;
    esac ;;
esac
exit 0
"""


def run_step(name, env=None, fake=None):
    d = tempfile.mkdtemp()
    try:
        bin_ = os.path.join(d, "bin")
        fk = os.path.join(d, "fake")
        tmp = os.path.join(d, "runner")
        for p in (bin_, fk, tmp):
            os.makedirs(p)
        dk = os.path.join(bin_, "docker")
        with open(dk, "w") as f:
            f.write(FAKE_DOCKER)
        os.chmod(dk, 0o755)
        # sleep is a no-op, so the 30 s waits do not run for 30 s
        sl = os.path.join(bin_, "sleep")
        with open(sl, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(sl, 0o755)
        for k, v in (fake or {}).items():
            with open(os.path.join(fk, k), "w") as f:
                f.write(v)
        e = {"PATH": bin_ + os.pathsep + os.environ["PATH"], "FAKE": fk, "RUNNER_TEMP": tmp, "HOME": d}
        e.update(env or {})
        p = subprocess.run(["bash", "-eo", "pipefail", "-c", IMAGE_STEPS[name]["run"]],
                           capture_output=True, text=True, env=e)
        calls, content = [], None
        if os.path.exists(os.path.join(fk, "calls")):
            with open(os.path.join(fk, "calls")) as f:
                calls = f.read().splitlines()
        envfile = os.path.join(tmp, "smoke.env")
        if os.path.exists(envfile):
            with open(envfile, "rb") as f:
                content = f.read()
        mode = stat.S_IMODE(os.stat(envfile).st_mode) if content is not None else None
        return p, calls, content, mode
    finally:
        shutil.rmtree(d)


class SmokeEnvFile(unittest.TestCase):
    N = "Prepare the smoke env file"

    def test_env_and_random(self):
        text = "GREETING=hello world\nQ=a=b $x \"y\" 'z'\n"
        p, _, content, mode = run_step(self.N, {"SMOKE_ENV_B64": base64.b64encode(text.encode()).decode(),
                                                "SMOKE_RANDOM": "KEY_A:32 KEY_B:16"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(mode, 0o600)
        lines = content.decode().split("\n")
        self.assertEqual(lines[:2], ["GREETING=hello world", "Q=a=b $x \"y\" 'z'"])
        keys = dict(l.split("=", 1) for l in lines[2:] if l)
        self.assertEqual(set(keys), {"KEY_A", "KEY_B"})
        self.assertEqual(len(base64.b64decode(keys["KEY_A"])), 32)
        self.assertEqual(len(base64.b64decode(keys["KEY_B"])), 16)
        # Nothing of the file reaches the log.
        for v in ("hello world", keys["KEY_A"], keys["KEY_B"]):
            self.assertNotIn(v, p.stdout + p.stderr)

    def test_random_differs_per_run(self):
        a = run_step(self.N, {"SMOKE_ENV_B64": "", "SMOKE_RANDOM": "K:32"})[2]
        b = run_step(self.N, {"SMOKE_ENV_B64": "", "SMOKE_RANDOM": "K:32"})[2]
        self.assertNotEqual(a, b)

    def test_empty_inputs_make_an_empty_file(self):
        p, _, content, mode = run_step(self.N, {"SMOKE_ENV_B64": "", "SMOKE_RANDOM": ""})
        self.assertEqual((p.returncode, content, mode), (0, b"", 0o600), p.stderr)


class SmokePostgres(unittest.TestCase):
    N = "Start the smoke Postgres"

    def test_creates_each_database(self):
        p, calls, _, _ = run_step(self.N, {"SMOKE_PG": "one two_2"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(calls[0], "network create smoke-net")
        self.assertEqual(calls[1], "run -d --name smoke-pg --network smoke-net -e POSTGRES_USER=smoke -e POSTGRES_PASSWORD=smoke postgres:16.15-alpine")
        self.assertIn("exec smoke-pg createdb -U smoke one", calls)
        self.assertIn("exec smoke-pg createdb -U smoke two_2", calls)

    def test_waits_for_ready(self):
        p, calls, _, _ = run_step(self.N, {"SMOKE_PG": "one"}, fake={"pg_after": "3"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len([c for c in calls if "pg_isready" in c]), 4)

    def test_never_ready_fails_after_30_tries_and_creates_nothing(self):
        p, calls, _, _ = run_step(self.N, {"SMOKE_PG": "one"}, fake={"pg_after": "999"})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("not ready after 30 s", p.stdout)
        self.assertEqual(len([c for c in calls if "pg_isready" in c]), 30)
        self.assertFalse([c for c in calls if "createdb" in c])


class CustomGoHealthcheck(unittest.TestCase):
    N = "Custom go image HEALTHCHECK"

    def test_no_healthcheck_fails_with_the_message(self):
        for hc in ("", "null\n", "[]\n", '["NONE"]\n'):
            with self.subTest(test=hc):
                p, calls, _, _ = run_step(self.N, fake={"hc_test": hc, "health": "healthy\n"})
                self.assertNotEqual(p.returncode, 0)
                self.assertIn("custom go image has no HEALTHCHECK", p.stdout)
                self.assertFalse([c for c in calls if c.startswith("inspect")], "must fail before polling the container")

    def test_healthy_after_a_few_polls_passes(self):
        p, calls, _, _ = run_step(self.N, fake={"hc_test": '["CMD","/fixture","healthcheck"]\n',
                                                "health": "starting\nstarting\nhealthy\n"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("image HEALTHCHECK healthy", p.stdout)
        self.assertFalse([c for c in calls if c.startswith("exec")])

    def test_never_healthy_fails_and_prints_the_health_log(self):
        p, calls, _, _ = run_step(self.N, fake={"hc_test": '["CMD","/x"]\n', "health": "unhealthy\n"})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("want healthy", p.stdout)
        self.assertTrue([c for c in calls if c.startswith("inspect") and "json" in c])
        self.assertTrue([c for c in calls if c.startswith("logs smoke")])


if __name__ == "__main__":
    unittest.main()
