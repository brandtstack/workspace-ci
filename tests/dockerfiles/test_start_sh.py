#!/usr/bin/env python3
"""Behaviour of dockerfiles/next/start.sh (the CMD of the shared next image), with `node` stubbed on PATH.
The stub records each call's argv and environment, so the tests see what migrate and the server each got.
Run: python3 tests/dockerfiles/test_start_sh.py
"""
import json
import os
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

    def test_skip_migrations(self):
        self.with_config()
        r = self.run_start({"SKIP_MIGRATIONS": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[start] SKIP_MIGRATIONS set", r.stdout)
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
        copies = [l for l in self.stage("migrate-true").splitlines() if l.startswith("COPY ") and "prisma" in l]
        self.assertEqual(sorted(copies), sorted([
            "COPY prisma/schema.prisma /opt/migrate/prisma/schema.prisma",
            "COPY prisma/migrations /opt/migrate/prisma/migrations",
            "COPY prisma.config.ts /opt/migrate/prisma.config.ts",
        ]))
        self.assertNotIn("npm_token", self.stage("migrate-true"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
