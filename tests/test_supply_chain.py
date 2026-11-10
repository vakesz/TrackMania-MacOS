from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    p
    for p in sorted((ROOT / "src").rglob("*"))
    if p.is_file()
    and "swift" not in p.relative_to(ROOT / "src").parts
    and p.suffix not in (".pyc", ".png", ".svg")
]
SHELL_CURL = re.compile(r"(?<![\w./-])curl\s+(?![\s]|-q\s)")
PYTHON_CURL = re.compile(r"""['"]curl['"]\s*,\s*(?!\s|['"]-q['"])""")


class CurlIgnoresUserConfig(unittest.TestCase):
    """curl reads ~/.curlrc unless -q is its first argument."""

    def test_every_curl_invocation_starts_with_q(self):
        invocations = 0
        offenders = []
        for path in SOURCES:
            try:
                lines = path.read_text().splitlines()
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(lines, 1):
                if line.lstrip().startswith("#"):
                    continue
                if path.suffix == ".py":
                    invocations += len(re.findall(r"""['"]curl['"]\s*,""", line))
                    bad = PYTHON_CURL.search(line)
                else:
                    invocations += len(re.findall(r"(?<![\w./-])curl\s+-", line))
                    bad = SHELL_CURL.search(line)
                if bad:
                    offenders.append(
                        "{}:{}: {}".format(path.relative_to(ROOT), number, line.strip())
                    )
        self.assertGreaterEqual(invocations, 3)
        self.assertEqual(offenders, [])


WORKFLOW = ROOT / ".github/workflows/release-hub.yml"


def release_jobs():
    """Split the release workflow into {job name: text}, comments removed."""
    lines = [
        re.sub(r"\s+#.*$", "", line)
        for line in WORKFLOW.read_text().splitlines()
        if not line.lstrip().startswith("#")
    ]
    jobs, name = {}, None
    in_jobs = False
    for line in lines:
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
        elif in_jobs and re.match(r"^\S", line):
            in_jobs = False
        match = re.match(r"^  ([\w-]+):\s*$", line) if in_jobs else None
        if match:
            name = match.group(1)
            jobs[name] = []
        elif in_jobs and name:
            jobs[name].append(line)
    return {key: "\n".join(value) for key, value in jobs.items()}


class ReleaseWorkflowSeparation(unittest.TestCase):
    """Build, sign and publish stay in separate jobs with separate privileges."""

    @classmethod
    def setUpClass(cls):
        cls.jobs = release_jobs()
        cls.text = "\n".join(cls.jobs.values())

    def test_three_jobs(self):
        self.assertEqual(set(self.jobs), {"build", "sign", "publish"})

    def test_only_publish_can_write(self):
        for name, body in self.jobs.items():
            with self.subTest(job=name):
                self.assertIn("permissions:", body)
                self.assertEqual("contents: write" in body, name == "publish")

    def test_build_has_no_secrets_or_environment(self):
        build = self.jobs["build"]
        self.assertNotIn("secrets.", build)
        self.assertNotIn("environment:", build)
        self.assertIn("package-app.sh build-unsigned", build)

    def test_sign_is_gated_and_builds_nothing(self):
        sign = self.jobs["sign"]
        self.assertIn("environment: release", sign)
        self.assertIn("package-app.sh sign", sign)
        for forbidden in (
            "swift ",
            "setup-uv",
            "uv python",
            "uv run",
            "uv sync",
            "make ",
            "build-unsigned",
            "package-app.sh appbundle",
            "package-app.sh zip",
            "package-app.sh all",
            "pip ",
            "brew ",
        ):
            self.assertNotIn(forbidden, sign)
        self.assertIn("needs.build.outputs.app_sha256", sign)

    def test_publish_runs_no_repository_code_and_never_clobbers(self):
        publish = self.jobs["publish"]
        self.assertIn("startsWith(github.ref, 'refs/tags/v')", publish)
        self.assertNotIn("actions/checkout", publish)
        self.assertNotIn("secrets.", publish)
        self.assertIn("gh release upload", publish)
        self.assertNotIn("--clobber", self.text)

    def test_actions_are_pinned_and_credentials_not_persisted(self):
        uses = re.findall(r"uses:\s*(\S+)", self.text)
        self.assertTrue(uses)
        for action in uses:
            self.assertRegex(action, r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
        self.assertEqual(
            self.text.count("persist-credentials: false"), self.text.count("actions/checkout@")
        )

    def test_no_event_payload_is_interpolated(self):
        self.assertNotIn("github.event.", self.text)


if __name__ == "__main__":
    unittest.main()
