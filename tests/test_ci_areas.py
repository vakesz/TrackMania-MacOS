"""Which check.yml jobs a change runs (.github/scripts/ci-areas.sh)."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/ci-areas.sh"
JOBS = ("scripts", "swift", "c-helpers", "x87")


def areas(*paths: str) -> set[str]:
    result = subprocess.run(
        [str(SCRIPT)],
        input="".join(path + "\n" for path in paths),
        capture_output=True,
        text=True,
        check=True,
    )
    pairs = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert set(pairs) == set(JOBS), pairs
    return {job for job, value in pairs.items() if value == "true"}


class CiAreaTests(unittest.TestCase):
    def test_docs_website_and_github_forms_run_nothing(self):
        self.assertEqual(
            areas(
                "website/index.html",
                "docs/runtime.md",
                "docs/images/hub.png",
                "README.md",
                ".github/SECURITY.md",
                ".github/ISSUE_TEMPLATE/bug_report.yml",
                ".github/workflows/pages.yml",
            ),
            set(),
        )

    def test_no_changes_run_nothing(self):
        self.assertEqual(areas(), set())

    def test_swift_runs_only_the_swift_jobs(self):
        self.assertEqual(areas("src/swift/Sources/TrackManiaHub/HubApp.swift"), {"swift"})

    def test_python_and_shell_run_the_scripts_check(self):
        self.assertEqual(areas("src/python/trackmania_mac/hub/run.py"), {"scripts"})
        self.assertEqual(areas("src/shell/lib/commands/maintain.sh"), {"scripts"})
        self.assertEqual(areas("config/titles/tmu.json"), {"scripts"})

    def test_markdown_inside_code_directories_still_runs_the_check(self):
        self.assertEqual(areas("tests/fixtures/notes.md"), {"scripts"})

    def test_c_helpers_and_their_flag_sources(self):
        for path in (
            "src/c/session.c",
            "src/python/trackmania_mac/build/runtime_builder.py",
            "src/python/trackmania_mac/session/monitor.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(areas(path), {"scripts", "c-helpers"})

    def test_x87_inputs(self):
        for path in (
            "tests/test_x87_patch.py",
            "patches/x87sidecar/0001-fix.patch",
            "config/sources.json",
            "uv.lock",
        ):
            with self.subTest(path=path):
                self.assertEqual(areas(path), {"scripts", "x87"})

    def test_ci_definitions_run_every_job(self):
        for path in (".github/workflows/check.yml", ".github/scripts/ci-areas.sh", "Makefile"):
            with self.subTest(path=path):
                self.assertEqual(areas(path), set(JOBS))

    def test_unknown_files_run_the_scripts_check(self):
        self.assertEqual(areas(".gitattributes"), {"scripts"})
        self.assertEqual(areas(".github/workflows/release-hub.yml"), {"scripts"})

    def test_every_check_workflow_job_reads_an_area_output(self):
        workflow = (ROOT / ".github/workflows/check.yml").read_text()
        for job in JOBS:
            with self.subTest(job=job):
                self.assertIn(f"needs.changes.outputs.{job} == 'true'", workflow)


if __name__ == "__main__":
    unittest.main()
