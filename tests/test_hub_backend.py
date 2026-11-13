import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
import unittest
from unittest import mock

from trackmania_mac.hub import run as hub_run
from trackmania_mac.hub import paths as hub_paths
from trackmania_mac.hub import diagnostics, registry, snapshot, state
from trackmania_mac.install import version as project_version


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src/shell/cli/trackmania-mac"


class HubBackendTests(unittest.TestCase):
    def setUp(self):
        # An empty HOME unless a test sets its own: the real one may hold
        # files of an earlier release, and then every action would wait for them.
        temporary = tempfile.TemporaryDirectory(
            dir="/private/tmp", prefix="trackmania-mac-hub-home-"
        )
        self.addCleanup(temporary.cleanup)
        patcher = mock.patch.dict(os.environ, HOME=temporary.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_actions_wait_while_files_of_an_earlier_release_are_left(self):
        legacy = Path(os.environ["HOME"]) / "Library/Logs/TMNF"
        legacy.mkdir(parents=True)
        payload = snapshot.snapshot()
        self.assertEqual(payload["migration"]["state"], "pending")
        self.assertEqual(payload["migration"]["from"], [str(legacy)])
        self.assertTrue(all(title["actions"] == [] for title in payload["titles"]))
        with mock.patch.object(hub_run.os, "execve") as execve, self.assertRaises(SystemExit):
            hub_run.run("tmnf", "doctor", None)
        execve.assert_not_called()

    def test_migrate_runs_the_cli_migration_without_title_overrides(self):
        environment = {
            "TRACKMANIA_MAC_ROOT": "/x",
            "TRACKMANIA_MAC_TITLE": "tmu",
            "TRACKMANIA_MAC_SUPPORT": "/support",
        }
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(hub_run.os, "execve", side_effect=KeyboardInterrupt) as execve,
            self.assertRaises(KeyboardInterrupt),
        ):
            hub_run.migrate()
        command, env = execve.call_args.args[1], execve.call_args.args[2]
        self.assertTrue(command[1].endswith("src/shell/cli/trackmania-mac"))
        self.assertEqual(command[2:], ["migrate"])
        self.assertNotIn("TRACKMANIA_MAC_ROOT", env)
        self.assertNotIn("TRACKMANIA_MAC_TITLE", env)
        self.assertEqual(env["TRACKMANIA_MAC_SUPPORT"], "/support")

    def run_hub(self, *arguments, home, titles_dir=None):
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "TRACKMANIA_MAC_APPS_DIR": str(Path(home) / "Applications"),
        }
        if titles_dir is not None:
            env["TRACKMANIA_MAC_TITLES_DIR"] = str(titles_dir)
        return subprocess.run(
            ["/bin/bash", str(CLI), "hub", *arguments],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )

    def test_snapshot_uses_each_titles_own_paths_without_running_wine(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            canyon_game = (
                home
                / "Library/TrackManiaMacOS/games/tm-canyon/prefix/drive_c/Program Files (x86)/ManiaPlanet"
                "/ManiaPlanet.exe"
            )
            canyon_game.parent.mkdir(parents=True)
            canyon_game.touch()

            result = self.run_hub("snapshot", home=home)
            self.assertEqual(result.returncode, 0, result.stderr)
            snapshot = json.loads(result.stdout)
            self.assertEqual(snapshot["schema"], 3)
            titles = {title["id"]: title for title in snapshot["titles"]}
            # The shipped JSON files, not the registry the snapshot itself reads.
            self.assertEqual(
                set(titles), {path.stem for path in (ROOT / "config/titles").glob("*.json")}
            )
            # Oldest release first; the hub keeps this order within its sections.
            self.assertEqual(
                [(title["releaseDate"], title["id"]) for title in snapshot["titles"]],
                sorted((title["releaseDate"], title["id"]) for title in snapshot["titles"]),
            )
            self.assertEqual(snapshot["titles"][0]["id"], "tm-original")
            canyon = titles["tm-canyon"]
            self.assertEqual(canyon["state"]["game"]["ready"], True)
            self.assertEqual(canyon["kind"], "online-trial")
            self.assertEqual(canyon["status"], "unsupported")
            self.assertEqual(canyon["releaseDate"], "2011-09-14")
            self.assertEqual(titles["tmu"]["status"], "provisional")
            self.assertEqual(titles["tmnf"]["status"], "supported")
            self.assertEqual(Path(canyon["apps"]["game"]).name, "TrackMania 2 Canyon.app")
            # TMU's retail installer keeps the serial-key note surfaced.
            self.assertEqual(titles["tmu"]["requiresSerial"], True)

    def test_cd_key_gate_reads_install_key_for_united_forever_only(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            games = home / "Library/TrackManiaMacOS/games"
            program_files = "prefix/drive_c/Program Files (x86)"
            tmu = games / "tmu" / program_files / "TmUnitedForever"
            tmnf = games / "tmnf" / program_files / "TmNationsForever"
            for folder in (tmu, tmnf):
                folder.mkdir(parents=True)
                (folder / "TmForever.exe").touch()
            ini = tmu / "Nadeo.ini"

            def titles():
                result = self.run_hub("snapshot", home=home)
                self.assertEqual(result.returncode, 0, result.stderr)
                return {t["id"]: t for t in json.loads(result.stdout)["titles"]}

            # Not installed yet: no CD key item, so the hub shows no notice.
            (tmu / "TmForever.exe").unlink()
            self.assertNotIn("cdKey", titles()["tmu"]["state"])
            (tmu / "TmForever.exe").touch()

            found = titles()
            self.assertEqual(found["tmu"]["state"]["cdKey"]["ready"], False)
            self.assertIn("Nadeo.ini", found["tmu"]["state"]["cdKey"]["note"])
            for other in ("tmnf", "tm-original", "tm-canyon", "tm-turbo"):
                self.assertNotIn("cdKey", found[other]["state"])

            header = (
                ";;;;;;;;;;;\n; WARNING ;\n;;;;;;;;;;;\n"
                "; In order to avoid any misfunctioning do no edit this file !\n \n"
                "[TmForever]\nVersion=2.11.25\nUserSubDir=TrackMania\n"
                "Key=Ask\nDistro=TAHOR\nLanguage=en\n"
            )
            for line in ("", "InstallKey=\n", "InstallKey=%CDKEY%\n", "InstallKey=Ask\n"):
                ini.write_text(header + line)
                cd_key = titles()["tmu"]["state"]["cdKey"]
                self.assertEqual(cd_key, {"ready": False, "note": "CD key required"}, line)

            # A key in another section does not count.
            ini.write_text(header + "[Other]\nInstallKey=PLACEHOLDER-KEY\n")
            self.assertFalse(titles()["tmu"]["state"]["cdKey"]["ready"])

            # A header with a trailing comment still opens the section.
            ini.write_text(header.replace("[TmForever]", "[TmForever] ; game") + "InstallKey=K-1\n")
            self.assertTrue(titles()["tmu"]["state"]["cdKey"]["ready"])

            placeholder = "TEST-PLACEHOLDER-0000"
            ini.write_text(header + f"InstallKey={placeholder}\r\n")
            result = self.run_hub("snapshot", home=home)
            cd_key = {t["id"]: t for t in json.loads(result.stdout)["titles"]}["tmu"]["state"][
                "cdKey"
            ]
            self.assertEqual(cd_key, {"ready": True, "note": "CD key present"})
            # The snapshot reports only whether a key exists, never the key.
            self.assertNotIn(placeholder, result.stdout)

    def test_cd_key_gate_accepts_a_key_registered_in_the_game_launcher(self):
        # The retail launcher's Register key stores the key in the player's
        # Config/pkey.dat and leaves Nadeo.ini at Key=Ask with no InstallKey.
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            game = home / "Library/TrackManiaMacOS/games/tmu/prefix/drive_c/Program Files (x86)"
            game = game / "TmUnitedForever"
            game.mkdir(parents=True)
            (game / "TmForever.exe").touch()
            (game / "Nadeo.ini").write_text("[TmForever]\nKey=Ask\n")
            config = home / "Library/TrackManiaMacOS/saves/tmu/TrackMania/Config"
            config.mkdir(parents=True)

            def cd_key():
                result = self.run_hub("snapshot", home=home)
                self.assertEqual(result.returncode, 0, result.stderr)
                titles = {t["id"]: t for t in json.loads(result.stdout)["titles"]}
                return titles["tmu"]["state"]["cdKey"]

            # An empty file is not a registered key.
            (config / "pkey.dat").touch()
            self.assertEqual(cd_key(), {"ready": False, "note": "CD key required"})
            (config / "pkey.dat").write_bytes(b"\x01" * 824)
            self.assertEqual(cd_key(), {"ready": True, "note": "CD key present"})
            # Only a regular file counts.
            (config / "pkey.dat").unlink()
            (config / "pkey.dat").symlink_to(game / "Nadeo.ini")
            self.assertEqual(cd_key(), {"ready": False, "note": "CD key required"})

    def test_install_key_parses_the_tmforever_section(self):
        with tempfile.TemporaryDirectory() as directory:
            ini = Path(directory) / "Nadeo.ini"
            self.assertIsNone(state.install_key(ini))
            ini.write_bytes(b"[TmForever]\r\nKey=Ask\r\n installkey = ABC-123 \r\n")
            self.assertEqual(state.install_key(ini), "ABC-123")
            # The last InstallKey in the section wins.
            ini.write_bytes(b"[TmForever]\nInstallKey=%CDKEY%\nInstallKey=ABC-456\n")
            self.assertEqual(state.install_key(ini), "ABC-456")
            ini.write_bytes(b"[TmForever]\nKey=Ask\n")
            self.assertIsNone(state.install_key(ini))

    def test_snapshot_icon_prefers_game_icon_then_bundled_artwork(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            support = home / "Library/TrackManiaMacOS"

            def titles():
                result = self.run_hub("snapshot", home=home)
                self.assertEqual(result.returncode, 0, result.stderr)
                return {t["id"]: t for t in json.loads(result.stdout)["titles"]}

            for title in titles().values():
                self.assertEqual(title["iconSource"], "bundled")
                self.assertEqual(Path(title["icon"]).parent, ROOT / "assets/titles")
                self.assertIn(Path(title["icon"]).suffix, (".png", ".svg"))
            (support / "icons").mkdir(parents=True)
            (support / "icons/tmu.icns").write_bytes(b"icns")
            found = titles()
            self.assertEqual(found["tmu"]["iconSource"], "game")
            self.assertEqual(found["tmu"]["icon"], str(support / "icons/tmu.icns"))
            self.assertEqual(found["tmnf"]["iconSource"], "bundled")

    def test_run_validates_only_the_target_title(self):
        with tempfile.TemporaryDirectory() as directory:
            titles = Path(directory) / "titles"
            titles.mkdir()
            for path in (ROOT / "config/titles").glob("*.json"):
                (titles / path.name).write_text(path.read_text())
            (titles / "tm-turbo.json").write_text("{}")
            environment = {
                "TRACKMANIA_MAC_TITLES_DIR": str(titles),
                "HOME": directory,
                # Never the real /Applications: wrappers there would offer Uninstall.
                "TRACKMANIA_MAC_APPS_DIR": str(Path(directory) / "Applications"),
            }
            with mock.patch.dict(os.environ, environment):
                actions = hub_run.available_action_ids(registry.get("tmu"))
            self.assertEqual(actions, {"install"})

    def test_state_ignores_the_environment_that_run_scrubs(self):
        scrubbed = {name: "/private/tmp/elsewhere" for name in hub_paths.TITLE_ENV}
        with mock.patch.dict(os.environ, scrubbed):
            paths = hub_paths.compute(registry.get("tmu"))
            env = hub_run._scrubbed_env("tmu")
        self.assertNotIn("elsewhere", str(paths))
        self.assertFalse(set(hub_paths.TITLE_ENV) & set(env))

    def test_titles_dir_is_not_scrubbed(self):
        with mock.patch.dict(os.environ, {"TRACKMANIA_MAC_TITLES_DIR": "/private/tmp/t"}):
            self.assertEqual(
                hub_run._scrubbed_env("tmu")["TRACKMANIA_MAC_TITLES_DIR"], "/private/tmp/t"
            )

    def test_runtime_source_never_leaks_from_the_caller(self):
        inherited = {
            "TRACKMANIA_MAC_RUNTIME_SOURCE": "build",
            "TRACKMANIA_MAC_RUNTIME_ARCHIVE": "/private/tmp/x.tar",
            "TRACKMANIA_MAC_RUNTIME_SHA256": "0" * 64,
        }
        with mock.patch.dict(os.environ, inherited):
            self.assertFalse(set(inherited) & set(hub_run._scrubbed_env("tmu")))
            self.assertFalse(
                (set(inherited) - {"TRACKMANIA_MAC_RUNTIME_SOURCE"})
                & set(hub_run._scrubbed_env("tmu", "download"))
            )
            self.assertEqual(
                hub_run._scrubbed_env("tmu", "download")["TRACKMANIA_MAC_RUNTIME_SOURCE"],
                "download",
            )

    def test_runtime_flag_sets_env_for_install_actions(self):
        cases = (
            (None, "install", []),
            ("build", "install", []),
            ("download", "install-platform", ["setup"]),
            (None, "install-platform", ["setup"]),
        )
        for runtime, action, extra in cases:
            with (
                self.subTest(runtime=runtime, action=action),
                mock.patch.dict(os.environ, {"TRACKMANIA_MAC_RUNTIME_SOURCE": "build"}),
                mock.patch.object(hub_run, "available_action_ids", return_value={action}),
                mock.patch.object(hub_run.os, "execve", side_effect=KeyboardInterrupt) as execve,
                self.assertRaises(KeyboardInterrupt),
            ):
                hub_run.run("tmnf", action, None, runtime)
            command, env = execve.call_args.args[1], execve.call_args.args[2]
            self.assertTrue(command[1].endswith("src/shell/cli/install.sh"))
            self.assertEqual(command[2:], extra)
            self.assertEqual(env.get("TRACKMANIA_MAC_RUNTIME_SOURCE"), runtime)

    def test_runtime_flag_is_rejected_for_other_actions_and_bad_values(self):
        for action, runtime in (
            ("update", "download"),
            ("import", "build"),
            ("install-apps", "build"),
            ("install", "auto"),
            ("install", "bogus"),
            ("install", ""),
        ):
            with (
                self.subTest(action=action, runtime=runtime),
                mock.patch.object(hub_run.os, "execve") as execve,
                self.assertRaises(SystemExit),
            ):
                hub_run.run("tmnf", action, "/tmp" if action == "import" else None, runtime)
            execve.assert_not_called()

    def test_error_placeholders_report_no_icon_source(self):
        placeholder = snapshot._error_placeholder("broken", "bad")
        self.assertIsNone(placeholder["icon"])
        self.assertIsNone(placeholder["iconSource"])

    def test_invalid_entries_become_unsupported_placeholders(self):
        placeholder = snapshot._error_placeholder("broken", "bad")
        self.assertEqual(placeholder["status"], "unsupported")
        self.assertIsNone(placeholder["releaseDate"])
        with tempfile.TemporaryDirectory() as directory:
            titles = Path(directory) / "titles"
            shutil.copytree(ROOT / "config/titles", titles)
            data = json.loads((titles / "tm-original.json").read_text())
            data["release_date"] = "2003-02-30"
            (titles / "tm-original.json").write_text(json.dumps(data))
            with mock.patch.dict(os.environ, {"TRACKMANIA_MAC_TITLES_DIR": str(titles)}):
                loaded, errors, _ = registry.load_registry()
            self.assertIn("tm-original", errors)
            self.assertEqual(
                [title.id for title in loaded],
                [
                    "tmn-eswc",
                    "tmnf",
                    "tmu",
                    "tm-canyon",
                    "tm-stadium",
                    "tm-valley",
                    "tm-turbo",
                    "tm-lagoon",
                    "tm-2020",
                ],
            )

    def test_link_is_an_object_or_null(self):
        linked = registry.get("tm-2020")
        self.assertEqual(
            snapshot._link(linked),
            {"label": linked.link_label, "url": linked.link_url},
        )
        self.assertTrue(linked.link_url.startswith("https://"))
        self.assertIsNone(snapshot._link(registry.get("tmnf")))
        self.assertIsNone(snapshot._error_placeholder("broken", "bad")["link"])
        with mock.patch.object(snapshot, "title_payload", side_effect=OSError("boom")):
            self.assertEqual(snapshot.contained_payload(linked)["link"], snapshot._link(linked))
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_hub("snapshot", home=directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            links = {t["id"]: t["link"] for t in json.loads(result.stdout)["titles"]}
            self.assertEqual(links["tm-2020"], snapshot._link(linked))
            self.assertIsNone(links["tmnf"])

    def test_contained_payload_keeps_status_and_release_date(self):
        title = registry.get("tm-turbo")
        with mock.patch.object(snapshot, "title_payload", side_effect=OSError("boom")):
            payload = snapshot.contained_payload(title)
        self.assertEqual(payload["error"], "boom")
        self.assertEqual(payload["status"], "unsupported")
        self.assertEqual(payload["releaseDate"], "2016-03-24")

    def test_launchers_are_per_title(self):
        with mock.patch.dict(
            os.environ, {"TRACKMANIA_MAC_SUPPORT": "/private/tmp/x/support"}, clear=False
        ):
            for name in ("TRACKMANIA_MAC_LAUNCHER", "TRACKMANIA_MAC_TITLE"):
                os.environ.pop(name, None)
            launchers = {
                title_id: hub_paths.compute(registry.get(title_id)).launcher
                for title_id in ("tmnf", "tmu")
            }
        self.assertEqual(launchers["tmnf"], Path("/private/tmp/x/support/launchers/tmnf"))
        self.assertEqual(launchers["tmu"], Path("/private/tmp/x/support/launchers/tmu"))

    def test_actions_matrix_per_installer_kind(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_hub("snapshot", home=directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            titles = {t["id"]: t for t in json.loads(result.stdout)["titles"]}
            self.assertEqual({a["id"] for a in titles["tmnf"]["actions"]}, {"install"})
            self.assertEqual({a["id"] for a in titles["tmu"]["actions"]}, {"install"})
            self.assertEqual({a["id"] for a in titles["tm-canyon"]["actions"]}, {"install"})
            self.assertEqual({a["id"] for a in titles["tm-turbo"]["actions"]}, {"install-platform"})
            self.assertEqual(
                {a["id"] for a in titles["tm-original"]["actions"]}, {"install-platform"}
            )
            for title in titles.values():
                for action in title["actions"]:
                    self.assertEqual(action.keys(), {"id", "label", "primary", "kind"})

    def test_game_ready_offers_apps_for_import_kinds(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            turbo = (
                home
                / "Library/TrackManiaMacOS/games/tm-turbo/prefix/drive_c/Program Files (x86)/TrackMania Turbo"
                "/TrackmaniaTurbo.exe"
            )
            turbo.parent.mkdir(parents=True)
            turbo.touch()
            result = self.run_hub("snapshot", home=home)
            self.assertEqual(result.returncode, 0, result.stderr)
            titles = {t["id"]: t for t in json.loads(result.stdout)["titles"]}
            actions = titles["tm-turbo"]["actions"]
            self.assertEqual(
                [action["id"] for action in actions], ["install-platform", "install-apps"]
            )
            self.assertTrue(actions[0]["primary"])
            self.assertFalse(actions[1]["primary"])

    def test_united_is_ready_only_with_the_competition_patch(self):
        title = registry.get("tmu")
        self.assertTrue(title.needs_tmloader)
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "prefix"
            game = prefix / "drive_c/Program Files (x86)" / title.game_dir_name / title.game_exe
            game.parent.mkdir(parents=True)
            game.touch()
            paths = mock.Mock(game_exe=game)
            self.assertFalse(state._game_ready(title, paths, prefix).ready)

            pins = dict(registry.pins())
            loader = prefix / "drive_c/Program Files/TMLoader"
            products = loader / "database/TmForever/products"
            files = {
                "tmloader_exe_sha256": loader / "TMLoader.exe",
                "tmcomp_dll_sha256": products
                / "Competition Patch"
                / pins["tmcomp_version"]
                / "TMCompPatch.dll",
                "tmloader_game_sha256": products
                / "TmForever"
                / pins["tmloader_game_version"]
                / "TmForever.exe",
                "tmloader_coremod_sha256": products
                / "CoreMod"
                / pins["tmloader_coremod_version"]
                / "CoreMod.dll",
            }
            for key, path in files.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(key.encode())
                pins[key] = hashlib.sha256(path.read_bytes()).hexdigest()
            (products / "TmForever/settings.yaml").write_text("install: x\n")
            profiles = loader / "database/TmForever/profiles"
            profiles.mkdir(parents=True)
            (profiles / "competition.yaml").write_text("program:\n")
            with mock.patch.object(registry, "pins", return_value=pins):
                self.assertTrue(state._game_ready(title, paths, prefix).ready)

    def test_imported_game_makes_app_wrappers_primary_after_platform_ready(self):
        title = registry.get("tm-turbo")
        items = {
            name: state.Probe(True, "")
            for name in ("runtime", "prefix", "directx", "renderer", "game")
        }
        items.update({"gameApp": state.Probe(False, ""), "settingsApp": state.Probe(False, "")})
        actions = snapshot._actions(title, items)
        self.assertEqual(
            [action["id"] for action in actions],
            ["install-apps", "doctor", "diagnostics", "uninstall"],
        )
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
            (contents / ".trackmania-mac-app").touch()
            (contents / "Info.plist").write_text("<plist/>")
            executable = contents / "MacOS/TrackManiaMac"
            executable.parent.mkdir()
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)
            result = self.run_hub("snapshot", home=home)
            self.assertEqual(result.returncode, 0, result.stderr)
            titles = {t["id"]: t for t in json.loads(result.stdout)["titles"]}
            self.assertTrue(titles["tm-turbo"]["state"]["gameApp"]["ready"])

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
            for title in snapshot["titles"]:
                for key in ("id", "name", "shortName", "status", "notes"):
                    self.assertIsInstance(title[key], str, (title["id"], key))

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
            env = {
                "PATH": "/usr/bin:/bin",
                "HOME": str(home),
                "TRACKMANIA_MAC_AUDIO": "bogus",
                "TRACKMANIA_MAC_WINDOW_SIZE": "0x0",
            }
            result = subprocess.run(
                ["/bin/bash", str(CLI), "hub", "snapshot"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["schema"], 3)

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

    NEW_FIELDS = {"sourceSize": (int, type(None)), "locations": dict, "sessionActive": bool}

    def test_new_fields_for_titles_and_placeholders(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_hub("snapshot", home=directory)
            data = json.loads(result.stdout)
            self.assertEqual(data["hubVersion"], project_version.read(ROOT))
            for title in data["titles"]:
                for key, kind in self.NEW_FIELDS.items():
                    self.assertIsInstance(title[key], kind, key)
                self.assertEqual(
                    set(title["locations"]), {"root", "prefix", "gameDir", "userdata", "logs"}
                )
                for value in title["locations"].values():
                    self.assertTrue(value.startswith("/"))
                self.assertEqual(
                    title["locations"]["logs"],
                    str(Path(directory) / "Library/TrackManiaMacOS/logs"),
                )
                self.assertFalse(title["sessionActive"])
                self.assertIsNone(title["launchOptions"])  # no launcher installed
            self.assertEqual(set(data["storage"]), {"downloads", "runtimes", "logs", "error"})
            self.assertEqual(data["storage"]["runtimes"]["current"], project_version.read(ROOT))
            self.assertEqual(data["storage"]["downloads"], {"bytes": 0, "items": []})
        placeholder = snapshot._error_placeholder("broken", "bad")
        self.assertIsNone(placeholder["launchOptions"])
        self.assertIsNone(placeholder["removal"])
        self.assertEqual(placeholder["locations"], {})
        self.assertIsNone(placeholder["sourceSize"])
        self.assertIs(placeholder["sessionActive"], False)

    def test_management_actions_need_a_ready_prefix(self):
        title = registry.get("tmnf")
        items = {
            name: state.Probe(False, "")
            for name in (
                "runtime",
                "prefix",
                "directx",
                "renderer",
                "game",
                "gameApp",
                "settingsApp",
            )
        }

        def ids(active=False):
            return [a["id"] for a in snapshot._actions(title, items, active)]

        self.assertEqual(ids(True), ["install"])
        items["prefix"] = state.Probe(True, "")
        self.assertEqual(ids(), ["install", "update", "doctor", "diagnostics", "uninstall"])
        self.assertEqual(
            ids(True), ["install", "update", "doctor", "diagnostics", "stop", "uninstall"]
        )
        kinds = {a["id"]: a["kind"] for a in snapshot._actions(title, items, True)}
        self.assertEqual(
            (kinds["doctor"], kinds["diagnostics"], kinds["stop"], kinds["uninstall"]),
            ("verify", "maintenance", "maintenance", "destructive"),
        )

    def test_uninstall_is_offered_whenever_a_title_left_something_behind(self):
        # A managed root: the uninstall preview never lists paths outside one.
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            home = Path(directory).resolve()
            apps = home / "Applications"
            tmu = registry.get("tmu")
            for name, bundle in (
                (tmu.app_name, tmu.bundle_id_game),
                (tmu.settings_app_name, tmu.bundle_id_settings),
            ):
                contents = apps / name / "Contents"
                (contents / "MacOS").mkdir(parents=True)
                (contents / "Info.plist").write_bytes(
                    plistlib.dumps({"CFBundleIdentifier": bundle})
                )
                (contents / ".trackmania-mac-app").touch()
            (home / "Library/TrackManiaMacOS/saves/tm-canyon").mkdir(parents=True)
            result = self.run_hub("snapshot", home=home)
            self.assertEqual(result.returncode, 0, result.stderr)
            titles = {title["id"]: title for title in json.loads(result.stdout)["titles"]}

        def action_ids(title_id):
            return [action["id"] for action in titles[title_id]["actions"]]

        self.assertIn("uninstall", action_ids("tmu"))
        self.assertEqual(
            [item["kind"] for item in titles["tmu"]["removal"]["items"]],
            ["game-app", "settings-app"],
        )
        self.assertEqual(
            titles["tmu"]["removal"]["items"][0],
            {"kind": "game-app", "label": "Game app", "path": str(apps / tmu.app_name)},
        )
        self.assertIsNone(titles["tmu"]["removal"]["saves"])
        self.assertIn("uninstall", action_ids("tm-canyon"))
        self.assertEqual(
            titles["tm-canyon"]["removal"],
            {"items": [], "saves": str(home / "Library/TrackManiaMacOS/saves/tm-canyon")},
        )
        self.assertNotIn("uninstall", action_ids("tm-original"))
        self.assertEqual(titles["tm-original"]["removal"], {"items": [], "saves": None})

    def test_include_saves_only_reaches_uninstall(self):
        for include_saves, extra in (
            (False, ["uninstall", "--yes"]),
            (True, ["uninstall", "--yes", "--include-saves"]),
        ):
            with (
                self.subTest(include_saves=include_saves),
                mock.patch.object(hub_run, "available_action_ids", return_value={"uninstall"}),
                mock.patch.object(hub_run.os, "execve", side_effect=KeyboardInterrupt) as execve,
                self.assertRaises(KeyboardInterrupt),
            ):
                hub_run.run("tmnf", "uninstall", None, include_saves=include_saves)
            self.assertEqual(execve.call_args.args[1][2:], extra)
        for action in ("install", "doctor", "stop"):
            with (
                self.subTest(action=action),
                mock.patch.object(hub_run.os, "execve") as execve,
                self.assertRaises(SystemExit),
            ):
                hub_run.run("tmnf", action, None, include_saves=True)
            execve.assert_not_called()

    def test_cli_parses_include_saves(self):
        from trackmania_mac.hub import __main__ as hub_main

        with mock.patch.object(hub_main.hub_run, "run", side_effect=SystemExit(0)) as run:
            with self.assertRaises(SystemExit):
                hub_main.main(["run", "tmu", "uninstall", "--include-saves"])
            with self.assertRaises(SystemExit):
                hub_main.main(["run", "tmu", "uninstall"])
        self.assertEqual(
            [call.kwargs["include_saves"] for call in run.call_args_list], [True, False]
        )

    def test_session_active_is_safe(self):
        guard = state.operation_guard
        with mock.patch.object(
            guard, "check_inactive", side_effect=ValueError("Wine is active for prefix: x")
        ):
            self.assertTrue(state.session_active(Path("/x")))
        for error in (ValueError("Unsafe Wine prefix"), OSError("boom"), RuntimeError("x")):
            with mock.patch.object(guard, "check_inactive", side_effect=error):
                self.assertFalse(state.session_active(Path("/x")))
        with mock.patch.object(guard, "check_inactive", return_value=None):
            self.assertFalse(state.session_active(Path("/x")))
        self.assertFalse(state.session_active(Path("/private/tmp/does-not-exist-tmnf")))

    def test_session_active_sees_a_held_wine_server_lock(self):
        # Managed paths must sit below home or /private/tmp.
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory).resolve()
            prefix = root / "prefix"
            prefix.mkdir(mode=0o755)
            info = prefix.lstat()
            user_dir = root / f".wine-{os.getuid()}"
            lock_dir = user_dir / f"server-{info.st_dev:x}-{info.st_ino:x}"
            for private in (user_dir, lock_dir):
                private.mkdir(mode=0o700)
            lock = lock_dir / "lock"
            lock.touch(mode=0o600)
            with mock.patch.object(state.operation_guard, "WINE_SERVER_ROOT", root):
                self.assertFalse(state.session_active(prefix))
                code = (
                    "import fcntl,sys,time;f=open(sys.argv[1],'r+b');"
                    "fcntl.lockf(f,fcntl.LOCK_EX,1,0,0);print('ok',flush=True);time.sleep(30)"
                )
                child = subprocess.Popen(
                    ["/usr/bin/python3", "-c", code, str(lock)], stdout=subprocess.PIPE, text=True
                )
                try:
                    self.assertEqual(child.stdout.readline().strip(), "ok")
                    self.assertTrue(state.session_active(prefix))
                finally:
                    child.kill()
                    child.wait()
                self.assertFalse(state.session_active(prefix))

    def test_new_actions_dispatch_to_the_cli(self):
        cases = {
            "doctor": ["doctor"],
            "diagnostics": ["diagnostics"],
            "stop": ["stop"],
            "uninstall": ["uninstall", "--yes"],
        }
        for action, extra in cases.items():
            with (
                self.subTest(action=action),
                mock.patch.object(hub_run, "available_action_ids", return_value={action}),
                mock.patch.object(hub_run.os, "execve", side_effect=KeyboardInterrupt) as execve,
                self.assertRaises(KeyboardInterrupt),
            ):
                hub_run.run("tmnf", action, None)
            command = execve.call_args.args[1]
            self.assertTrue(command[1].endswith("src/shell/cli/trackmania-mac"))
            self.assertEqual(command[2:], extra)

    def write_launcher(self, title_id, lines):
        launcher = Path(os.environ["HOME"]) / "Library/TrackManiaMacOS/launchers" / title_id
        (launcher / "config").mkdir(parents=True)
        (launcher / ".trackmania-mac-launcher").touch()
        settings = {"TITLE": title_id, **lines}
        (launcher / "config/installation.sh").write_text(
            "".join(
                f'if test -z "${{TRACKMANIA_MAC_{key}:-}}"; then export TRACKMANIA_MAC_{key}={value}; fi\n'
                for key, value in settings.items()
            )
        )
        return launcher

    def test_launch_options_read_the_installed_launcher(self):
        tmnf = registry.get("tmnf")
        self.assertIsNone(snapshot.title_payload(tmnf)["launchOptions"])
        launcher = self.write_launcher("tmnf", {"WINDOW_SIZE": "1280x720", "AUDIO": "off"})
        self.assertEqual(
            snapshot.title_payload(tmnf)["launchOptions"],
            {
                "windowSize": "1280x720",
                "audio": "off",
                "x87": "on",
                "renderer": "d9vk",
                "editable": True,
                "reason": None,
            },
        )
        config = launcher / "config/installation.sh"
        config.write_text(config.read_text().replace("AUDIO=off", "AUDIO=loud"))
        options = snapshot.title_payload(tmnf)["launchOptions"]
        self.assertEqual((options["audio"], options["editable"]), ("on", True))
        self.assertIn("audio", options["reason"])
        with config.open("a") as stream:
            stream.write("echo surprise\n")
        options = snapshot.title_payload(tmnf)["launchOptions"]
        self.assertFalse(options["editable"])
        self.assertTrue(options["reason"])
        (launcher / ".trackmania-mac-launcher").unlink()
        config.write_text(config.read_text().replace("echo surprise\n", ""))
        self.assertFalse(snapshot.title_payload(tmnf)["launchOptions"]["editable"])

    def test_set_defaults_runs_the_defaults_command(self):
        self.write_launcher("tmnf", {})
        with (
            mock.patch.object(hub_run.os, "execve", side_effect=KeyboardInterrupt) as execve,
            self.assertRaises(KeyboardInterrupt),
        ):
            hub_run.run(
                "tmnf",
                "set-defaults",
                None,
                launch_options={"--audio": "off", "--window-size": "1280x720", "--x87": None},
            )
        command, env = execve.call_args.args[1], execve.call_args.args[2]
        self.assertTrue(command[1].endswith("src/shell/cli/trackmania-mac"))
        self.assertEqual(command[2:], ["defaults", "--window-size", "1280x720", "--audio", "off"])
        self.assertEqual(env["TRACKMANIA_MAC_TITLE"], "tmnf")

    def test_set_defaults_is_refused_without_flags_apps_or_a_readable_config(self):
        cases = [("tmnf", {}), ("tmu", {"--audio": "off"})]  # tmu has no launcher
        self.write_launcher("tmnf", {})
        broken = self.write_launcher("tm-canyon", {})
        with (broken / "config/installation.sh").open("a") as stream:
            stream.write("echo surprise\n")
        cases.append(("tm-canyon", {"--x87": "off"}))
        for title_id, options in cases:
            with (
                self.subTest(title=title_id, options=options),
                mock.patch.object(hub_run.os, "execve") as execve,
                mock.patch("sys.stderr", new_callable=io.StringIO) as stderr,
                self.assertRaises(SystemExit) as raised,
            ):
                hub_run.run(title_id, "set-defaults", None, launch_options=options)
            execve.assert_not_called()
            self.assertEqual(raised.exception.code, 1)
            self.assertTrue(stderr.getvalue().startswith("error: "), stderr.getvalue())

    def test_launch_flags_only_reach_set_defaults(self):
        for action in ("install", "doctor", "uninstall"):
            with (
                self.subTest(action=action),
                mock.patch.object(hub_run, "available_action_ids", return_value={action}),
                mock.patch.object(hub_run.os, "execve") as execve,
                self.assertRaises(SystemExit),
            ):
                hub_run.run("tmnf", action, None, launch_options={"--audio": "off"})
            execve.assert_not_called()

    def test_cli_parses_launch_flags_and_clean(self):
        from trackmania_mac.hub import __main__ as hub_main

        with mock.patch.object(hub_main.hub_run, "run", side_effect=SystemExit(0)) as run:
            with self.assertRaises(SystemExit):
                hub_main.main(["run", "tmu", "set-defaults", "--window-size", "800x600"])
        self.assertEqual(
            run.call_args.kwargs["launch_options"],
            {"--window-size": "800x600", "--audio": None, "--x87": None},
        )
        with mock.patch.object(hub_main.hub_run, "clean", side_effect=SystemExit(0)) as clean:
            with self.assertRaises(SystemExit):
                hub_main.main(["clean", "logs"])
        clean.assert_called_once_with("logs")

    def test_clean_runs_the_cli_without_title_overrides(self):
        environment = {"TRACKMANIA_MAC_ROOT": "/x", "TRACKMANIA_MAC_TITLE": "tmu"}
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(hub_run.os, "execve", side_effect=KeyboardInterrupt) as execve,
            self.assertRaises(KeyboardInterrupt),
        ):
            hub_run.clean("downloads")
        command, env = execve.call_args.args[1], execve.call_args.args[2]
        self.assertTrue(command[1].endswith("src/shell/cli/trackmania-mac"))
        self.assertEqual(command[2:], ["clean", "downloads"])
        self.assertNotIn("TRACKMANIA_MAC_ROOT", env)
        self.assertNotIn("TRACKMANIA_MAC_TITLE", env)

    def test_clean_refuses_unknown_kinds_and_waits_for_the_migration(self):
        with (
            mock.patch.object(hub_run.os, "execve") as execve,
            mock.patch("sys.stderr", new_callable=io.StringIO),
            self.assertRaises(SystemExit),
        ):
            hub_run.clean("everything")
        execve.assert_not_called()
        (Path(os.environ["HOME"]) / "Library/Logs/TMNF").mkdir(parents=True)
        with (
            mock.patch.object(hub_run.os, "execve") as execve,
            mock.patch("sys.stderr", new_callable=io.StringIO),
            self.assertRaises(SystemExit),
        ):
            hub_run.clean("logs")
        execve.assert_not_called()

    def test_hub_clean_and_set_defaults_end_to_end(self):
        home = Path(os.environ["HOME"])
        logs = home / "Library/TrackManiaMacOS/logs"
        logs.mkdir(parents=True)
        (logs / "play.log").write_text("log")
        result = self.run_hub("clean", "logs", home=home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Removed {logs.resolve() / 'play.log'}", result.stdout)
        self.assertTrue((logs / "play.log.lock").exists())
        result = self.run_hub("clean", "bogus", home=home)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("error: "), result.stderr)
        result = self.run_hub("run", "tmnf", "set-defaults", "--audio", "off", home=home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("error: ", result.stderr)

    def test_redact_home_and_username(self):
        text = "/Users/alice/Games and alice here, malice and alice_x stay; ~/x"
        out = diagnostics.redact(text, "/Users/alice", "alice")
        self.assertNotIn("/Users/alice", out)
        self.assertIn("~/Games", out)
        self.assertIn("<user> here", out)
        self.assertIn("malice", out)
        self.assertIn("alice_x", out)

    def test_diagnostics_zip_contents_are_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "alice"
            logs = home / "Library/TrackManiaMacOS/logs"
            logs.mkdir(parents=True)
            for index in range(7):
                log = logs / f"tmu-run{index}.log"
                log.write_text(f"line from {home} by alice\n")
                os.utime(log, (1000 + index, 1000 + index))
            (logs / "play.log").write_text("not this title")
            os.utime(logs / "play.log", (9500, 9500))
            (logs / "tmu-big.log").write_text(
                "x" * (diagnostics.LOG_TAIL_BYTES + 100) + f"END {home}"
            )
            os.utime(logs / "tmu-big.log", (9000, 9000))
            with (
                mock.patch.dict(os.environ, {"HOME": str(home), "TRACKMANIA_MAC_TITLE": "tmu"}),
                mock.patch.object(diagnostics.getpass, "getuser", return_value="alice"),
            ):
                target = diagnostics.build("tmu", doctor_output=f"doctor says {home}\n")
            self.assertEqual(target.parent, logs)
            self.assertRegex(target.name, r"^diagnostics-tmu-\d{8}-\d{6}\.zip$")
            with zipfile.ZipFile(target) as archive:
                names = set(archive.namelist())
                self.assertTrue({"summary.txt", "doctor.txt", "snapshot.json"} <= names)
                self.assertEqual(
                    len([n for n in names if n.startswith("logs/")]), diagnostics.LOG_COUNT
                )
                self.assertIn("logs/tmu-big.log", names)
                self.assertNotIn("logs/play.log", names)
                for name in names:
                    body = archive.read(name).decode()
                    self.assertNotIn(str(home), body, name)
                    self.assertNotIn("alice", body, name)
                self.assertLessEqual(
                    len(archive.read("logs/tmu-big.log")), diagnostics.LOG_TAIL_BYTES + 100
                )
                self.assertTrue(archive.read("logs/tmu-big.log").decode().endswith("END ~"))
                self.assertEqual(json.loads(archive.read("snapshot.json"))["id"], "tmu")

    def test_diagnostics_logs_are_per_title(self):
        with tempfile.TemporaryDirectory() as directory:
            logs = Path(directory)
            names = ["play.log", "configure.log", "tmu-play.log", "tmu-configure.log", "other.log"]
            for index, name in enumerate(names):
                (logs / name).write_text("x")
                os.utime(logs / name, (1000 + index, 1000 + index))

            def found(title):
                return sorted(p.name for p in diagnostics._recent_logs(logs, title))

            self.assertEqual(found("tmnf"), ["configure.log", "play.log"])
            self.assertEqual(found("tmu"), ["tmu-configure.log", "tmu-play.log"])
            self.assertEqual(found("tm-canyon"), [])

    def test_doctor_timeout_signals_the_process_group_and_waits_for_guard_grace(self):
        # Fake doctor: a shell that forks a child, ignores nothing, and sleeps.
        script = (
            "import os, signal, subprocess, sys, time\n"
            "pidfile = sys.argv[1]\n"
            "child = subprocess.Popen(['sleep', '60'])\n"
            "open(pidfile, 'w').write(str(child.pid))\n"
            "signal.signal(signal.SIGTERM, lambda *a: (time.sleep(0.3), sys.exit(143)))\n"
            "time.sleep(60)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "child.pid"
            real_popen = subprocess.Popen

            def fake_popen(command, **kwargs):
                self.assertTrue(kwargs.get("start_new_session"))
                # The doctor command must name the CLI that exists.
                self.assertTrue(Path(command[1]).is_file(), command)
                return real_popen([sys.executable, "-c", script, str(pidfile)], **kwargs)

            waits = []
            real_stop = diagnostics._stop_group

            def recording_stop(child):
                waits.append(child.pid)
                real_stop(child)

            with (
                mock.patch.object(diagnostics.subprocess, "Popen", fake_popen),
                mock.patch.object(diagnostics, "DOCTOR_TIMEOUT", 1),
                mock.patch.object(diagnostics, "_stop_group", recording_stop),
            ):
                started = time.monotonic()
                output = diagnostics._doctor("tmnf")
            self.assertLess(time.monotonic() - started, 10)
            self.assertIn("timed out", output)
            self.assertEqual(len(waits), 1)
            grandchild = int(pidfile.read_text())
            for _ in range(50):
                try:
                    os.kill(grandchild, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                os.kill(grandchild, signal.SIGKILL)
                self.fail("the doctor's child survived the timeout")

    def test_doctor_timeout_escalation_outlasts_guard_grace(self):
        # A child that ignores SIGTERM must get at least INTERRUPT_GRACE before SIGKILL.
        child = mock.Mock(pid=4242)
        child.wait.side_effect = [subprocess.TimeoutExpired("x", 1), None]
        with mock.patch.object(diagnostics.os, "killpg") as killpg:
            diagnostics._stop_group(child)
        self.assertEqual(
            [call.args for call in killpg.call_args_list],
            [(4242, signal.SIGTERM), (4242, signal.SIGKILL)],
        )
        self.assertGreater(
            child.wait.call_args_list[0].kwargs["timeout"],
            diagnostics.operation_guard.INTERRUPT_GRACE,
        )

    def test_prefix_with_initializing_marker_is_not_ready(self):
        title = registry.get("tmnf")
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            (prefix / ".trackmania-mac-prefix").write_text(title.prefix_marker)
            (prefix / "system.reg").write_text("")
            self.assertTrue(state._prefix_ready(title, prefix).ready)
            (prefix / ".trackmania-mac-initializing").touch()
            probe = state._prefix_ready(title, prefix)
            self.assertFalse(probe.ready)
            self.assertIn("run install again", probe.note)
            # Legacy tmnf prefix (empty marker) is covered by the same check.
            (prefix / ".trackmania-mac-prefix").write_text("")
            self.assertFalse(state._prefix_ready(title, prefix).ready)


if __name__ == "__main__":
    unittest.main()
