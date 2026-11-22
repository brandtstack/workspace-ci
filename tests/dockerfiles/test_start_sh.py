#!/usr/bin/env python3
"""Behaviour of dockerfiles/next/start.sh (the CMD of the shared next image), with `node` stubbed on PATH.
The stub records each call's argv and environment, so the tests see what migrate and the server each got.
Run: python3 tests/dockerfiles/test_start_sh.py
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
START = os.path.join(ROOT, "dockerfiles", "next", "start.sh")

# Prints canned Prisma output (STUB_OUT) and exits STUB_RC for `migrate deploy`; for the server it just records.
STUB = r"""#!/bin/sh
log="$(dirname "$0")/../calls.jsonl"
python3 -c 'import json,os,sys; print(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ)}))' "$@" >> "$log"
case "$*" in
  *"migrate deploy"*) [ -f "$(dirname "$0")/../out" ] && cat "$(dirname "$0")/../out"; exit "$(cat "$(dirname "$0")/../rc" 2>/dev/null || echo 0)" ;;
  *) echo "SERVER $*"; exit 0 ;;
esac
"""

OK_OUT = """Loaded Prisma config from prisma.config.ts.

Prisma schema loaded from prisma/schema.prisma.
Datasource "db": PostgreSQL database "insta", schema "public" at "10.0.0.5:5432"

2 migrations found in prisma/migrations

Applying migration `20261118000000_init`

The following migration(s) have been applied:

migrations/
  └─ 20261118000000_init/
    └─ migration.sql

All migrations have been successfully applied.
"""

FAIL_OUT = """Datasource "db": PostgreSQL database "insta", schema "public" at "10.0.0.5:5432"
Error: P3009

migrate found failed migrations in the target database, new migrations will not be applied.
The `20261118000000_init` migration started at 2026-11-18 failed
connection postgresql://app:hunter2@10.0.0.5:5432/insta
"""


class StartSh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = os.path.join(self.tmp, "bin")
        os.mkdir(self.bin)
        with open(os.path.join(self.bin, "node"), "w") as f:
            f.write(STUB)
        os.chmod(os.path.join(self.bin, "node"), 0o755)
        self.migrate = os.path.join(self.tmp, "migrate")
        os.mkdir(self.migrate)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def with_config(self):
        open(os.path.join(self.migrate, "prisma.config.ts"), "w").close()

    def run_start(self, env=None, out="", rc=0):
        with open(os.path.join(self.tmp, "out"), "w") as f:
            f.write(out)
        with open(os.path.join(self.tmp, "rc"), "w") as f:
            f.write(str(rc))
        e = {"PATH": self.bin + ":/usr/bin:/bin", "HOME": self.tmp,
             "START_MIGRATE_DIR": self.migrate, "START_SERVER": "/app/server.js"}
        e.update(env or {})
        return subprocess.run([os.environ.get("START_SH", "/bin/sh"), START], env=e, capture_output=True, text=True, timeout=30)

    def calls(self):
        p = os.path.join(self.tmp, "calls.jsonl")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(l) for l in f if l.strip()]

    def test_no_migrate_dir_only_starts_the_server(self):
        r = self.run_start({"DATABASE_URL": "postgresql://a"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c["argv"] for c in self.calls()], [["/app/server.js"]])
        self.assertNotIn("[start]", r.stdout)

    def reset_calls(self):
        p = os.path.join(self.tmp, "calls.jsonl")
        if os.path.exists(p):
            os.remove(p)

    def test_skip_migrations(self):
        self.with_config()
        for value in ("1", "true"):
            with self.subTest(value=value):
                self.reset_calls()
                r = self.run_start({"SKIP_MIGRATIONS": value})
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("[start] SKIP_MIGRATIONS set", r.stdout)
                self.assertEqual([c["argv"] for c in self.calls()], [["/app/server.js"]])

    def test_empty_or_unset_skip_migrations_migrates(self):
        self.with_config()
        for env in ({}, {"SKIP_MIGRATIONS": ""}):
            with self.subTest(env=env):
                self.reset_calls()
                r = self.run_start({"DATABASE_URL": "postgresql://runtime", **env},
                                   out="No pending migrations to apply.\n")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("SKIP_MIGRATIONS set", r.stdout)
                self.assertEqual([c["argv"][-2:] for c in self.calls()][0], ["migrate", "deploy"])
                self.assertEqual(self.calls()[-1]["argv"], ["/app/server.js"])

    def test_any_other_skip_migrations_value_refuses_to_start(self):
        self.with_config()
        for value in ("false", "0", "no", "TRUE", "True", "yes", " 1", "1 ", "true\n", "canary-value"):
            with self.subTest(value=value):
                self.reset_calls()
                r = self.run_start({"DATABASE_URL": "postgresql://runtime", "SKIP_MIGRATIONS": value})
                self.assertEqual(r.returncode, 1)
                self.assertEqual(self.calls(), [], "neither migrate nor the server may run")
                self.assertIn("SKIP_MIGRATIONS must be 1, true or empty: refusing to start the server", r.stderr)
                self.assertNotIn("canary-value", r.stdout + r.stderr)

    def test_skip_migrations_is_ignored_without_migrate(self):
        r = self.run_start({"SKIP_MIGRATIONS": "false"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c["argv"] for c in self.calls()], [["/app/server.js"]])

    def test_migrate_then_server_with_minimal_env(self):
        self.with_config()
        r = self.run_start({"DATABASE_URL": "postgresql://runtime", "MIGRATE_DATABASE_URL": "postgresql://owner",
                            "APP_SECRET": "s3cr3t"}, out=OK_OUT)
        self.assertEqual(r.returncode, 0, r.stderr)
        mig, srv = self.calls()
        self.assertEqual(mig["argv"], ["node_modules/prisma/build/index.js", "migrate", "deploy"])
        self.assertEqual(mig["env"]["DATABASE_URL"], "postgresql://owner")
        self.assertNotIn("APP_SECRET", mig["env"])
        self.assertNotIn("MIGRATE_DATABASE_URL", mig["env"])
        self.assertEqual(mig["env"]["CHECKPOINT_DISABLE"], "1")
        self.assertEqual(srv["argv"], ["/app/server.js"])
        self.assertNotIn("MIGRATE_DATABASE_URL", srv["env"])
        self.assertEqual(srv["env"]["APP_SECRET"], "s3cr3t")
        self.assertEqual(srv["env"]["DATABASE_URL"], "postgresql://runtime")
        # Only allow-listed Prisma lines reach the log: no datasource banner.
        self.assertIn("[start] prisma: Applying migration `20261118000000_init`", r.stdout)
        self.assertIn("[start] prisma: All migrations have been successfully applied.", r.stdout)
        self.assertIn("[start] prisma: 2 migrations found in prisma/migrations", r.stdout)
        self.assertNotIn("10.0.0.5", r.stdout + r.stderr)
        self.assertIn("[start] migrations applied", r.stdout)

    def test_runtime_url_used_when_no_migrate_url(self):
        self.with_config()
        self.run_start({"DATABASE_URL": "postgresql://runtime"}, out="No pending migrations to apply.\n")
        self.assertEqual(self.calls()[0]["env"]["DATABASE_URL"], "postgresql://runtime")

    def test_failed_migration_refuses_to_start_and_prints_only_the_code(self):
        self.with_config()
        r = self.run_start({"DATABASE_URL": "postgresql://app:hunter2@10.0.0.5:5432/insta"}, out=FAIL_OUT, rc=1)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len(self.calls()), 1, "the server must not be started")
        self.assertIn("migration failed (exit 1, Prisma P3009): refusing to start the server", r.stderr)
        self.assertNotIn("hunter2", r.stdout + r.stderr)
        self.assertNotIn("10.0.0.5", r.stdout + r.stderr)


class DockerfileWiring(unittest.TestCase):
    """CI cannot run the dotenvx wrapper (no private key), so pin its wiring statically: the wrapper is the
    ENTRYPOINT and start.sh the CMD, so production runs `dotenvx run ... -- /bin/sh /app/start.sh`."""

    def stage(self, name):
        with open(os.path.join(ROOT, "dockerfiles", "next.Dockerfile")) as f:
            text = f.read()
        body = text.split(" AS " + name + "\n", 1)[1]
        return body.split("\nFROM ", 1)[0]

    def test_both_runtimes_cmd_is_start_sh(self):
        for name in ("runtime-plain", "runtime-dotenvx"):
            self.assertIn('CMD ["/bin/sh", "/app/start.sh"]', self.stage(name), name)

    def test_dotenvx_entrypoint_is_the_wrapper(self):
        self.assertIn('ENTRYPOINT ["/bin/sh", "./dotenvx-entry.sh"]', self.stage("runtime-dotenvx"))
        with open(os.path.join(ROOT, "dockerfiles", "next", "dotenvx-entry.sh")) as f:
            self.assertIn('-- "$@"', f.read())

    def test_migrate_true_copies_only_schema_and_migrations(self):
        stage = self.stage("migrate-true")
        # No COPY of the schema any more: a RUN with a bind mount picks schema.prisma or schema/ (multi-file).
        copies = [l for l in stage.splitlines() if l.startswith("COPY ") and "prisma" in l]
        self.assertEqual(copies, ["COPY prisma.config.ts /opt/migrate/prisma.config.ts"])
        self.assertIn("RUN --mount=type=bind,source=prisma,target=/src/prisma", stage)
        self.assertIn("prisma: need prisma/schema.prisma or prisma/schema/", stage)
        self.assertNotIn("npm_token", stage)


class MigratePrismaCopy(unittest.TestCase):
    """Runs the migrate-true stage's schema-copy script for real, with /src/prisma and /opt/migrate pointed at
    temp dirs (the bind mount is BuildKit's; the script is plain sh)."""

    @classmethod
    def script(cls):
        with open(os.path.join(ROOT, "dockerfiles", "next.Dockerfile")) as f:
            body = f.read().split(" AS migrate-true\n", 1)[1].split("\nFROM ", 1)[0]
        m = re.search(r"^RUN --mount=type=bind,source=prisma,target=/src/prisma ((?:.*\\\n)*.*)$", body, re.M)
        assert m, "bind-mount RUN not found"
        return m.group(1).replace("\\\n", "\n")

    def run_copy(self, files):
        d = tempfile.mkdtemp()
        try:
            src, dst = os.path.join(d, "src", "prisma"), os.path.join(d, "opt", "migrate")
            os.makedirs(src)
            os.makedirs(dst)
            for rel in files:
                path = os.path.join(src, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write("x")
            script = self.script().replace("/src/prisma", src).replace("/opt/migrate", dst)
            r = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
            got = sorted(os.path.relpath(os.path.join(root, n), dst)
                         for root, _, names in os.walk(dst) for n in names)
            return r, got
        finally:
            shutil.rmtree(d)

    def test_single_file_schema(self):
        r, got = self.run_copy(["schema.prisma", "migrations/1_init/migration.sql", "seed.ts", ".env"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(got, ["prisma/migrations/1_init/migration.sql", "prisma/schema.prisma"])

    def test_multi_file_schema_dir(self):
        r, got = self.run_copy(["schema/a.prisma", "schema/b.prisma", "migrations/1_init/migration.sql", "dump.sql"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(got, ["prisma/migrations/1_init/migration.sql", "prisma/schema/a.prisma", "prisma/schema/b.prisma"])

    def test_multi_file_copies_only_prisma_files_keeping_subfolders(self):
        r, got = self.run_copy(["schema/a.prisma", "schema/sub/x.prisma", "schema/.env", "schema/seed.sql",
                                "schema/sub/notes.txt", "migrations/1_init/migration.sql"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(got, ["prisma/migrations/1_init/migration.sql", "prisma/schema/a.prisma", "prisma/schema/sub/x.prisma"])

    def test_multi_file_dir_without_prisma_files_fails(self):
        r, got = self.run_copy(["schema/.env", "schema/seed.sql", "migrations/1_init/migration.sql"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("prisma/schema/ has no *.prisma files", r.stderr)
        self.assertEqual([g for g in got if "schema" in g], [])

    def test_neither_fails_with_the_message(self):
        r, got = self.run_copy(["migrations/1_init/migration.sql"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("prisma: need prisma/schema.prisma or prisma/schema/", r.stderr)
        self.assertEqual(got, [])

    def test_both_fail_with_the_message(self):
        r, _ = self.run_copy(["schema.prisma", "schema/a.prisma", "migrations/1_init/migration.sql"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("prisma: need prisma/schema.prisma or prisma/schema/", r.stderr)

    def test_missing_migrations_fails(self):
        r, _ = self.run_copy(["schema.prisma"])
        self.assertNotEqual(r.returncode, 0)

if __name__ == "__main__":
    unittest.main(verbosity=2)
