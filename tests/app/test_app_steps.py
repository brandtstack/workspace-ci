#!/usr/bin/env python3
"""Runs app.yml's smoke-support steps for real, under `bash -eo pipefail` like the runner, with a fake `docker` on PATH.
Covers: "Prepare the smoke env file" (real files), "Start the smoke Postgres" and "Custom go image HEALTHCHECK" (the
fake docker answers `image inspect` / `inspect` / `exec` from a script and records every call).
Run: python3 tests/app/test_app_steps.py   (needs PyYAML, bash, base64)
"""
import base64
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(HERE, "..", "..", ".github", "workflows", "app.yml")
WAIT = os.path.join(HERE, "..", "..", "scripts", "wait-healthy")
with open(WORKFLOW) as f:
    IMAGE_STEPS = {s["name"]: s for s in yaml.safe_load(f)["jobs"]["image"]["steps"] if "name" in s}

# Fake docker: logs argv; behaviour comes from files in $FAKE (HC_TEST: `image inspect` output; HEALTH: successive
# `inspect` statuses; PG_READY_AFTER: pg_isready fails that many times first).
FAKE_DOCKER = r"""#!/usr/bin/env bash
echo "$*" >> "$FAKE/calls"
case "$1 $2" in
  "image inspect")
    case "$*" in
      *Config.User*) cat "$FAKE/user" 2>/dev/null ;;
      *ExposedPorts*) cat "$FAKE/ports" 2>/dev/null ;;
      *) cat "$FAKE/hc_test" 2>/dev/null ;;
    esac
    exit 0 ;;
esac
case "$1" in
  inspect)
    case "$*" in
      *Config.Healthcheck*) cat "$FAKE/hc_dur" 2>/dev/null ;;
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
        cu = os.path.join(bin_, "curl")  # the boot step's reachability probe
        with open(cu, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(cu, 0o755)
        sl = os.path.join(bin_, "sleep")
        with open(sl, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(sl, 0o755)
        ws = os.path.join(tmp, "ws-ci", "scripts")
        os.makedirs(ws)
        shutil.copy(WAIT, os.path.join(ws, "wait-healthy"))
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


class ImageContract(unittest.TestCase):
    N = "Image contract (non-root user, exposed port 80)"
    PORT_80 = '{"80/tcp":{}}\n'
    PORT_8090 = '{"8090/tcp":{}}\n'

    def check(self, user, ports, skip="false", allow="false"):
        return run_step(self.N, {"SKIP_IMAGE_CHECKS": skip, "ALLOW_ROOT_IMAGE": allow},
                        fake={"user": user, "ports": ports})[0]

    def test_a_normal_image_passes(self):
        p = self.check("node\n", self.PORT_80)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_root_fails_with_skip_image_checks_alone(self):
        for user in ("", "0", "root", "0:0", "root:root"):
            with self.subTest(user=user):
                p = self.check(user + "\n", self.PORT_8090, skip="true")
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("image runs as root", p.stdout)

    def test_root_passes_only_with_allow_root_image(self):
        for user in ("", "0", "root", "0:0", "root:root"):
            with self.subTest(user=user):
                p = self.check(user + "\n", self.PORT_8090, skip="true", allow="true")
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("::warning::image runs as root", p.stdout)

    def test_skip_image_checks_turns_off_only_the_port_check(self):
        p = self.check("65532\n", self.PORT_8090, skip="true")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_wrong_port_fails_without_skip(self):
        p = self.check("node\n", self.PORT_8090)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("must expose exactly 80/tcp", p.stdout)

    def test_allow_root_alone_does_not_turn_off_the_port_check(self):
        p = self.check("root\n", self.PORT_8090, allow="true")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("must expose exactly 80/tcp", p.stdout)


class BootMigrations(unittest.TestCase):
    N = "Boot and smoke test"

    def boot(self, **kw):
        env = {"SMOKE_ARGS": "", "FAMILY": "next", "DOTENVX": "false", "MIGRATE": "false", "SMOKE_PG": ""}
        env.update(kw)
        p, calls, _, _ = run_step(self.N, env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return [c for c in calls if c.startswith("run -d --name smoke ")][0]

    def test_migrate_without_a_database_skips_migrations(self):
        self.assertIn("-e SKIP_MIGRATIONS=1", self.boot(MIGRATE="true"))

    def test_migrate_with_the_smoke_database_runs_them(self):
        run = self.boot(MIGRATE="true", SMOKE_PG="app")
        self.assertNotIn("SKIP_MIGRATIONS", run)
        self.assertIn("--network smoke-net", run)

    def test_no_migrate_adds_nothing(self):
        self.assertNotIn("SKIP_MIGRATIONS", self.boot(SMOKE_PG="app"))


class SmokePostgres(unittest.TestCase):
    N = "Start the smoke Postgres"

    def test_creates_each_database(self):
        p, calls, _, _ = run_step(self.N, {"SMOKE_PG": "one two_2"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(calls[0], "network create smoke-net")
        self.assertEqual(calls[1], "run -d --name smoke-pg --network smoke-net -e POSTGRES_USER=smoke -e POSTGRES_PASSWORD=smoke docker.io/library/postgres:16.15-alpine")
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

    def test_waits_with_the_budget_of_the_images_own_healthcheck(self):
        p, _, _, _ = run_step(self.N, fake={"hc_test": '["CMD","/x"]\n', "health": "healthy\n",
                                            "hc_dur": "10000000000 5000000000 3\n"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("budget 55 s", p.stdout)

    def test_never_healthy_fails_and_prints_the_health_log(self):
        p, calls, _, _ = run_step(self.N, fake={"hc_test": '["CMD","/x"]\n', "health": "unhealthy\n"})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("State.Health.Status is 'unhealthy' after 130 s, want healthy", p.stdout)
        self.assertTrue([c for c in calls if c.startswith("inspect") and "json" in c])
        self.assertTrue([c for c in calls if c.startswith("logs smoke")])


class WaitHealthy(unittest.TestCase):
    """scripts/wait-healthy <container>: the budget comes from the container's own Config.Healthcheck
    (inspect gives nanoseconds): StartPeriod + Interval * (Retries + 1) + 10 s, Docker's defaults for a zero field
    (interval 30 s, start period 0, retries 3), clamped to 30..300 s."""

    def wait(self, hc_dur, health="healthy\n"):
        d = tempfile.mkdtemp()
        try:
            bin_, fk = os.path.join(d, "bin"), os.path.join(d, "fake")
            os.makedirs(bin_)
            os.makedirs(fk)
            for name, body in (("docker", FAKE_DOCKER), ("sleep", "#!/bin/sh\nexit 0\n")):
                with open(os.path.join(bin_, name), "w") as f:
                    f.write(body)
                os.chmod(os.path.join(bin_, name), 0o755)
            for k, v in (("hc_dur", hc_dur), ("health", health)):
                with open(os.path.join(fk, k), "w") as f:
                    f.write(v)
            p = subprocess.run(["bash", WAIT, "smoke"], capture_output=True, text=True,
                               env={"PATH": bin_ + os.pathsep + os.environ["PATH"], "FAKE": fk, "HOME": d})
            with open(os.path.join(fk, "polls")) as f:
                polls = int(f.read()) if os.path.exists(os.path.join(fk, "polls")) else 0
            return p, polls
        finally:
            shutil.rmtree(d)

    def budget(self, hc_dur):
        p, _ = self.wait(hc_dur)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        m = re.search(r"budget (\d+) s", p.stdout)
        self.assertTrue(m, p.stdout)
        return int(m.group(1))

    def test_budget_arithmetic(self):
        S = 10**9
        for label, dur, want in (
            ("no healthcheck output: all defaults (0 + 30 * 4 + 10)", "", 130),
            ("all zero: defaults", "0 0 0", 130),
            ("explicit (5 + 10 * 4 + 10)", "%d %d 3" % (10 * S, 5 * S), 55),
            ("zero interval only: default 30 s (5 + 30 * 3 + 10)", "0 %d 2" % (5 * S), 105),
            ("zero retries only: default 3 (0 + 10 * 4 + 10)", "%d 0 0" % (10 * S), 50),
            ("clamped up to 30", "%d 0 1" % (1 * S), 30),
            ("clamped down to 300", "%d 0 3" % (120 * S), 300),
            ("just under the ceiling", "%d %d 3" % (60 * S, 40 * S), 290),
        ):
            with self.subTest(label):
                self.assertEqual(self.budget(dur + "\n"), want)

    def test_polls_exactly_the_budget_then_fails_with_the_text_and_dump(self):
        p, polls = self.wait("%d 0 1\n" % (1 * 10**9), health="starting\n")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(polls, 30)
        self.assertIn("::error::State.Health.Status is 'starting' after 30 s, want healthy", p.stdout)
        self.assertIn('{"Status":"starting"}', p.stdout)

    def test_returns_as_soon_as_healthy(self):
        p, polls = self.wait("", health="starting\nstarting\nhealthy\n")
        self.assertEqual((p.returncode, polls), (0, 3), p.stdout)


if __name__ == "__main__":
    unittest.main()
