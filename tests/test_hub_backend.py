import hashlib
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
import unittest
from unittest import mock

from tmnf.hub import run as hub_run
from tmnf.hub import paths as hub_paths
from tmnf.hub import diagnostics, registry, snapshot, state


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src/shell/cli/tmnf"


class HubBackendTests(unittest.TestCase):
    def run_hub(self, *arguments, home, titles_dir=None):
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "TMNF_APPS_DIR": str(Path(home) / "Applications"),
        }
        if titles_dir is not None:
            env["TMNF_TITLES_DIR"] = str(titles_dir)
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
                home / "Games/TMNF/Canyon/prefix/drive_c/Program Files (x86)/ManiaPlanet"
                "/ManiaPlanet.exe"
            )
            canyon_game.parent.mkdir(parents=True)
            canyon_game.touch()

            result = self.run_hub("snapshot", home=home)
            self.assertEqual(result.returncode, 0, result.stderr)
            snapshot = json.loads(result.stdout)
            self.assertEqual(snapshot["schema"], 2)
            titles = {title["id"]: title for title in snapshot["titles"]}
            # The shipped JSON files, not the registry the snapshot itself reads.
            self.assertEqual(
                set(titles), {path.stem for path in (ROOT / "config/titles").glob("*.json")}
            )
            canyon = titles["tm-canyon"]
            self.assertEqual(canyon["state"]["game"]["ready"], True)
            self.assertEqual(canyon["kind"], "online-trial")
            self.assertEqual(canyon["status"], "provisional")
            self.assertEqual(Path(canyon["apps"]["game"]).name, "TrackMania 2 Canyon.app")
            # TMU's retail installer keeps the serial-key note surfaced.
            self.assertEqual(titles["tmu"]["requiresSerial"], True)

    def test_snapshot_icon_prefers_game_icon_then_bundled_artwork(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            support = home / "Library/Application Support/TMNF"

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
                "TMNF_TITLES_DIR": str(titles),
                "HOME": directory,
                # Never the real /Applications: wrappers there would offer Uninstall.
                "TMNF_APPS_DIR": str(Path(directory) / "Applications"),
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
        with mock.patch.dict(os.environ, {"TMNF_TITLES_DIR": "/private/tmp/t"}):
            self.assertEqual(hub_run._scrubbed_env("tmu")["TMNF_TITLES_DIR"], "/private/tmp/t")

    def test_runtime_source_never_leaks_from_the_caller(self):
        inherited = {
            "TMNF_RUNTIME_SOURCE": "build",
            "TMNF_RUNTIME_ARCHIVE": "/private/tmp/x.tar",
            "TMNF_RUNTIME_SHA256": "0" * 64,
        }
        with mock.patch.dict(os.environ, inherited):
            self.assertFalse(set(inherited) & set(hub_run._scrubbed_env("tmu")))
            self.assertFalse(
                (set(inherited) - {"TMNF_RUNTIME_SOURCE"})
                & set(hub_run._scrubbed_env("tmu", "download"))
            )
            self.assertEqual(
                hub_run._scrubbed_env("tmu", "download")["TMNF_RUNTIME_SOURCE"], "download"
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
                mock.patch.dict(os.environ, {"TMNF_RUNTIME_SOURCE": "build"}),
                mock.patch.object(hub_run, "available_action_ids", return_value={action}),
                mock.patch.object(hub_run.os, "execve", side_effect=KeyboardInterrupt) as execve,
                self.assertRaises(KeyboardInterrupt),
            ):
                hub_run.run("tmnf", action, None, runtime)
            command, env = execve.call_args.args[1], execve.call_args.args[2]
            self.assertTrue(command[1].endswith("src/shell/cli/install.sh"))
            self.assertEqual(command[2:], extra)
            self.assertEqual(env.get("TMNF_RUNTIME_SOURCE"), runtime)

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

    def test_launchers_are_per_title(self):
        with mock.patch.dict(os.environ, {"TMNF_SUPPORT": "/private/tmp/x/support"}, clear=False):
            for name in ("TMNF_LAUNCHER", "TMNF_TITLE"):
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
                home / "Games/TMNF/Turbo/prefix/drive_c/Program Files (x86)/TrackMania Turbo"
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
                "TMNF_AUDIO": "bogus",
                "TMNF_WINDOW_SIZE": "0x0",
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

    NEW_FIELDS = {"sourceSize": (int, type(None)), "locations": dict, "sessionActive": bool}

    def test_new_fields_for_titles_and_placeholders(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_hub("snapshot", home=directory)
            data = json.loads(result.stdout)
            self.assertEqual(data["hubVersion"], (ROOT / "VERSION").read_text().strip())
            for title in data["titles"]:
                for key, kind in self.NEW_FIELDS.items():
                    self.assertIsInstance(title[key], kind, key)
                self.assertEqual(
                    set(title["locations"]), {"root", "prefix", "gameDir", "userdata", "logs"}
                )
                for value in title["locations"].values():
                    self.assertTrue(value.startswith("/"))
                self.assertEqual(
                    title["locations"]["logs"], str(Path(directory) / "Library/Logs/TMNF")
                )
                self.assertFalse(title["sessionActive"])
        placeholder = snapshot._error_placeholder("broken", "bad")
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
                (contents / ".tmnf-app").touch()
            (home / "Games/TMNF/Canyon/UserData").mkdir(parents=True)
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
            {"items": [], "saves": str(home / "Games/TMNF/Canyon/UserData")},
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
        from tmnf.hub import __main__ as hub_main

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
            self.assertTrue(command[1].endswith("src/shell/cli/tmnf"))
            self.assertEqual(command[2:], extra)

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
            logs = home / "Library/Logs/TMNF"
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
                mock.patch.dict(os.environ, {"HOME": str(home), "TMNF_TITLE": "tmu"}),
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
            (prefix / ".tmnf-prefix").write_text(title.prefix_marker)
            (prefix / "system.reg").write_text("")
            self.assertTrue(state._prefix_ready(title, prefix).ready)
            (prefix / ".tmnf-initializing").touch()
            probe = state._prefix_ready(title, prefix)
            self.assertFalse(probe.ready)
            self.assertIn("run install again", probe.note)
            # Legacy tmnf prefix (empty marker) is covered by the same check.
            (prefix / ".tmnf-prefix").write_text("")
            self.assertFalse(state._prefix_ready(title, prefix).ready)


if __name__ == "__main__":
    unittest.main()
