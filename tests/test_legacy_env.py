"""Settings of earlier releases (TMNF_*) keep working under their new names.

The shell (src/shell/lib/legacy.sh) and Python (trackmania_mac/legacy.py) apply
one rule; these tests hold both to it. Every run uses a temporary HOME.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from trackmania_mac import legacy

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src/shell/cli/trackmania-mac"
ENVIRONMENT = {
    "TMNF_AUDIO": "off",
    "TMNF_RENDERER": "wined3d-gl",
    "TRACKMANIA_MAC_RENDERER": "d9vk",
    "TMNF_OPERATION_FD": "9",
    "TMNF_TITLE_NAME": "stale",
    "XTMNF_UNRELATED": "kept",
}
EXPECTED = {
    "TRACKMANIA_MAC_AUDIO": "off",
    "TRACKMANIA_MAC_RENDERER": "d9vk",
    "XTMNF_UNRELATED": "kept",
}
NOTICES = [
    "warning: TMNF_AUDIO is now TRACKMANIA_MAC_AUDIO; the old name still works.",
    "warning: TMNF_RENDERER is ignored because TRACKMANIA_MAC_RENDERER is set.",
]


def shell(environment):
    """(adopted settings, stderr) after legacy.sh runs in a clean shell, builtins only."""
    script = (
        '. "$1"; adopt_legacy_settings; '
        "for name in ${!TMNF_*} ${!TRACKMANIA_MAC_*} ${!XTMNF_*}; do "
        'printf "%s=%s\\n" "$name" "${!name}"; done'
    )
    result = subprocess.run(
        [
            "/usr/bin/env",
            "-i",
            "PATH=/nonexistent",
            *("{}={}".format(*item) for item in environment.items()),
            "/bin/bash",
            "-c",
            script,
            "_",
            str(ROOT / "src/shell/lib/legacy.sh"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    adopted = dict(line.split("=", 1) for line in result.stdout.splitlines())
    return adopted, result.stderr.splitlines()


class LegacyEnvironmentTests(unittest.TestCase):
    def test_python_maps_drops_and_reports_once(self):
        environ = dict(ENVIRONMENT)
        stream = io.StringIO()
        legacy.adopt_settings(environ, stream)
        self.assertEqual(environ, EXPECTED)
        self.assertEqual(sorted(stream.getvalue().splitlines()), sorted(NOTICES))
        stream = io.StringIO()
        legacy.adopt_settings(environ, stream)
        self.assertEqual(stream.getvalue(), "")

    def test_shell_and_python_agree(self):
        adopted, notices = shell(ENVIRONMENT)
        self.assertEqual(adopted, EXPECTED)
        self.assertEqual(sorted(notices), sorted(NOTICES))

    def test_hub_snapshot_stdout_stays_json_under_a_minimal_path(self):
        with tempfile.TemporaryDirectory(
            dir="/private/tmp", prefix="trackmania-mac-legacy-env-"
        ) as home:
            result = subprocess.run(
                ["/bin/bash", str(CLI), "hub", "snapshot"],
                env={
                    "HOME": home,
                    "PATH": "/usr/bin:/bin",
                    "TMNF_AUDIO": "off",
                    "TMNF_CACHE": home,
                },
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(json.loads(result.stdout)["schema"], 3)
            self.assertIn("TMNF_AUDIO is now TRACKMANIA_MAC_AUDIO", result.stderr)
            self.assertEqual(result.stderr.count("TMNF_CACHE is now"), 1)

    def test_python_entry_points_adopt_too(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os, trackmania_mac; print(os.environ['TRACKMANIA_MAC_X87'])",
            ],
            env={
                "PYTHONPATH": str(ROOT / "src/python"),
                "TMNF_X87": "off",
                "PATH": "/usr/bin:/bin",
            },
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "off")
        self.assertIn("TMNF_X87 is now TRACKMANIA_MAC_X87", result.stderr)


if __name__ == "__main__":
    unittest.main()
