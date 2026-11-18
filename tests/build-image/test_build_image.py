#!/usr/bin/env python3
"""Tests for scripts/build-image (the local podman build). Fake podman, npm, make and curl on PATH log their
argv; nothing is built. contract/read.sh runs for real (cached yq, as in tests/contract).
Run: python3 tests/build-image/test_build_image.py
"""
import base64
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SCRIPT = os.path.join(ROOT, "scripts", "build-image")
CASES = os.path.join(ROOT, "tests", "contract", "cases")
REAL_CURL = shutil.which("curl")

FAKE = """#!/usr/bin/env bash
# fake {name}: log argv (and cwd), behave like the happy path
printf '%s cwd=%s |%s\\n' "{name}" "$PWD" "$*" >> "$FAKE_LOG"
{body}
"""
BODIES = {
    "podman": """case "$1" in
  run) echo fakecontainerid123 ;;
  logs) echo "container log line" ;;
esac
[ -n "${FAKE_PODMAN_BUILD_FAIL:-}" ] && [ "$1" = build ] && exit 1
exit 0""",
    "npm": "exit ${FAKE_NPM_EXIT:-0}",
    "make": "exit 0",
    # read.sh downloads yq with curl when the cache is cold: pass anything that is not the smoke probe through.
    "curl": """case "$*" in *127.0.0.1*) [ -n "${FAKE_CURL_FAIL:-}" ] && exit 22; exit 0 ;; esac
exec %s "$@\"""" % REAL_CURL,
}


class BuildImage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = os.path.join(self.tmp, "bin")
        os.mkdir(self.bin)
        self.log = os.path.join(self.tmp, "log")
        open(self.log, "w").close()
        for name, body in BODIES.items():
            p = os.path.join(self.bin, name)
            with open(p, "w") as f:
                f.write(FAKE.format(name=name, body=body))
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)

    def repo(self, case, name="demo-App"):
        d = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(d, ".github"))
        shutil.copy(os.path.join(CASES, case + ".yml"), os.path.join(d, ".github", "build.yml"))
        return d

    def run_bi(self, *args, env=None, cwd=None):
        e = dict(os.environ)
        e.pop("NPM_TOKEN", None)
        e["PATH"] = self.bin + os.pathsep + e["PATH"]
        e["FAKE_LOG"] = self.log
        e["BUILD_IMAGE_SMOKE_TIMEOUT"] = "2"
        e.update(env or {})
        return subprocess.run([SCRIPT, *args], env=e, cwd=cwd, capture_output=True, text=True)

    def lines(self):
        with open(self.log) as f:
            return f.read().splitlines()

    def calls(self, tool, sub=None):
        out = [l for l in self.lines() if l.startswith(tool + " ")]
        if sub:
            out = [l for l in out if l.split("|", 1)[1].split(" ")[0] == sub]
        return out

    def argv(self, line):
        return line.split("|", 1)[1]

    def test_verify_runs_before_build(self):
        d = self.repo("ok-spa-min")
        r = self.run_bi(d)
        self.assertEqual(r.returncode, 0, r.stderr)
        tools = [l.split(" ")[0] + ":" + l.split("|", 1)[1].split(" ")[0] for l in self.lines()]
        self.assertLess(tools.index("npm:run"), tools.index("podman:build"))
        self.assertIn("cwd=" + os.path.realpath(d), self.calls("npm")[0])
        self.assertEqual(self.argv(self.calls("npm")[0]), "run verify")

    def test_no_verify_skips_it(self):
        r = self.run_bi("--no-verify", self.repo("ok-spa-min"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls("npm"), [])
        self.assertEqual(self.calls("make"), [])
        self.assertEqual(len(self.calls("podman", "build")), 1)

    def test_go_verifies_with_make(self):
        r = self.run_bi(self.repo("ok-go-subdir"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.argv(self.calls("make")[0]), "verify")
        self.assertEqual(self.calls("npm"), [])

    def test_verify_failure_stops_before_build(self):
        r = self.run_bi(self.repo("ok-spa-min"), env={"FAKE_NPM_EXIT": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.calls("podman"), [])

    def test_bad_contract_exits_1_before_podman(self):
        r = self.run_bi(self.repo("bad-unknown-key"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("build.yml", r.stderr)
        self.assertEqual(self.calls("podman"), [])
        self.assertEqual(self.calls("npm"), [])

    def test_missing_contract_exits_1(self):
        d = os.path.join(self.tmp, "empty")
        os.mkdir(d)
        r = self.run_bi(d)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.calls("podman"), [])

    def test_secret_only_with_private_registry(self):
        r = self.run_bi("--no-verify", self.repo("ok-spa-min"))
        self.assertNotIn("--secret", self.argv(self.calls("podman", "build")[0]))
        shutil.rmtree(os.path.join(self.tmp, "demo-App"))
        open(self.log, "w").close()
        r = self.run_bi("--no-verify", self.repo("ok-next-full"), env={"NPM_TOKEN": "x"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--secret id=npm_token,env=NPM_TOKEN", self.argv(self.calls("podman", "build")[0]))

    def test_private_registry_without_token_exits_1_quietly(self):
        r = self.run_bi("--no-verify", self.repo("ok-next-full"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("NPM_TOKEN", r.stderr)
        self.assertEqual(self.calls("podman"), [])

    def test_token_value_never_printed(self):
        r = self.run_bi("--no-verify", self.repo("ok-next-full"), env={"NPM_TOKEN": "s3cr3tvalue"})
        self.assertNotIn("s3cr3tvalue", r.stdout + r.stderr + "\n".join(self.lines()))

    def test_next_dotenvx_target_args_and_context(self):
        r = self.run_bi("--no-verify", self.repo("ok-next-full"), env={"NPM_TOKEN": "x"})
        b = self.argv(self.calls("podman", "build")[0])
        self.assertIn("-f " + os.path.join(ROOT, "dockerfiles", "next.Dockerfile"), b)
        self.assertIn("--target runtime-dotenvx", b)
        self.assertIn("--build-context ws=" + os.path.join(ROOT, "dockerfiles"), b)
        self.assertIn("--build-arg NODE=24", b)
        self.assertIn("--build-arg PRISMA=true", b)
        self.assertNotIn("GO=", b)
        self.assertTrue(b.rstrip().endswith(" " + os.path.realpath(os.path.join(self.tmp, "demo-App"))))
        want = base64.b64encode(
            b"NEXT_PUBLIC_SITE_URL=https://example.test\nNEXT_PUBLIC_FLAG=1").decode()
        self.assertIn("--build-arg PUBLIC_BUILD_ENV_B64=" + want, b)
        self.assertEqual(
            base64.b64decode(want).decode(),
            "NEXT_PUBLIC_SITE_URL=https://example.test\nNEXT_PUBLIC_FLAG=1")

    def test_spa_args_have_no_target_and_empty_b64(self):
        self.run_bi("--no-verify", self.repo("ok-spa-min"))
        b = self.argv(self.calls("podman", "build")[0])
        self.assertNotIn("--target", b)
        self.assertIn("--build-arg NODE=24", b)
        self.assertIn("--build-arg PUBLIC_BUILD_ENV_B64= ", b + " ")
        self.assertNotIn("PRISMA", b)
        self.assertIn("spa.Dockerfile", b)

    def test_next_without_dotenvx_is_runtime_plain(self):
        d = self.repo("ok-spa-min")
        with open(os.path.join(d, ".github", "build.yml"), "w") as f:
            f.write("family: next\nnode: '24'\n")
        self.run_bi("--no-verify", d)
        self.assertIn("--target runtime-plain", self.argv(self.calls("podman", "build")[0]))

    def test_go_args(self):
        self.run_bi("--no-verify", self.repo("ok-go-subdir"))
        b = self.argv(self.calls("podman", "build")[0])
        for a in ("GO=1.27", "GO_MAIN=./cmd/usage-server", "GO_DIR=server"):
            self.assertIn("--build-arg " + a, b)
        self.assertNotIn("NODE=", b)
        self.assertNotIn("PUBLIC_BUILD_ENV_B64", b)
        self.assertNotIn("--target", b)

    def test_custom_uses_repo_dockerfile_and_no_args(self):
        d = self.repo("ok-custom")
        r = self.run_bi("--no-verify", d)
        b = self.argv(self.calls("podman", "build")[0])
        self.assertIn("-f " + os.path.realpath(d) + "/deploy/Dockerfile", b)
        self.assertNotIn("--build-arg", b)
        self.assertNotIn("--target", b)
        self.assertIn("--build-context ws=", b)

    def test_tag_is_lowercased_repo_basename(self):
        self.run_bi("--no-verify", self.repo("ok-spa-min", name="Demo-APP"))
        self.assertIn("-t localhost/demo-app:dev", self.argv(self.calls("podman", "build")[0]))

    def test_tag_sanitizes_odd_dir_names(self):
        self.run_bi("--no-verify", "--no-smoke", self.repo("ok-spa-min", name="My App+v2"))
        b = self.argv(self.calls("podman", "build")[0])
        self.assertIn("-t localhost/my-app-v2:dev ", b)

    def test_unusable_dir_name_dies(self):
        r = self.run_bi("--no-verify", "--no-smoke", self.repo("ok-spa-min", name="+++"))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.calls("podman"), [])

    def test_smoke_run_flags(self):
        r = self.run_bi("--no-verify", self.repo("ok-next-full"), env={"NPM_TOKEN": "x"})
        self.assertEqual(r.returncode, 0, r.stderr)
        run = self.argv(self.calls("podman", "run")[0])
        self.assertIn("--sysctl net.ipv4.ip_unprivileged_port_start=0", run)
        self.assertIn("-p 127.0.0.1:8080:80", run)
        self.assertIn("--path /health --port 3000", run)
        self.assertTrue(run.rstrip().endswith("localhost/demo-app:dev"))
        self.assertIn("http://127.0.0.1:8080/healthz", self.argv(self.calls("curl")[0]))
        rm = self.calls("podman", "rm")
        self.assertEqual(len(rm), 1)
        self.assertIn("fakecontainerid123", rm[0])

    def test_port_override(self):
        self.run_bi("--no-verify", self.repo("ok-spa-min"), env={"BUILD_IMAGE_PORT": "9091"})
        self.assertIn("-p 127.0.0.1:9091:80", self.argv(self.calls("podman", "run")[0]))
        self.assertIn("127.0.0.1:9091/healthz", self.argv(self.calls("curl")[0]))

    def test_no_smoke(self):
        r = self.run_bi("--no-verify", "--no-smoke", self.repo("ok-spa-min"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls("podman", "run"), [])
        self.assertEqual(self.calls("curl"), [])

    def test_teardown_and_logs_on_smoke_failure(self):
        r = self.run_bi("--no-verify", self.repo("ok-spa-min"), env={"FAKE_CURL_FAIL": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(len(self.calls("podman", "logs")), 1)
        self.assertIn("container log line", r.stdout + r.stderr)
        self.assertEqual(len(self.calls("podman", "rm")), 1)

    def test_build_failure_exits_nonzero_without_smoke(self):
        r = self.run_bi("--no-verify", self.repo("ok-spa-min"), env={"FAKE_PODMAN_BUILD_FAIL": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.calls("podman", "run"), [])

    def test_never_localhost_never_docker(self):
        self.run_bi(self.repo("ok-next-full"), env={"NPM_TOKEN": "x"})
        for l in self.lines():
            self.assertNotIn("localhost:", l.replace("localhost/", ""))
            self.assertNotIn("http://localhost", l)
        with open(SCRIPT) as f:
            src = f.read()
        self.assertNotIn("docker", src.replace("dockerfile", "").replace("Dockerfile", "").replace("DOCKERFILE", "").lower())
        self.assertNotIn("http://localhost", src)

    def test_default_dir_is_cwd_and_symlink_works(self):
        d = self.repo("ok-spa-min")
        link = os.path.join(self.tmp, "link-bi")
        os.symlink(SCRIPT, link)
        e = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"], FAKE_LOG=self.log)
        r = subprocess.run([link, "--no-verify", "--no-smoke"], cwd=d, env=e, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("-f " + os.path.join(ROOT, "dockerfiles", "spa.Dockerfile"), self.argv(self.calls("podman", "build")[0]))

    def test_help_and_unknown_flag(self):
        r = self.run_bi("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Usage", r.stdout)
        r = self.run_bi("--bogus")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.calls("podman"), [])


if __name__ == "__main__":
    unittest.main()
