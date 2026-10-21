import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src/shell/cli/tmnf"


class HubBackendTests(unittest.TestCase):
    def run_hub(self, *arguments, home):
        return subprocess.run(["/bin/bash", str(CLI), "hub", *arguments], cwd=ROOT,
                              env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
                              text=True, capture_output=True, timeout=20)

    def test_snapshot_uses_each_titles_own_paths_without_running_wine(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            canyon_game = (home / "Games/TMNF/Canyon/prefix/drive_c/Program Files (x86)/ManiaPlanet"
                           "/ManiaPlanet.exe")
            canyon_game.parent.mkdir(parents=True)
            canyon_game.touch()

            result = self.run_hub("snapshot", home=home)
            self.assertEqual(result.returncode, 0, result.stderr)
            snapshot = json.loads(result.stdout)
            self.assertEqual(snapshot["schema"], 1)
            titles = {title["id"]: title for title in snapshot["titles"]}
            self.assertEqual(set(titles), {"tmnf", "tmu", "tm-original", "tm-canyon", "tm-turbo"})
            canyon = titles["tm-canyon"]
            self.assertEqual(canyon["state"]["game"], "ready")
            self.assertEqual(Path(canyon["apps"]["game"]).name, "TrackMania 2 Canyon.app")
            self.assertEqual([action["id"] for action in canyon["actions"]], ["install-platform"])

    def test_run_rejects_unknown_or_non_snapshot_actions_before_installing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            for arguments in (
                ("run", "unknown", "install"),
                ("run", "tmnf", "not-an-action"),
                ("run", "tmnf", "install", "--source", "/tmp/ignored"),
            ):
                with self.subTest(arguments=arguments):
                    result = self.run_hub(*arguments, home=home)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertTrue(result.stderr.strip())
                    self.assertFalse((home / "Games").exists())


if __name__ == "__main__":
    unittest.main()
