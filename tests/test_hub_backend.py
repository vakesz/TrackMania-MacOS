import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tmnf.hub import run as hub_run
from tmnf.hub import registry, snapshot, state


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src/shell/cli/tmnf"


class HubBackendTests(unittest.TestCase):
    def run_hub(self, *arguments, home, titles_dir=None):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home),
               "TMNF_APPS_DIR": str(Path(home) / "Applications")}
        if titles_dir is not None:
            env["TMNF_TITLES_DIR"] = str(titles_dir)
        return subprocess.run(["/bin/bash", str(CLI), "hub", *arguments], cwd=ROOT,
                              env=env, text=True, capture_output=True, timeout=30)

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
            self.assertEqual(snapshot["schema"], 2)
            titles = {title["id"]: title for title in snapshot["titles"]}
            self.assertEqual(set(titles), {"tmnf", "tmu", "tm-original", "tm-canyon", "tm-turbo"})
            canyon = titles["tm-canyon"]
            self.assertEqual(canyon["state"]["game"]["ready"], True)
            self.assertEqual(canyon["kind"], "online-trial")
            self.assertEqual(canyon["status"], "provisional")
            self.assertEqual(Path(canyon["apps"]["game"]).name, "TrackMania 2 Canyon.app")
            # The trial installs the game itself; compare to a disk-image title.
            self.assertEqual([action["id"] for action in canyon["actions"]], ["install"])
            self.assertEqual([action["id"] for action in titles["tm-original"]["actions"]],
                             ["install-platform"])
            # TMU's retail installer keeps the serial-key note surfaced.
            self.assertEqual(titles["tmu"]["requiresSerial"], True)

    def test_actions_matrix_per_installer_kind(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_hub("snapshot", home=directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            titles = {t["id"]: t for t in json.loads(result.stdout)["titles"]}
            self.assertEqual(
                {a["id"] for a in titles["tmnf"]["actions"]}, {"install"})
            self.assertEqual(
                {a["id"] for a in titles["tmu"]["actions"]}, {"install"})
            self.assertEqual(
                {a["id"] for a in titles["tm-canyon"]["actions"]}, {"install"})
            self.assertEqual(
                {a["id"] for a in titles["tm-turbo"]["actions"]}, {"install-platform"})
            self.assertEqual(
                {a["id"] for a in titles["tm-original"]["actions"]}, {"install-platform"})
            for title in titles.values():
                for action in title["actions"]:
                    self.assertEqual(action.keys(), {"id", "label", "primary", "kind"})

    def test_game_ready_offers_apps_for_import_kinds(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            turbo = (home / "Games/TMNF/Turbo/prefix/drive_c/Program Files (x86)/TrackMania Turbo"
                     "/TrackmaniaTurbo.exe")
            turbo.parent.mkdir(parents=True)
            turbo.touch()
            result = self.run_hub("snapshot", home=home)
            self.assertEqual(result.returncode, 0, result.stderr)
            titles = {t["id"]: t for t in json.loads(result.stdout)["titles"]}
            actions = titles["tm-turbo"]["actions"]
            self.assertEqual([action["id"] for action in actions],
                             ["install-platform", "install-apps"])
            self.assertTrue(actions[0]["primary"])
            self.assertFalse(actions[1]["primary"])

    def test_imported_game_makes_app_wrappers_primary_after_platform_ready(self):
        title = registry.get("tm-turbo")
        items = {name: state.Probe(True, "") for name in
                 ("runtime", "prefix", "directx", "renderer", "game")}
        items.update({"gameApp": state.Probe(False, ""),
                      "settingsApp": state.Probe(False, "")})
        actions = snapshot._actions(title, items)
        self.assertEqual([action["id"] for action in actions], ["install-apps"])
        self.assertTrue(actions[0]["primary"])

    def test_unmanaged_or_symlinked_app_is_not_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            app = home / "Applications/TrackMania Turbo.app"
            app.mkdir(parents=True)
            result = self.run_hub("snapshot", home=home)
            self.assertEqual(result.returncode, 0, result.stderr)
            titles = {t["id"]: t for t in json.loads(result.stdout)["titles"]}
            self.assertFalse(titles["tm-turbo"]["state"]["gameApp"]["ready"])

            contents = app / "Contents"
            contents.mkdir()
            (contents / ".tmnf-app").touch()
            (contents / "Info.plist").write_text("<plist/>")
            executable = contents / "MacOS/TMNF"
            executable.parent.mkdir()
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)
            result = self.run_hub("snapshot", home=home)
            self.assertEqual(result.returncode, 0, result.stderr)
            titles = {t["id"]: t for t in json.loads(result.stdout)["titles"]}
            self.assertTrue(titles["tm-turbo"]["state"]["gameApp"]["ready"])

    def test_run_scrubs_title_specific_root_override(self):
        with mock.patch.dict(os.environ, {"TMNF_ROOT": "/tmp/one-title-only"}, clear=True):
            environment = hub_run._scrubbed_env("tm-turbo")
        self.assertEqual(environment["TMNF_TITLE"], "tm-turbo")
        self.assertNotIn("TMNF_ROOT", environment)

    def test_bad_registry_entry_is_contained_per_title(self):
        with tempfile.TemporaryDirectory() as directory:
            homedir = Path(directory) / "home"
            titles = Path(directory) / "titles"
            titles.mkdir(parents=True)
            for path in (ROOT / "config/titles").glob("*.json"):
                (titles / path.name).write_text(path.read_text())
            (titles / "tmnf.json").write_text('{"id": "broken"}')  # Malformed.
            result = self.run_hub("snapshot", home=homedir, titles_dir=titles)
            self.assertEqual(result.returncode, 0, result.stderr)
            snapshot = json.loads(result.stdout)
            titles = {t["id"]: t for t in snapshot["titles"]}
            self.assertTrue(titles["tmnf"]["error"])
            self.assertIn("tmu", titles)
            self.assertEqual(titles["tmu"]["error"], None)

    def test_broken_registry_file_is_contained(self):
        with tempfile.TemporaryDirectory() as directory:
            homedir = Path(directory) / "home"
            titles = Path(directory) / "titles"
            titles.mkdir(parents=True)
            for path in (ROOT / "config/titles").glob("*.json"):
                if path.stem != "tmnf":
                    (titles / path.name).write_text(path.read_text())
            (titles / "tmnf.json").write_text("not json at all")
            result = self.run_hub("snapshot", home=homedir, titles_dir=titles)
            self.assertEqual(result.returncode, 0, result.stderr)
            snapshot = json.loads(result.stdout)
            titles = {t["id"]: t for t in snapshot["titles"]}
            # The real registry still carries tmnf; other titles parse.
            self.assertIn("tmu", titles)
            self.assertNotIn("tmnf", titles)

    def test_all_unreadable_registry_files_return_a_root_error(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            titles = Path(directory) / "titles"
            titles.mkdir(parents=True)
            (titles / "tmnf.json").write_text("not valid json")
            result = self.run_hub("snapshot", home=home, titles_dir=titles)
            self.assertEqual(result.returncode, 0, result.stderr)
            document = json.loads(result.stdout)
            self.assertEqual(document["titles"], [])
            self.assertEqual(document["error"], "tmnf.json: not valid JSON")

    def test_snapshot_survives_mistyped_launch_options(self):
        """A bad launch option must not break the whole hub UI (state probe only)."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "TMNF_AUDIO": "bogus",
                   "TMNF_WINDOW_SIZE": "0x0"}
            result = subprocess.run(
                ["/bin/bash", str(CLI), "hub", "snapshot"], cwd=ROOT,
                env=env, text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["schema"], 2)

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
