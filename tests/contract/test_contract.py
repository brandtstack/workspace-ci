#!/usr/bin/env python3
"""Tests for contract/read.sh (the .github/build.yml reader). Needs bash, jq, curl and network
for the one-time yq download (cached in a temp RUNNER_TOOL_CACHE). Run: python3 tests/contract/test_contract.py
"""
import base64
import glob
import os
import re
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
READ = os.path.join(ROOT, "contract", "read.sh")
CASES = os.path.join(HERE, "cases")
YQ_VERSION = "4.47.2"
REPO = "brandtstack/demo-repo"


def dockerfile(family):
    return os.path.join(ROOT, "dockerfiles", family + ".Dockerfile")


def base(**kw):
    out = {
        "family": "", "dockerfile": "", "node": "", "go": "", "prisma": "false",
        "dotenvx": "false", "integration": "false", "favicon": "", "build-args": "",
        "private-registry": "false", "coolify-app": "", "smoke-args": "",
        "go-main": "", "go-dir": "", "migrate": "false",
        "smoke-env-b64": "", "smoke-postgres": "", "smoke-random": "",
    }
    out.update(kw)
    return out


# What ok-smoke-keys.yml's smoke-env decodes to: K=V lines, each newline-terminated, values untouched.
SMOKE_ENV_TEXT = "GREETING=hello world\nTRUSTED=a=b c=d $HOME \"q\" 'r' #not-a-comment\nEMPTY=\n"
SMOKE_ENV_B64 = base64.b64encode(SMOKE_ENV_TEXT.encode()).decode()

EXPECTED = {
    "ok-next-full": base(
        family="next", dockerfile=dockerfile("next"), node="24", prisma="true",
        dotenvx="true", integration="true", migrate="true",
        favicon="public/favicon.svg\npublic/icon.svg",
        **{"build-args": "NEXT_PUBLIC_SITE_URL=https://example.test\nNEXT_PUBLIC_FLAG=1",
           "private-registry": "true",
           "coolify-app": "app0example0test\napp2example0test",
           "smoke-args": "--path /health --port 3000"}),
    "ok-spa-min": base(family="spa", dockerfile=dockerfile("spa"), node="24"),
    "ok-go-subdir": base(
        family="go", dockerfile=dockerfile("go"), go="1.27",
        **{"coolify-app": "usageapp0example0test", "go-main": "./cmd/usage-server", "go-dir": "server"}),
    "ok-multiline": base(
        family="next", dockerfile=dockerfile("next"), node="24",
        favicon="a.svg\nb.svg\nc.svg",
        **{"build-args": "VITE_A=1\nNEXT_PUBLIC_B=two words", "smoke-args": "line one\nline two\n"}),
    "ok-custom": base(family="custom", dockerfile="deploy/Dockerfile", node="24"),
    "ok-smoke-keys": base(
        family="custom", dockerfile="Dockerfile", go="1.27",
        **{"smoke-env-b64": SMOKE_ENV_B64, "smoke-postgres": "one two_2",
           "smoke-random": "FIXTURE_KEY:32 OTHER_KEY:64"}),
    "ok-favicon-empty": base(family="spa", dockerfile=dockerfile("spa"), node="24"),
}


def parse_output(text):
    """Parse GITHUB_OUTPUT format (key=value and key<<DELIM heredocs)."""
    out, lines, i = {}, text.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line:
            continue
        m = re.match(r"^([A-Za-z0-9_-]+)<<(.+)$", line)
        if m:
            key, delim, vals = m.group(1), m.group(2), []
            while lines[i] != delim:
                vals.append(lines[i])
                i += 1
            i += 1
            out[key] = "\n".join(vals)
        else:
            k, _, v = line.partition("=")
            out[k] = v
    return out


def run(path, tool_cache, extra_env=None, github_output=True):
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_OUTPUT", "GITHUB_REPOSITORY")}
    env["RUNNER_TOOL_CACHE"] = tool_cache
    env["GITHUB_REPOSITORY"] = REPO
    env.update(extra_env or {})
    out = None
    if github_output:
        fd, out = tempfile.mkstemp()
        os.close(fd)
        env["GITHUB_OUTPUT"] = out
    p = subprocess.run(["bash", READ, path], capture_output=True, text=True, env=env)
    written = ""
    if out:
        with open(out) as f:
            written = f.read()
        os.unlink(out)
    return p, written


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.cache, ignore_errors=True)

    def test_ok_cases(self):
        for name, want in EXPECTED.items():
            with self.subTest(case=name):
                p, written = run(os.path.join(CASES, name + ".yml"), self.cache)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(parse_output(written), want)

    def test_bad_cases(self):
        paths = sorted(glob.glob(os.path.join(CASES, "bad-*.yml")))
        self.assertGreaterEqual(len(paths), 30)
        for path in paths:
            with self.subTest(case=os.path.basename(path)):
                with open(path) as f:
                    expects = [m.group(1).strip() for m in
                               (re.match(r"^# expect: (.*)$", l) for l in f.read().split("\n")) if m]
                self.assertTrue(expects, "no # expect: line")
                p, written = run(path, self.cache)
                self.assertEqual(p.returncode, 1, p.stderr)
                self.assertEqual(written, "", "outputs written on failure")
                for e in expects:
                    self.assertIn(e, p.stderr)
                for line in p.stderr.strip().split("\n"):
                    self.assertTrue(line.startswith("::error::build.yml: "), line)

    def test_two_errors_two_lines(self):
        p, _ = run(os.path.join(CASES, "bad-two-errors.yml"), self.cache)
        self.assertGreaterEqual(len(p.stderr.strip().split("\n")), 2)

    def test_stdout_when_github_output_unset(self):
        p, _ = run(os.path.join(CASES, "ok-spa-min.yml"), self.cache, github_output=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(parse_output(p.stdout), EXPECTED["ok-spa-min"])

    def test_go_main_default_from_repo(self):
        d = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(d, ".github"))
            f = os.path.join(d, ".github", "build.yml")
            with open(f, "w") as fh:
                fh.write("family: go\n")
            p, written = run(f, self.cache)
            self.assertEqual(p.returncode, 0, p.stderr)
            o = parse_output(written)
            self.assertEqual((o["go-main"], o["go-dir"], o["go"], o["node"]), ("./cmd/demo-repo", ".", "1.27", ""))
            p, written = run(f, self.cache, {"GITHUB_REPOSITORY": ""})
            self.assertEqual(parse_output(written)["go-main"], "./cmd/" + os.path.basename(d))
        finally:
            shutil.rmtree(d)

    def test_file_problems(self):
        d = tempfile.mkdtemp()
        try:
            p, _ = run(os.path.join(d, "nope.yml"), self.cache)
            self.assertEqual(p.returncode, 1)
            self.assertIn("::error::build.yml: <file>:", p.stderr)
            for name, body in (("empty.yml", ""), ("comment.yml", "# only\n"), ("list.yml", "- a\n- b\n")):
                f = os.path.join(d, name)
                with open(f, "w") as fh:
                    fh.write(body)
                p, _ = run(f, self.cache)
                self.assertEqual(p.returncode, 1, name)
                self.assertRegex(p.stderr, r"::error::build\.yml: <(file|root)>:", name)
        finally:
            shutil.rmtree(d)

    def test_key_lists_agree(self):
        import json
        with open(os.path.join(ROOT, "contract", "schema.json")) as f:
            schema_keys = set(json.load(f)["properties"])
        with open(os.path.join(ROOT, "contract", "validate.jq")) as f:
            jq = f.read()
        block = jq.split("def keys_allowed:")[1].split("];")[0]
        allowed = set(re.findall(r'"([a-z-]+)"', block))
        with open(READ) as f:
            rd = f.read()
        block = rd.split("KEYS='[")[1].split("]'")[0]
        # smoke-env is emitted as smoke-env-b64 (base64 of the K=V lines); every other output has its key's name
        out_keys = set(k.replace("smoke-env-b64", "smoke-env") for k in re.findall(r'"([a-z0-9-]+)"', block))
        self.assertEqual(allowed, schema_keys)
        self.assertEqual(out_keys, schema_keys)

    def test_smoke_env_round_trips_byte_for_byte(self):
        p, written = run(os.path.join(CASES, "ok-smoke-keys.yml"), self.cache)
        self.assertEqual(p.returncode, 0, p.stderr)
        got = base64.b64decode(parse_output(written)["smoke-env-b64"]).decode()
        self.assertEqual(got, SMOKE_ENV_TEXT)

    def test_smoke_keys_default_to_empty(self):
        p, written = run(os.path.join(CASES, "ok-spa-min.yml"), self.cache)
        o = parse_output(written)
        self.assertEqual((o["smoke-env-b64"], o["smoke-postgres"], o["smoke-random"]), ("", "", ""))

    def test_every_fixture_has_a_valid_contract_and_a_matrix_row(self):
        fx = os.path.join(ROOT, "fixtures")
        names = sorted(n for n in os.listdir(fx) if os.path.isdir(os.path.join(fx, n)) and n != "bad-contract")
        for n in names:
            with self.subTest(fixture=n):
                p, _ = run(os.path.join(fx, n, ".github", "build.yml"), self.cache)
                self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(ROOT, ".github", "workflows", "tests.yml")) as f:
            text = f.read()
        m = re.search(r"family: \[([^\]]*)\]", text)
        self.assertTrue(m, "fixture matrix not found in tests.yml")
        self.assertEqual(sorted(x.strip() for x in m.group(1).split(",")), names)

    def test_docs_cover_the_smoke_keys_and_the_custom_family(self):
        with open(os.path.join(ROOT, "README.md")) as f:
            readme = f.read()
        with open(os.path.join(ROOT, "dockerfiles", "README.md")) as f:
            dock = f.read()
        app_contract = readme.split("## App contract", 1)[1].split("\n## ", 1)[0]
        for k in ("smoke-env", "smoke-postgres", "smoke-random"):
            self.assertIn("`%s`" % k, app_contract, "README App contract does not name " + k)
            self.assertIn("`%s`" % k, dock, "dockerfiles/README.md does not name " + k)
        self.assertRegex(dock, r"(?m)^## custom\b")
        for needle in ("postgresql://smoke:smoke@smoke-pg:5432/<name>?sslmode=disable", "--entrypoint", "PUBLIC_BUILD_ENV_B64",
                       "HEALTHCHECK", "wget"):
            self.assertIn(needle, dock.split("## custom", 1)[1], needle)

    def test_yq_checksum_mismatch(self):
        tc = tempfile.mkdtemp()
        try:
            yd = os.path.join(tc, "yq", YQ_VERSION)
            os.makedirs(yd)
            marker = os.path.join(tc, "ran")
            fake = os.path.join(yd, "yq")
            with open(fake, "w") as f:
                f.write('#!/bin/sh\ntouch "%s"\necho \'{"family":"spa"}\'\n' % marker)
            os.chmod(fake, 0o755)
            p, written = run(os.path.join(CASES, "ok-spa-min.yml"), tc)
            self.assertEqual(p.returncode, 1)
            self.assertIn("::error::", p.stderr)
            self.assertIn("checksum", p.stderr)
            self.assertIn("delete " + fake, p.stderr)
            self.assertFalse(os.path.exists(marker), "tampered yq was executed")
            self.assertEqual(written, "")
        finally:
            shutil.rmtree(tc)

    def test_yq_swapped_in_the_cache_after_the_check_is_not_executed(self):
        # A sha256sum shim replaces the shared cached file right after hashing: the check-then-exec window.
        run(os.path.join(CASES, "ok-spa-min.yml"), self.cache)
        tc = tempfile.mkdtemp()
        try:
            yd = os.path.join(tc, "cache", "yq", YQ_VERSION)
            os.makedirs(yd)
            cached = os.path.join(yd, "yq")
            shutil.copy(os.path.join(self.cache, "yq", YQ_VERSION, "yq"), cached)
            bindir, temp = os.path.join(tc, "bin"), os.path.join(tc, "runner-temp")
            os.makedirs(bindir)
            os.makedirs(temp)
            marker = os.path.join(tc, "ran")
            shim = os.path.join(bindir, "sha256sum")
            with open(shim, "w") as f:
                f.write("#!/bin/sh\n"
                        "python3 -c 'import hashlib,sys; p=sys.argv[1]; "
                        "print(hashlib.sha256(open(p,\"rb\").read()).hexdigest() + \"  \" + p)' \"$1\"\n"
                        "rm -f '%s'\nprintf '#!/bin/sh\\ntouch %s\\n' > '%s'\nchmod +x '%s'\n"
                        % (cached, marker, cached, cached))
            os.chmod(shim, 0o755)
            p, written = run(os.path.join(CASES, "ok-spa-min.yml"), os.path.join(tc, "cache"),
                             {"PATH": bindir + os.pathsep + os.environ["PATH"], "RUNNER_TEMP": temp})
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertFalse(os.path.exists(marker), "the swapped cache file was executed")
            self.assertEqual(parse_output(written), EXPECTED["ok-spa-min"])
            self.assertEqual(os.listdir(temp), [], "the private yq copy must be removed")
        finally:
            shutil.rmtree(tc)

    def test_yq_mismatch_leaves_no_copy_in_runner_temp(self):
        tc = tempfile.mkdtemp()
        try:
            yd, temp = os.path.join(tc, "yq", YQ_VERSION), os.path.join(tc, "runner-temp")
            os.makedirs(yd)
            os.makedirs(temp)
            with open(os.path.join(yd, "yq"), "w") as f:
                f.write("#!/bin/sh\nexit 0\n")
            p, _ = run(os.path.join(CASES, "ok-spa-min.yml"), tc, {"RUNNER_TEMP": temp})
            self.assertEqual(p.returncode, 1)
            self.assertEqual(os.listdir(temp), [])
        finally:
            shutil.rmtree(tc)

    def test_duplicate_key_error_stays_on_one_line(self):
        d = tempfile.mkdtemp()
        try:
            f = os.path.join(d, "build.yml")
            with open(f, "w") as fh:
                fh.write('family: spa\nnode: "24"\n"a\\n::warning::x": 1\n"a\\n::warning::x": 2\n')
            p, written = run(f, self.cache)
            self.assertEqual(p.returncode, 1, p.stderr)
            self.assertEqual(written, "")
            self.assertNotIn("\n::warning::", p.stderr)
            self.assertIn("duplicate top-level key", p.stderr)
        finally:
            shutil.rmtree(d)

    def test_error_lines_escape_percent_encoded_line_breaks(self):
        # The runner decodes %0A and %0D in ::error:: messages, so a key name must not carry them through.
        d = tempfile.mkdtemp()
        try:
            for name, body in (("dup.yml", 'family: spa\nnode: "24"\n"a%0A::warning::x": 1\n"a%0A::warning::x": 2\n'),
                               ("unknown.yml", 'family: spa\nnode: "24"\n"a%0D%0Ab": 1\n')):
                f = os.path.join(d, name)
                with open(f, "w") as fh:
                    fh.write(body)
                p, written = run(f, self.cache)
                self.assertEqual(p.returncode, 1, p.stderr)
                self.assertEqual(written, "")
                self.assertNotRegex(p.stderr, r"%0[AD]", name)
                self.assertIn("%25", p.stderr, name)
        finally:
            shutil.rmtree(d)

    def test_the_two_log_sanitisers_are_identical(self):
        with open(READ) as f:
            here = re.search(r"^JQ_SAFE='(.*)'$", f.read(), re.M).group(1)
        with open(os.path.join(ROOT, ".github", "workflows", "release.yml")) as f:
            there = re.search(r"^\s*JQ_SAFE: '(.*)'$", f.read(), re.M).group(1)
        self.assertEqual(here, there)

    def test_keys_equal_as_final_text_are_duplicates(self):
        # A plain TRUE is a boolean to yq and a quoted "TRUE" a string, but both become the JSON key TRUE.
        d = tempfile.mkdtemp()
        try:
            f = os.path.join(d, "build.yml")
            for word in ("TRUE", "FALSE", "NULL"):
                for a, b in ((word, '"%s"' % word), ('"%s"' % word, word)):
                    with self.subTest(first=a, second=b):
                        with open(f, "w") as fh:
                            fh.write('family: custom\ndockerfile: Dockerfile\ngo: "1.27"\n'
                                     'smoke-env:\n  %s: first\n  %s: second\n' % (a, b))
                        p, written = run(f, self.cache)
                        self.assertEqual(p.returncode, 1, p.stderr)
                        self.assertEqual(written, "")
                        self.assertIn("smoke-env.%s: duplicate key" % word, p.stderr)
        finally:
            shutil.rmtree(d)

    def test_a_non_scalar_key_is_refused_cleanly(self):
        d = tempfile.mkdtemp()
        try:
            f = os.path.join(d, "build.yml")
            with open(f, "w") as fh:
                fh.write('family: spa\nnode: "24"\n? {a: 1}\n: v\n')
            p, written = run(f, self.cache)
            self.assertEqual(p.returncode, 1, p.stderr)
            self.assertEqual(written, "")
            for line in p.stderr.strip().split("\n"):
                self.assertTrue(line.startswith("::error::build.yml: "), line)
        finally:
            shutil.rmtree(d)

    def test_a_long_key_is_capped_in_the_error_line(self):
        d = tempfile.mkdtemp()
        try:
            f = os.path.join(d, "build.yml")
            with open(f, "w") as fh:
                fh.write('family: spa\nnode: "24"\n%s: 1\n' % ("k" * 2000))
            p, _ = run(f, self.cache)
            self.assertEqual(p.returncode, 1, p.stderr)
            self.assertNotIn("k" * 301, p.stderr)
        finally:
            shutil.rmtree(d)

    def test_validation_errors_cannot_carry_runner_commands(self):
        d = tempfile.mkdtemp()
        try:
            for name, body in (("key-newline.yml", 'family: spa\nnode: "24"\n"a\\n::warning::x": 1\n'),
                               ("key-command.yml", 'family: spa\nnode: "24"\n"x ##[add-mask]tok": 1\n'),
                               ("value-newline.yml", 'family: spa\nnode: "24"\ncoolify-app: "a\\n::warning::x"\n'),
                               ("value-command.yml", 'family: spa\nnode: "24"\ncoolify-app: "x ##[add-mask]tok"\n')):
                f = os.path.join(d, name)
                with open(f, "w") as fh:
                    fh.write(body)
                p, written = run(f, self.cache)
                self.assertEqual(p.returncode, 1, p.stderr)
                self.assertEqual(written, "")
                self.assertNotIn("##[", p.stderr, name)
                for line in p.stderr.strip().split("\n"):
                    self.assertTrue(line.startswith("::error::build.yml: "), line)
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
