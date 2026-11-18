#!/usr/bin/env python3
"""No shared Dockerfile runs a gate: lint, tests, typecheck and verify belong to `ci / verify`, not the image.
Static: reads dockerfiles/*.Dockerfile line by line (comments skipped). Run: python3 tests/dockerfiles/test_no_gate.py
"""
import glob
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
GATE = re.compile(
    r"npm\s+(?:run\s+)?(?:verify|test|lint|typecheck)\b|npm\s+t\b|\bvitest\b|\beslint\b|\btsc\b"
    r"|\bgo\s+(?:test|vet)\b|\bmake\s+verify\b"
)


class NoGate(unittest.TestCase):
    def test_dockerfiles_exist(self):
        names = sorted(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "dockerfiles", "*.Dockerfile")))
        self.assertEqual(names, ["go.Dockerfile", "next.Dockerfile", "spa.Dockerfile"])

    def test_no_gate_command_in_any_dockerfile(self):
        for path in glob.glob(os.path.join(ROOT, "dockerfiles", "*.Dockerfile")):
            with open(path) as f:
                for n, line in enumerate(f, 1):
                    if line.lstrip().startswith("#"):
                        continue
                    self.assertIsNone(GATE.search(line), "%s:%d runs a gate: %s" % (os.path.basename(path), n, line.strip()))

    def test_pattern_catches_what_it_names(self):
        for bad in ("RUN npm run verify", "RUN npm test", "RUN npm run lint", "RUN npm run typecheck", "RUN npx vitest run",
                    "RUN npx eslint .", "RUN npx tsc --noEmit", "RUN go test ./...", "RUN go vet ./...", "RUN make verify"):
            self.assertIsNotNone(GATE.search(bad), bad)
        for ok in ("RUN npm run build", "RUN npm ci", "RUN go build ./...", "RUN npx prisma generate"):
            self.assertIsNone(GATE.search(ok), ok)


if __name__ == "__main__":
    unittest.main()
