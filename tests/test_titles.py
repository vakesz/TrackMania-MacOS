import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from hang_guard import SUBPROCESS_TIMEOUT

ROOT = Path(__file__).resolve().parents[1]
TITLES = ROOT / "config/titles"


def load_registry():
    import sys

    sys.path.insert(0, str(ROOT / "src/python"))
    from trackmania_mac.hub import registry

    return registry


class TitleRegistryTests(unittest.TestCase):
    def test_allTitle_json_valid(self):
        # The registry module is the only schema; this test does not restate it.
        registry = load_registry()
        titles = registry.load_all(TITLES)
        self.assertEqual(
            sorted(title.id for title in titles),
            sorted(path.stem for path in TITLES.glob("*.json")),
        )
        # The one deliberate inventory: adding or removing a title is a visible change.
        self.assertEqual(
            {title.id for title in titles},
            {
                "tmnf",
                "tmu",
                "tm-original",
                "tm-sunrise",
                "tm-sunrise-extreme",
                "tm-canyon",
                "tm-turbo",
                "tmn-eswc",
                "tm-stadium",
                "tm-valley",
                "tm-lagoon",
                "tm-2020",
            },
        )

    def test_titles_load_oldest_release_first(self):
        registry = load_registry()
        titles = registry.load_all(TITLES)
        self.assertEqual(
            [title.id for title in titles],
            [title.id for title in sorted(titles, key=lambda t: (t.release_date, t.id))],
        )
        # tmnf and tmu share a release date, so the id breaks the tie.
        self.assertEqual(
            [title.id for title in titles],
            [
                "tm-original",
                "tm-sunrise",
                "tm-sunrise-extreme",
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

    def test_status_of_each_shipped_title(self):
        registry = load_registry()
        self.assertEqual(
            {title.id: title.status for title in registry.load_all(TITLES)},
            {
                "tmnf": "supported",
                "tmu": "supported",
                "tm-original": "unsupported",
                "tm-sunrise": "unsupported",
                "tm-sunrise-extreme": "provisional",
                "tm-canyon": "unsupported",
                "tm-turbo": "unsupported",
                "tmn-eswc": "provisional",
                "tm-stadium": "unsupported",
                "tm-valley": "unsupported",
                "tm-lagoon": "unsupported",
                "tm-2020": "unsupported",
            },
        )

    def test_legacy_prefix_subdirs_are_unique(self):
        subdirs = {}
        for path in sorted(TITLES.glob("*.json")):
            data = json.loads(path.read_text())
            self.assertNotIn("prefix_subdir", data, "new paths come from the id")
            if not data.get("legacy_prefix_subdir"):
                continue
            # APFS is case-insensitive: "TMNF/Canyon" and "tmnf/canyon" are one folder.
            key = data["legacy_prefix_subdir"].casefold()
            self.assertNotIn(key, subdirs, data["legacy_prefix_subdir"])
            subdirs[key] = data["id"]
        for key, title_id in subdirs.items():
            for other in subdirs:
                for area in ("prefix", "userdata"):
                    self.assertFalse(
                        key.startswith(other + "/" + area + "/") or key == other + "/" + area,
                        f"{title_id} sits inside the {area} of another title",
                    )

    def test_registry_refuses_colliding_or_nested_roots_at_load_time(self):
        import shutil
        import sys
        import tempfile

        sys.path.insert(0, str(ROOT / "src/python"))
        from trackmania_mac.hub import registry

        self.assertEqual(registry.subdir_conflicts(registry.load_all(TITLES)), {})
        for subdir in ("tmnf", "TMNF/Prefix/x", "tmnf/userdata/x", "tmnf/canyon"):
            with self.subTest(subdir=subdir), tempfile.TemporaryDirectory() as directory:
                titles = Path(directory) / "titles"
                shutil.copytree(TITLES, titles)
                data = json.loads((titles / "tm-turbo.json").read_text())
                data["legacy_prefix_subdir"] = subdir
                (titles / "tm-turbo.json").write_text(json.dumps(data))
                loaded, errors, _ = registry.load_registry(titles)
                self.assertIn("tm-turbo", errors)
                self.assertNotIn("tm-turbo", [title.id for title in loaded])
                with self.assertRaises(registry.RegistryError):
                    registry.load_all(titles)

    def test_registry_refuses_ids_that_differ_only_in_case(self):
        import sys

        sys.path.insert(0, str(ROOT / "src/python"))
        from trackmania_mac.hub import registry

        titles = registry.load_all(TITLES)
        twin = titles[0]._replace(id=titles[0].id.upper(), legacy_prefix_subdir=None)
        conflicts = registry.subdir_conflicts([*titles, twin])
        self.assertIn(twin.id, conflicts)
        self.assertIn("differs only in case", conflicts[twin.id])

    def test_a_title_without_a_legacy_root_is_valid(self):
        import shutil
        import sys
        import tempfile

        sys.path.insert(0, str(ROOT / "src/python"))
        from trackmania_mac.hub import registry

        with tempfile.TemporaryDirectory() as directory:
            titles = Path(directory) / "titles"
            shutil.copytree(TITLES, titles)
            data = json.loads((titles / "tm-turbo.json").read_text())
            del data["legacy_prefix_subdir"]
            (titles / "tm-turbo.json").write_text(json.dumps(data))
            loaded = {title.id: title for title in registry.load_all(titles)}
            self.assertIsNone(loaded["tm-turbo"].legacy_prefix_subdir)

    def test_shell_and_python_read_the_same_release_pins(self):
        """config/pins.json is the only home: shell and Python must agree on it."""
        import sys

        sys.path.insert(0, str(ROOT / "src/python"))
        from trackmania_mac.hub import registry

        pins = registry.pins()
        self.assertEqual(
            pins,
            {
                key: value
                for key, value in json.loads((ROOT / "config/pins.json").read_text()).items()
            },
        )
        names = " ".join(f'"${key.upper()}"' for key in pins)
        result = subprocess.run(
            ["/bin/bash", "-euc", f'. src/shell/lib/common.sh; printf "%s\\n" {names}'],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), list(pins.values()))
        for literal in pins.values():
            for source in (
                "src/shell/lib/common.sh",
                "src/python/trackmania_mac/hub/state.py",
                "src/python/trackmania_mac/hub/paths.py",
            ):
                self.assertNotIn(literal, (ROOT / source).read_text(), (source, literal))

    def test_modloader_layout_is_unchanged_and_defined_once(self):
        """The shell's ModLoader paths come from trackmania_mac.install.modloader, byte for byte."""
        pins = load_registry().pins()
        result = subprocess.run(
            [
                "/bin/bash",
                "-euc",
                ". src/shell/lib/common.sh; "
                'printf "%s\\n" "$TMLOADER_DIR" "$TMLOADER_EXE" "$TMCOMP_DLL" '
                '"$TMLOADER_GAME_EXE" "$TMLOADER_COREMOD_DLL" "$TMLOADER_SETTINGS" '
                '"$TMLOADER_PROFILE_FILE" "$TMLOADER_PROGRAM_ID" "$TMLOADER_PROFILE"',
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp/h", "TRACKMANIA_MAC_PREFIX": "/tmp/p"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        loader = "/tmp/p/drive_c/Program Files/TMLoader"
        database = loader + "/database/TmForever"
        self.assertEqual(
            result.stdout.splitlines(),
            [
                loader,
                loader + "/TMLoader.exe",
                "{}/products/Competition Patch/{}/TMCompPatch.dll".format(
                    database, pins["tmcomp_version"]
                ),
                "{}/products/TmForever/{}/TmForever.exe".format(
                    database, pins["tmloader_game_version"]
                ),
                "{}/products/CoreMod/{}/CoreMod.dll".format(
                    database, pins["tmloader_coremod_version"]
                ),
                database + "/products/TmForever/settings.yaml",
                database + "/profiles/competition.yaml",
                "TmForever",
                "competition",
            ],
        )
        for source in (
            "src/shell/lib/common.sh",
            "src/shell/lib/install/patch.sh",
            "src/shell/lib/commands/play.sh",
            "src/python/trackmania_mac/hub/state.py",
        ):
            self.assertNotIn("database/TmForever", (ROOT / source).read_text(), source)
            self.assertNotIn('"database"', (ROOT / source).read_text(), source)

    def test_tmnf_installer_cache_path_is_historical(self):
        result = subprocess.run(
            [
                "/bin/bash",
                "-euc",
                ". src/shell/lib/common.sh; . src/shell/lib/install/game.sh; "
                "_cache_installer_path tmnf",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "TRACKMANIA_MAC_CACHE": "/tmp/c"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "/tmp/c/tmnationsforever_setup.exe")

    def test_staged_payload_contains_pins(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-euc",
                    ". src/shell/lib/common.sh; "
                    ". src/shell/lib/apps/payload.sh; "
                    f'stage_launcher_payload "$PWD" "{directory}/p"',
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=SUBPROCESS_TIMEOUT,
                env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (Path(directory) / "p/config/pins.json").read_text(),
                (ROOT / "config/pins.json").read_text(),
            )

    def test_a_custom_root_keeps_its_saves_beside_the_prefix(self):
        script = '. src/shell/lib/common.sh; printf "%s\\n" "$TRACKMANIA_MAC_USERDATA"'
        for environment, expected in (
            ({"TRACKMANIA_MAC_ROOT": "/tmp/custom"}, "/tmp/custom/UserData"),
            ({}, "/tmp/Library/TrackManiaMacOS/saves/tmnf"),
        ):
            with self.subTest(environment=environment):
                result = subprocess.run(
                    ["/bin/bash", "-euc", script],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    timeout=SUBPROCESS_TIMEOUT,
                    env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", **environment},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_common_sh_title_defaults(self):
        result = subprocess.run(
            [
                "/bin/bash",
                "-euc",
                '. src/shell/lib/common.sh; printf "%s\\n" "$TRACKMANIA_MAC_TITLE" "$GAME_EXE" "$TRACKMANIA_MAC_ROOT"',
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        title, game_exe, root = result.stdout.splitlines()
        self.assertEqual(title, "tmnf")
        self.assertTrue(game_exe.endswith("TmNationsForever/TmForever.exe"))
        self.assertTrue(root.endswith("Library/TrackManiaMacOS/games/tmnf"))

    def test_shell_matches_title_registry(self):
        for path in sorted(TITLES.glob("*.json")):
            data = json.loads(path.read_text())
            with self.subTest(title=data["id"]):
                result = subprocess.run(
                    [
                        "/bin/bash",
                        "-euc",
                        '. src/shell/lib/common.sh; printf "%s\\n" "$GAME_EXE" '
                        '"$LAUNCHER_EXE" "$TRACKMANIA_MAC_ROOT" "$TRACKMANIA_MAC_APP" "$TRACKMANIA_MAC_SETTINGS_APP" '
                        '"$TRACKMANIA_MAC_USERDATA" "$TRACKMANIA_MAC_SUPPORT" "$TRACKMANIA_MAC_CACHE"',
                    ],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    timeout=SUBPROCESS_TIMEOUT,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "HOME": "/tmp",
                        "TRACKMANIA_MAC_TITLE": data["id"],
                    },
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                lines = result.stdout.splitlines()
                game_exe, launcher_exe, root, app, settings_app = lines[:5]
                userdata, support, cache = lines[5:]
                data_dir = "/tmp/Library/TrackManiaMacOS"
                self.assertEqual(root, "{}/games/{}".format(data_dir, data["id"]))
                self.assertEqual(userdata, "{}/saves/{}".format(data_dir, data["id"]))
                self.assertEqual(support, data_dir)
                self.assertEqual(cache, data_dir + "/cache")
                game_dir = "drive_c/Program Files (x86)/{}".format(data["game_dir_name"])
                self.assertTrue(game_exe.endswith(game_dir + "/" + data["game_exe"]), game_exe)
                self.assertTrue(
                    launcher_exe.endswith(game_dir + "/" + data["launcher_exe"]), launcher_exe
                )
                self.assertTrue(app.endswith("/" + data["app_name"]), app)
                self.assertTrue(
                    settings_app.endswith("/" + data["settings_app_name"]), settings_app
                )

    def test_shell_and_hub_agree_on_custom_app_names(self):
        registry = load_registry()
        from trackmania_mac.hub import paths as hub_paths

        script = '. src/shell/lib/common.sh; printf "%s\\n" "$TRACKMANIA_MAC_APP" "$TRACKMANIA_MAC_SETTINGS_APP"'
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            home = Path(directory)
            apps = home / "Applications"
            root = home / "Library/TrackManiaMacOS/games/tmu"
            root.mkdir(parents=True)
            env = {
                "PATH": "/usr/bin:/bin",
                "HOME": str(home),
                "TRACKMANIA_MAC_TITLE": "tmu",
                "TRACKMANIA_MAC_APPS_DIR": str(apps),
            }
            title = registry.get("tmu", TITLES)
            for content, expected in (
                ("My TMU\n", ("My TMU.app", "My TMU Settings.app")),
                ("bad/name\n", (title.app_name, title.settings_app_name)),
            ):
                with self.subTest(content=content):
                    (root / "app-name").write_text(content)
                    result = subprocess.run(
                        ["/bin/bash", "-euc", script],
                        cwd=ROOT,
                        text=True,
                        capture_output=True,
                        timeout=SUBPROCESS_TIMEOUT,
                        env=env,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    shell = tuple(result.stdout.splitlines())
                    self.assertEqual(shell, tuple(str(apps / name) for name in expected))
                    with mock.patch.dict(os.environ, env):
                        computed = hub_paths.compute(title)
                    self.assertEqual(shell, (str(computed.app), str(computed.settings_app)))
            # An app path given explicitly turns the file off.
            result = subprocess.run(
                ["/bin/bash", "-euc", script],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=SUBPROCESS_TIMEOUT,
                env=dict(env, TRACKMANIA_MAC_APP=str(home / "Game.app")),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [str(home / "Game.app"), str(home / title.settings_app_name)],
            )

    def test_tmnf_source_override_does_not_change_other_title_cache_paths(self):
        result = subprocess.run(
            [
                "/bin/bash",
                "-euc",
                ". src/shell/lib/common.sh; . src/shell/lib/install/game.sh; "
                "_cache_installer_path tmu",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/tmp",
                "TRACKMANIA_MAC_TITLE": "tmu",
                "TRACKMANIA_MAC_CACHE": "/tmp/trackmania-mac-cache",
                "TRACKMANIA_MAC_SOURCE_URL": "https://example.invalid/override.exe",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(), "/tmp/trackmania-mac-cache/games/tmu/tmunitedforever_setup.exe"
        )

    def test_wrapped_inno_installers_target_the_registry_game_dir(self):
        """Every online installer kind, and every update it runs, gets /DIR equal to GAME_DIR."""
        stubs = (
            "download_pinned_installer() { :; }; "
            'unpack_inner_inno() { printf "/stub/setup.exe\\n"; }; '
            "run_wine() { :; }; "
            'run_released_installer() { printf "%s\\n" "$TRACKMANIA_MAC_INNO_DIR"; TRACKMANIA_MAC_INSTALLER_PID=0; }; '
            'run_interactive_installer() { printf "%s\\n" "$TRACKMANIA_MAC_INNO_DIR"; TRACKMANIA_MAC_INSTALLER_PID=0; mkdir -p "${GAME_EXE%/*}"; : >"$GAME_EXE"; }; '
            "download_title_extras() { :; }; stop_prefix_processes() { :; }; apply_overlay() { :; }; apply_nx_compat() { :; }; "
            "apply_game_exe_patch() { :; }; "
            'write_patched_setup() { printf "/stub/patched.exe\\n"; }; '
            "seed_game_system_config() { :; }; "
            "verify_file() { :; }; "
            "wait_inno_setup() { :; }; verify_installed_game() { :; }; say() { :; }; "
        )
        for path in sorted(TITLES.glob("*.json")):
            data = json.loads(path.read_text())
            if data["installer"] not in ("online", "online-retail", "online-trial"):
                continue
            with (
                self.subTest(title=data["id"]),
                tempfile.TemporaryDirectory(prefix="trackmania-mac-dir-") as home,
            ):
                result = subprocess.run(
                    [
                        "/bin/bash",
                        "-euc",
                        ". src/shell/lib/common.sh; . src/shell/lib/install/game.sh; "
                        + stubs
                        + "install_game",
                    ],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    timeout=SUBPROCESS_TIMEOUT,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "HOME": home,
                        "TRACKMANIA_MAC_TITLE": data["id"],
                    },
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                runs = 1 + len(data.get("updates", []))
                self.assertEqual(
                    result.stdout.splitlines(),
                    ["C:\\Program Files (x86)\\" + data["game_dir_name"]] * runs,
                )

    def run_eswc_install(self, home, write_patched_setup):
        """install_game for tmn-eswc up to the setup; prints each setup path it runs."""
        stubs = (
            "download_pinned_installer() { :; }; download_title_extras() { :; }; "
            'run_interactive_installer() { printf "run %s\\n" "$1"; TRACKMANIA_MAC_INSTALLER_PID=0; '
            'mkdir -p "${GAME_EXE%/*}"; : >"$GAME_EXE"; }; '
            "wait_inno_setup() { :; }; "
            'stop_prefix_processes() { printf "stopped\\n"; }; '
            # The first step after the setup reports whether the copy is still there.
            'apply_official_updates() { ls "${TRACKMANIA_MAC_CACHE}/games/tmn-eswc"; exit 0; }; '
            "say() { :; }; "
        )
        return subprocess.run(
            [
                "/bin/bash",
                "-euc",
                ". src/shell/lib/common.sh; . src/shell/lib/install/game.sh; "
                + stubs
                + write_patched_setup
                + "install_game",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT,
            env={"PATH": "/usr/bin:/bin", "HOME": home, "TRACKMANIA_MAC_TITLE": "tmn-eswc"},
        )

    def test_a_patched_setup_runs_from_a_copy_that_is_removed(self):
        """source_patch: the wizard runs the copy, which is gone when the setup ends."""
        cache = "Library/TrackManiaMacOS/cache/games/tmn-eswc"
        staging = cache + "/TmNationsESWC_Setup.inner"
        writer = (
            'write_patched_setup() { mkdir -p "$2"; : >"$2/copy.exe"; '
            'test -f "$1" || return 1; printf "%s\\n" "$2/copy.exe"; }; '
        )
        with tempfile.TemporaryDirectory(prefix="trackmania-mac-dir-") as home:
            (Path(home) / cache).mkdir(parents=True)
            (Path(home) / cache / "TmNationsESWC_Setup.exe").write_bytes(b"download")
            result = self.run_eswc_install(home, writer)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                ["run {}/{}/copy.exe".format(home, staging), "stopped", "TmNationsESWC_Setup.exe"],
            )
            self.assertFalse((Path(home) / staging).exists())
            self.assertEqual(
                (Path(home) / cache / "TmNationsESWC_Setup.exe").read_bytes(), b"download"
            )

    def test_a_setup_that_cannot_be_patched_is_not_run(self):
        cache = "Library/TrackManiaMacOS/cache/games/tmn-eswc"
        staging = cache + "/TmNationsESWC_Setup.inner"
        writer = (
            'write_patched_setup() { mkdir -p "$2"; : >"$2/partial"; '
            'die "Could not prepare the setup."; }; '
        )
        with tempfile.TemporaryDirectory(prefix="trackmania-mac-dir-") as home:
            (Path(home) / cache).mkdir(parents=True)
            result = self.run_eswc_install(home, writer)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Could not prepare the setup.", result.stderr)
            self.assertNotIn("run ", result.stdout)
            self.assertFalse((Path(home) / staging).exists())

    def test_prefix_marker_is_derived_and_exported(self):
        for path in sorted(TITLES.glob("*.json")):
            with self.subTest(title=path.stem):
                result = subprocess.run(
                    [
                        "/bin/bash",
                        "-euc",
                        '. src/shell/lib/common.sh; printf "%s" "$TRACKMANIA_MAC_TITLE_PREFIX_MARKER"',
                    ],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    timeout=SUBPROCESS_TIMEOUT,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "HOME": "/tmp",
                        "TRACKMANIA_MAC_TITLE": path.stem,
                    },
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, path.stem + ":1")
        registry = load_registry()
        self.assertEqual(registry.get("tmu", TITLES).prefix_marker, "tmu:1")

    def test_components_per_title(self):
        registry = load_registry()
        components = {title.id: title.components for title in registry.load_all(TITLES)}
        self.assertEqual(components["tmnf"], ("competition-patch", "openal", "system-config"))
        self.assertEqual(components["tmu"], ("competition-patch", "openal", "system-config"))
        self.assertEqual(
            components["tmn-eswc"],
            (
                "source-patch",
                "updates",
                "overlay",
                "nx-compat",
                "game-exe-patch",
                "game-saves",
                "game-system-config",
            ),
        )
        for title_id in ("tm-canyon", "tm-turbo", "tm-original", "tm-sunrise"):
            self.assertEqual(components[title_id], (), title_id)
        self.assertEqual(
            components["tm-sunrise-extreme"], ("updates", "overlay", "nx-compat", "game-saves")
        )

    def test_layered_downloads_are_exported(self):
        registry = load_registry()
        names = (
            "TRACKMANIA_MAC_TITLE_SOURCE_INTERACTIVE",
            "TRACKMANIA_MAC_TITLE_UPDATE_COUNT",
            "TRACKMANIA_MAC_TITLE_OVERLAY_URL",
            "TRACKMANIA_MAC_TITLE_OVERLAY_SHA256",
            "TRACKMANIA_MAC_TITLE_OVERLAY_SIZE",
            "TRACKMANIA_MAC_TITLE_OVERLAY_REMOVE",
            "TRACKMANIA_MAC_TITLE_OVERLAY_ROOT",
            "TRACKMANIA_MAC_TITLE_OVERLAY_SKIP",
            "TRACKMANIA_MAC_TITLE_NX_COMPAT",
        )
        for title in registry.load_all(TITLES):
            with self.subTest(title=title.id):
                script = (
                    ". src/shell/lib/common.sh; "
                    + 'printf "%s\\0" '
                    + " ".join('"${}"'.format(name) for name in names)
                )
                script += "; i=1; while test $i -le $TRACKMANIA_MAC_TITLE_UPDATE_COUNT; do "
                script += 'for f in URL SHA256 SIZE; do v="TRACKMANIA_MAC_TITLE_UPDATE_${i}_$f"; '
                script += 'printf "%s\\0" "${!v}"; done; i=$((i + 1)); done'
                result = subprocess.run(
                    ["/bin/bash", "-euc", script],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    timeout=SUBPROCESS_TIMEOUT,
                    env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "TRACKMANIA_MAC_TITLE": title.id},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                values = result.stdout.split("\0")[:-1]
                overlay = title.overlay
                expected = [
                    "true" if title.source_interactive else "",
                    str(len(title.updates)),
                    overlay.url if overlay else "",
                    overlay.sha256 if overlay else "",
                    str(overlay.size) if overlay else "",
                    " ".join(overlay.remove) if overlay else "",
                    overlay.root if overlay else "",
                    " ".join(overlay.skip) if overlay else "",
                    " ".join(title.nx_compat),
                ]
                for update in title.updates:
                    expected += [update.url, update.sha256, str(update.size)]
                self.assertEqual(values, expected)

    def test_shell_components_match_python(self):
        registry = load_registry()
        for title in registry.load_all(TITLES):
            with self.subTest(title=title.id):
                checks = "; ".join(
                    "if title_has {0}; then echo {0}; fi".format(name)
                    for name in registry.COMPONENTS
                )
                result = subprocess.run(
                    ["/bin/bash", "-euc", ". src/shell/lib/common.sh; " + checks],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    timeout=SUBPROCESS_TIMEOUT,
                    env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "TRACKMANIA_MAC_TITLE": title.id},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(tuple(result.stdout.split()), title.components)

    def test_title_has_rejects_unknown_components(self):
        result = subprocess.run(
            ["/bin/bash", "-euc", ". src/shell/lib/common.sh; title_has competition_patch"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "TRACKMANIA_MAC_TITLE": "tmnf"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown title component", result.stderr)

    def test_user_subdir_export_matches_each_title(self):
        for path in sorted(TITLES.glob("*.json")):
            data = json.loads(path.read_text())
            with self.subTest(title=data["id"]):
                result = subprocess.run(
                    [
                        "/bin/bash",
                        "-euc",
                        '. src/shell/lib/common.sh; printf "%s" "$TRACKMANIA_MAC_TITLE_USER_SUBDIR"',
                    ],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    timeout=SUBPROCESS_TIMEOUT,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "HOME": "/tmp/trackmania-mac-no-home",
                        "TRACKMANIA_MAC_TITLE": data["id"],
                    },
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, data["user_subdir"])

    def test_save_dirs_are_declared_for_every_guarded_name(self):
        """managed_paths.save_dir_names() must stay a superset of the registry."""
        import sys

        sys.path.insert(0, str(ROOT / "src/python"))
        from trackmania_mac.guard.managed_paths import save_dir_names

        guarded = set(save_dir_names())
        for path in sorted(TITLES.glob("*.json")):
            data = json.loads(path.read_text())
            with self.subTest(title=data["id"]):
                for name in data["save_dirs"]:
                    self.assertIn(name, guarded, name)

    def test_cli_titles_lists_all(self):
        result = subprocess.run(
            ["/bin/bash", str(ROOT / "src/shell/cli/trackmania-mac"), "titles"],
            text=True,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in sorted(TITLES.glob("*.json")):
            self.assertIn(path.stem, result.stdout)

    def test_unknown_title_rejected(self):
        result = subprocess.run(
            ["/bin/bash", "-euc", ". src/shell/lib/common.sh; validate_launch_options"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "TRACKMANIA_MAC_TITLE": "nope"},
        )
        self.assertNotEqual(result.returncode, 0)


# Title facts belong in config/titles/*.json. These patterns catch code that
# branches on a title id instead; only the legacy migration shims may.
TITLE_CONDITIONALS = (
    re.compile(r'"?\$\{?TRACKMANIA_MAC_TITLE\}?"?\s+!?=\s'),
    re.compile(r'\btest\s+"?\$\{?\w+\}?"?\s+!?=\s+"?tmnf"?(\s|;|$)'),
    re.compile(r'case\s+"?\$\{?TRACKMANIA_MAC_TITLE\}?"?\s+in'),
    re.compile(r'\btitle(_id|\.id)\s*[!=]=\s*["\']'),
    re.compile(r"\btitle(_id|\.id)\s+(not\s+)?in\s*[(\[{]"),
    re.compile(r'\$\{?\w+\}?"?\s*!?==?\s*"?tmnf"?\s*\]\]'),
    re.compile(r"NEEDS_TMLOADER"),
    re.compile(r"\bopenal_applies\b"),
    re.compile(r"TmNationsForever"),
)
TITLE_CONDITIONAL_ALLOWLIST = (
    # A pre-marker TMNF prefix from before multi-title support.
    (
        "src/python/trackmania_mac/hub/state.py",
        'if not identity and title.id == "tmnf" and (prefix / "system.reg").is_file():',
    ),
    ("src/python/trackmania_mac/hub/state.py", 'if not identity and title.id == "tmnf":'),
    # The single-title installer cache location existing downloads still use;
    # uninstall mirrors it and TMNF's unprefixed log names.
    ("src/shell/lib/titles.sh", 'if test "$title" = tmnf; then'),
    ("src/python/trackmania_mac/guard/uninstall.py", 'if title.id == "tmnf":'),
    ("src/python/trackmania_mac/guard/uninstall.py", 'if title_id != "tmnf":'),
    # The shared pre-multi-title launcher only TMNF ever installed.
    ("src/shell/lib/apps/wrappers.sh", 'test "$TRACKMANIA_MAC_TITLE" = tmnf || return 0'),
    # TMNF's app logs kept their pre-multi-title names (play.log, configure.log).
    (
        "src/shell/lib/apps/wrappers.sh",
        'test "$TRACKMANIA_MAC_TITLE" = tmnf || log_name="${TRACKMANIA_MAC_TITLE}-${action}.log"',
    ),
    ("src/python/trackmania_mac/hub/diagnostics.py", 'if title_id == "tmnf":'),
)


def source_files():
    for path in sorted((ROOT / "src/shell").rglob("*")):
        if path.is_file() and (path.suffix == ".sh" or path.parent.name == "cli"):
            yield path
    yield from sorted((ROOT / "src/python/trackmania_mac").rglob("*.py"))


class TitleConditionalTests(unittest.TestCase):
    def test_no_title_id_conditionals(self):
        allowed = {(file, line) for file, line in TITLE_CONDITIONAL_ALLOWLIST}
        found = []
        for path in source_files():
            relative = str(path.relative_to(ROOT))
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if any(pattern.search(line) for pattern in TITLE_CONDITIONALS):
                    if (relative, line.strip()) not in allowed:
                        found.append(f"{relative}:{number}: {line.strip()}")
        self.assertEqual(found, [], "move the title fact into config/titles/*.json")

    def test_allowlist_entries_still_exist(self):
        for file, line in TITLE_CONDITIONAL_ALLOWLIST:
            with self.subTest(file=file, line=line):
                lines = [item.strip() for item in (ROOT / file).read_text().splitlines()]
                self.assertIn(line, lines, "remove the stale allowlist entry")
                self.assertTrue(any(pattern.search(line) for pattern in TITLE_CONDITIONALS))

    def test_patterns_catch_common_spellings(self):
        for line in (
            'if test "$TRACKMANIA_MAC_TITLE" = tmu; then',
            "if [[ $TRACKMANIA_MAC_TITLE == tmnf ]]; then",
            'case "$TRACKMANIA_MAC_TITLE" in',
            'if title.id == "tmu":',
            'if title.id in ("tmnf", "tmu"):',
            "if title_id not in {'tmnf'}:",
        ):
            with self.subTest(line=line):
                self.assertTrue(any(pattern.search(line) for pattern in TITLE_CONDITIONALS))


class RegistryValidationTests(unittest.TestCase):
    def setUp(self):
        import sys

        sys.path.insert(0, str(ROOT / "src/python"))
        from trackmania_mac.hub import registry

        self.registry = registry
        self.data = json.loads((TITLES / "tmu.json").read_text())

    def validate(self, **changes):
        data = dict(self.data, **changes)
        return self.registry._validate(data, Path("/x/tmu.json"))

    def test_shipped_entry_is_valid(self):
        self.validate()

    def test_unknown_fields_are_rejected(self):
        with self.assertRaisesRegex(self.registry.RegistryError, "unknown fields: user_subdr"):
            self.validate(user_subdr="TrackMania")

    def test_title_fields_match_schema_table(self):
        self.assertEqual(
            self.registry.Title._fields, tuple(field.name for field in self.registry._FIELDS)
        )

    def test_optional_fields_take_their_defaults(self):
        data = {
            key: value
            for key, value in self.data.items()
            if key not in ("notes", "source_size", "source_url", "source_sha256")
        }
        data["installer"] = "steam-copy"
        title = self.registry._validate(data, Path("/x/tmu.json"))
        self.assertEqual(
            (title.notes, title.source_url, title.source_sha256, title.source_size),
            ("", None, None, None),
        )
        self.assertEqual((title.source_interactive, title.updates, title.overlay), (None, (), None))
        self.assertEqual(title.nx_compat, ())
        self.assertEqual(title.game_save_dirs, ())
        self.assertIsNone(title.game_system_config)

    def test_release_pins_are_optional_but_consistent(self):
        sha = "0" * 64
        title = self.validate(game_version="1.0", game_exe_sha256=sha, creative_openal_sha256=sha)
        self.assertEqual((title.game_version, title.game_exe_sha256), ("1.0", sha))
        self.assertIsNone(self.validate().game_exe_sha256)
        for changes in (
            {"game_exe_sha256": sha},
            {"game_version": "1.0", "game_exe_sha256": "ABC"},
            {"creative_openal_sha256": "x" * 64},
            {"game_version": "1.0; rm"},
        ):
            with self.subTest(changes=changes), self.assertRaises(self.registry.RegistryError):
                self.validate(**changes)

    def test_status_must_be_known(self):
        for status in ("supported", "provisional", "unsupported"):
            self.assertEqual(self.validate(status=status).status, status)
        for status in ("beta", "", "Supported", None):
            with self.subTest(status=status), self.assertRaises(self.registry.RegistryError):
                self.validate(status=status)

    def test_release_date_is_a_required_real_iso_date(self):
        self.assertEqual(self.validate(release_date="2008-02-29").release_date, "2008-02-29")
        for value in (
            "2007-02-29",
            "2008-13-01",
            "2008-4-16",
            "16/04/2008",
            "20080416",
            "2008-04-16T00:00",
            " 2008-04-16",
            "",
            None,
            2008,
        ):
            with self.subTest(value=value), self.assertRaises(self.registry.RegistryError):
                self.validate(release_date=value)
        missing = {key: value for key, value in self.data.items() if key != "release_date"}
        with self.assertRaisesRegex(self.registry.RegistryError, "missing fields: release_date"):
            self.registry._validate(missing, Path("/x/tmu.json"))

    def test_link_is_optional_and_needs_both_fields(self):
        title = self.validate()
        self.assertEqual((title.link_url, title.link_label), (None, None))
        url = "https://example.test/port"
        title = self.validate(link_url=url, link_label="A port")
        self.assertEqual((title.link_url, title.link_label), (url, "A port"))
        for changes in (
            {"link_url": url},
            {"link_label": "A port"},
            {"link_url": "http://example.test/port", "link_label": "A port"},
            {"link_url": "file:///etc/passwd", "link_label": "A port"},
            {"link_url": url, "link_label": "a\nb"},
            {"link_url": url, "link_label": " padded"},
            {"link_url": url, "link_label": "x" * 61},
            {"link_url": url, "link_label": 5},
        ):
            with self.subTest(changes=changes), self.assertRaises(self.registry.RegistryError):
                self.validate(**changes)

    def test_cd_key_gate_is_a_registry_fact_of_serial_titles(self):
        self.assertTrue(self.validate().cd_key_in_nadeo_ini)
        data = {key: value for key, value in self.data.items() if key != "cd_key_in_nadeo_ini"}
        self.assertIsNone(self.registry._validate(data, Path("/x/tmu.json")).cd_key_in_nadeo_ini)
        with self.assertRaisesRegex(self.registry.RegistryError, "requires requires_serial"):
            self.validate(requires_serial=False)
        with self.assertRaises(self.registry.RegistryError):
            self.validate(cd_key_in_nadeo_ini="yes")

    def test_cd_key_in_system_config_is_a_registry_fact_of_serial_titles(self):
        data = {key: value for key, value in self.data.items() if key != "cd_key_in_nadeo_ini"}
        data["cd_key_in_system_config"] = True
        title = self.registry._validate(data, Path("/x/tmu.json"))
        self.assertTrue(title.cd_key_in_system_config)
        self.assertIsNone(title.cd_key_in_nadeo_ini)
        self.assertIsNone(self.validate().cd_key_in_system_config)
        with self.assertRaisesRegex(self.registry.RegistryError, "requires requires_serial"):
            self.registry._validate(dict(data, requires_serial=False), Path("/x/tmu.json"))
        with self.assertRaisesRegex(self.registry.RegistryError, "exclusive"):
            self.validate(cd_key_in_system_config=True)
        seeded = dict(
            data,
            installer="online",
            game_version="1.0",
            game_exe_sha256="0" * 64,
            game_system_config={"format": "text-2006", "file": "Default.SystemConfig.Gbx"},
        )
        with self.assertRaisesRegex(
            self.registry.RegistryError, "game_system_config are exclusive"
        ):
            self.registry._validate(seeded, Path("/x/tmu.json"))
        with self.assertRaises(self.registry.RegistryError):
            self.registry._validate(dict(data, cd_key_in_system_config=1), Path("/x/tmu.json"))

    def test_online_installer_requires_an_exe_pin(self):
        with self.assertRaisesRegex(self.registry.RegistryError, "requires game_exe_sha256"):
            self.validate(installer="online")
        self.validate(installer="online", game_version="1.0", game_exe_sha256="0" * 64)

    def layered(self, **changes):
        """An online title with one update and an overlay, plus CHANGES."""
        base = dict(
            installer="online",
            game_version="1.0",
            game_exe_sha256="0" * 64,
            source_interactive=True,
            updates=[{"url": "https://example.com/u/update.exe", "sha256": "1" * 64, "size": 5}],
            overlay={
                "url": "https://example.com/o/overlay.tar.gz",
                "sha256": "2" * 64,
                "size": 7,
                "remove": ["old.exe", "old.dat"],
            },
        )
        base.update(changes)
        return self.validate(**base)

    def test_layered_install_fields_are_parsed(self):
        title = self.layered()
        self.assertTrue(title.source_interactive)
        self.assertEqual(
            title.updates,
            (self.registry.Download("https://example.com/u/update.exe", "1" * 64, 5),),
        )
        self.assertEqual(
            title.overlay,
            self.registry.Overlay(
                "https://example.com/o/overlay.tar.gz", "2" * 64, 7, ("old.exe", "old.dat"), "", ()
            ),
        )
        self.assertEqual(title.components[-2:], ("updates", "overlay"))
        self.assertEqual(
            [extra.url for extra in title.extra_downloads],
            ["https://example.com/u/update.exe", "https://example.com/o/overlay.tar.gz"],
        )
        self.assertEqual(title.download_size, (title.source_size or 0) + 12)
        overlay = dict(url="https://example.com/o/overlay.tar.gz", sha256="2" * 64, size=7)
        self.assertEqual(self.layered(overlay=overlay).overlay.remove, ())
        nested = self.layered(
            overlay=dict(overlay, root="Windows 10 patch", skip=["d3d9.dll", "Docs/readme.txt"])
        )
        self.assertEqual(nested.overlay.root, "Windows 10 patch")
        self.assertEqual(nested.overlay.skip, ("d3d9.dll", "Docs/readme.txt"))
        names = dict(self.registry.shell_assignments(nested))
        self.assertEqual(names["TRACKMANIA_MAC_TITLE_OVERLAY_ROOT"], "Windows 10 patch")
        self.assertEqual(names["TRACKMANIA_MAC_TITLE_OVERLAY_SKIP"], "d3d9.dll Docs/readme.txt")
        self.assertIsNone(self.layered(source_size=None).download_size)

    def test_layered_install_fields_are_validated(self):
        good = {"url": "https://example.com/u/update.exe", "sha256": "1" * 64, "size": 5}
        bad_downloads = (
            [],
            "https://example.com/u/update.exe",
            [dict(good, url="http://example.com/u/update.exe")],
            [dict(good, url="https://example.com/u/update.exe?x=1")],
            [dict(good, url="https://example.com/u/")],
            [dict(good, url="https://example.com/u/a%20b.exe")],
            [dict(good, sha256="A" * 64)],
            [dict(good, size=None)],
            [dict(good, size=True)],
            [dict(good, size=0)],
            [{"url": good["url"], "sha256": good["sha256"]}],
            [dict(good, extra=1)],
            [dict(good, remove=[])],
        )
        for updates in bad_downloads:
            with (
                self.subTest(updates=updates),
                self.assertRaises(self.registry.RegistryError),
            ):
                self.layered(updates=updates)
        overlay = {"url": "https://example.com/o/overlay.tar.gz", "sha256": "2" * 64, "size": 7}
        for remove in (
            "old.exe",
            ["a/b"],
            ["a b"],
            ["*.dll"],
            [".hidden"],
            ["..", "x"],
            [""],
            ["old.exe", "OLD.EXE"],
            [5],
        ):
            with (
                self.subTest(remove=remove),
                self.assertRaises(self.registry.RegistryError),
            ):
                self.layered(overlay=dict(overlay, remove=remove))
        with self.assertRaisesRegex(self.registry.RegistryError, "must not list game_exe"):
            self.layered(overlay=dict(overlay, remove=[self.data["game_exe"].upper()]))
        with self.assertRaisesRegex(self.registry.RegistryError, "skip must not list game_exe"):
            self.layered(overlay=dict(overlay, skip=[self.data["game_exe"].lower()]))
        for root in ("../x", "/abs", "a//b", " lead", "a/", 5, "a\\b"):
            with (
                self.subTest(root=root),
                self.assertRaisesRegex(self.registry.RegistryError, "root must be"),
            ):
                self.layered(overlay=dict(overlay, root=root))
        for skip in ("d3d9.dll", ["a b.txt"], ["../x"], ["a//b"], [""], ["a.dll", "A.DLL"], [5]):
            with (
                self.subTest(skip=skip),
                self.assertRaisesRegex(self.registry.RegistryError, "skip"),
            ):
                self.layered(overlay=dict(overlay, skip=skip))
        with self.assertRaises(self.registry.RegistryError):
            self.layered(overlay=None)
        with self.assertRaises(self.registry.RegistryError):
            self.layered(source_interactive="yes")

    def retail(self, **changes):
        """A disk-image title (no pinned installer) with CHANGES."""
        data = {
            key: value
            for key, value in self.data.items()
            if key not in ("source_url", "source_sha256", "source_size", "creative_openal_sha256")
        }
        data.update(installer="disk-image", needs_tmloader=False, game_exe="Game.exe")
        data.update(changes)
        return self.registry._validate(data, Path("/x/tmu.json"))

    def test_retail_titles_may_carry_official_updates(self):
        updates = [
            {"url": "https://example.com/u/update.exe", "sha256": "1" * 64, "size": 5},
            {"url": "https://example.com/u/patch.exe", "sha256": "2" * 64, "size": 7},
        ]
        title = self.retail(updates=updates)
        self.assertEqual(title.components, ("updates",))
        self.assertEqual([update.size for update in title.updates], [5, 7])
        self.assertEqual(title.download_size, 12)
        self.assertFalse(title.downloads)
        self.assertIsNone(self.retail().download_size)
        names = dict(self.registry.shell_assignments(title))
        self.assertEqual(names["TRACKMANIA_MAC_TITLE_UPDATE_COUNT"], "2")
        self.assertEqual(
            names["TRACKMANIA_MAC_TITLE_UPDATE_2_URL"], "https://example.com/u/patch.exe"
        )
        # The update names still share the title's cache folder.
        with self.assertRaisesRegex(self.registry.RegistryError, "distinct file names"):
            self.retail(updates=[updates[0], dict(updates[1], url=updates[0]["url"])])

    def test_updates_need_a_kind_whose_flow_runs_them(self):
        update = {"url": "https://example.com/u/update.exe", "sha256": "1" * 64, "size": 5}
        for installer in ("steam-copy", "online-retail", "online-trial"):
            with (
                self.subTest(installer=installer),
                self.assertRaisesRegex(
                    self.registry.RegistryError,
                    "updates requires installer kind online or disk-image",
                ),
            ):
                if installer == "steam-copy":
                    self.retail(installer=installer, updates=[update])
                else:
                    self.validate(installer=installer, updates=[update])

    def test_retail_titles_may_carry_an_overlay_and_the_nx_flag(self):
        overlay = {"url": "https://example.com/o/overlay.zip", "sha256": "2" * 64, "size": 7}
        title = self.retail(overlay=overlay, nx_compat=["Game.exe"])
        self.assertEqual(title.components, ("overlay", "nx-compat"))
        for installer in ("steam-copy", "online-retail", "online-trial"):
            for changes in ({"overlay": overlay}, {"nx_compat": ["Game.exe"]}):
                with (
                    self.subTest(installer=installer, changes=changes),
                    self.assertRaisesRegex(
                        self.registry.RegistryError, "requires installer kind online or disk-image"
                    ),
                ):
                    if installer == "steam-copy":
                        self.retail(installer=installer, **changes)
                    else:
                        self.validate(installer=installer, **changes)

    def test_retail_titles_get_no_other_layered_steps(self):
        for changes in (
            {"source_interactive": True},
            {"game_system_config": {"format": "text-2006", "file": "Default.SystemConfig.Gbx"}},
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(
                    self.registry.RegistryError, "requires installer kind online"
                ),
            ):
                self.retail(**changes)

    def test_nx_compat_is_a_list_of_plain_file_names(self):
        title = self.layered(nx_compat=["a.exe", "B.dll"])
        self.assertEqual(title.nx_compat, ("a.exe", "B.dll"))
        self.assertEqual(title.components[-1], "nx-compat")
        self.assertNotIn("nx-compat", self.layered().components)
        for value in (
            [],
            "a.exe",
            ["a/b.dll"],
            ["a b.dll"],
            ["*.dll"],
            [".hidden"],
            ["..", "x"],
            [""],
            [5],
            ["a.dll", "A.DLL"],
            ["a.dll\n"],
        ):
            with self.subTest(value=value), self.assertRaises(self.registry.RegistryError):
                self.layered(nx_compat=value)

    def test_game_save_dirs_are_relative_folder_paths(self):
        title = self.validate(game_save_dirs=["GameData/Profiles", "GameData/Tracks/My Replays"])
        self.assertEqual(title.game_save_dirs, ("GameData/Profiles", "GameData/Tracks/My Replays"))
        self.assertEqual(title.components[-1], "game-saves")
        self.assertNotIn("game-saves", self.validate().components)
        for value in (
            [],
            "GameData/Profiles",
            [5],
            [""],
            ["/GameData"],
            ["GameData/"],
            ["GameData//Profiles"],
            ["GameData/../Profiles"],
            ["./GameData"],
            ["~/GameData"],
            ["GameData\\Profiles"],
            ["GameData/Pro\nfiles"],
            ["GameData/Profiles\n"],
            ["GameData/ Profiles"],
            ["GameData/Profiles", "gamedata/PROFILES"],
            ["GameData", "GameData/Profiles"],
            ["GameData/Profiles/Player", "gamedata/profiles"],
        ):
            with self.subTest(value=value), self.assertRaises(self.registry.RegistryError):
                self.validate(game_save_dirs=value)

    def test_game_save_dirs_never_lie_under_a_game_file(self):
        for name in (self.data["game_exe"], self.data["launcher_exe"].upper()):
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(self.registry.RegistryError, "under a game file"),
            ):
                self.validate(game_save_dirs=[name + "/Saves"])
        with self.assertRaisesRegex(self.registry.RegistryError, "under a game file"):
            self.layered(game_save_dirs=["old.dat"])
        with self.assertRaisesRegex(self.registry.RegistryError, "under a game file"):
            self.layered(nx_compat=["a.dll"], game_save_dirs=["A.DLL/x"])

    def test_game_save_dirs_are_not_exported_to_the_shell(self):
        title = self.validate(game_save_dirs=["GameData/My Replays"])
        pairs = dict(self.registry.shell_assignments(title))
        self.assertNotIn("TRACKMANIA_MAC_TITLE_GAME_SAVE_DIRS", pairs)
        self.assertEqual(pairs["TRACKMANIA_MAC_TITLE_COMPONENTS"].split()[-1], "game-saves")

    def test_game_system_config_names_a_known_format_and_a_plain_file(self):
        good = {"format": "text-2006", "file": "Default.SystemConfig.Gbx"}
        title = self.layered(game_system_config=good)
        self.assertEqual(
            title.game_system_config,
            self.registry.GameSystemConfig("text-2006", "Default.SystemConfig.Gbx"),
        )
        self.assertEqual(title.components[-1], "game-system-config")
        self.assertNotIn("game-system-config", self.layered().components)
        for value in (
            "Default.SystemConfig.Gbx",
            [],
            {},
            {"file": "a.Gbx"},
            {"format": "text-2006"},
            dict(good, extra=1),
            dict(good, format="binary-2008"),
            dict(good, format=None),
            dict(good, file="Config/a.Gbx"),
            dict(good, file="../a.Gbx"),
            dict(good, file=".hidden"),
            dict(good, file="a b.Gbx"),
            dict(good, file="a.Gbx\n"),
            dict(good, file=5),
        ):
            with self.subTest(value=value), self.assertRaises(self.registry.RegistryError):
                self.layered(game_system_config=value)

    def test_game_system_config_is_its_own_file_and_needs_the_online_kind(self):
        good = {"format": "text-2006", "file": "Default.SystemConfig.Gbx"}
        with self.assertRaisesRegex(self.registry.RegistryError, "requires installer kind"):
            self.validate(game_system_config=good)
        for file, extra in (
            (self.data["game_exe"].lower(), {}),
            (self.data["launcher_exe"], {}),
            ("OLD.DAT", {}),
            ("a.dll", {"nx_compat": ["A.dll"]}),
        ):
            with (
                self.subTest(file=file),
                self.assertRaisesRegex(self.registry.RegistryError, "file install changes"),
            ):
                self.layered(game_system_config=dict(good, file=file), **extra)
        with self.assertRaisesRegex(self.registry.RegistryError, "under a game file"):
            self.layered(game_system_config=good, game_save_dirs=["default.systemconfig.gbx/x"])

    def test_game_system_config_is_not_exported_to_the_shell(self):
        good = {"format": "text-2006", "file": "Default.SystemConfig.Gbx"}
        pairs = dict(self.registry.shell_assignments(self.layered(game_system_config=good)))
        self.assertNotIn("TRACKMANIA_MAC_TITLE_GAME_SYSTEM_CONFIG", pairs)
        self.assertEqual(pairs["TRACKMANIA_MAC_TITLE_COMPONENTS"].split()[-1], "game-system-config")

    def test_game_exe_patch_names_a_patch_file_and_the_hash_before_it(self):
        good = {"file": "tmu/fast.json", "sha256_before": "3" * 64}
        title = self.layered(game_exe_patch=good)
        self.assertEqual(
            title.game_exe_patch, self.registry.GameExePatch("tmu/fast.json", "3" * 64)
        )
        self.assertEqual(title.components[-1], "game-exe-patch")
        self.assertNotIn("game-exe-patch", self.layered().components)
        for value in (
            "tmu/fast.json",
            {},
            {"file": "tmu/fast.json"},
            dict(good, extra=1),
            dict(good, file="fast.json"),
            dict(good, file="tmu/fast.patch"),
            dict(good, file="tmu/../fast.json"),
            dict(good, file="/tmu/fast.json"),
            dict(good, file="tmu/sub/fast.json"),
            dict(good, file="TMU/fast.json"),
            dict(good, file="tmu/fast.json\n"),
            dict(good, file=5),
            dict(good, sha256_before="3" * 63),
            dict(good, sha256_before="A" * 64),
            dict(good, sha256_before=None),
        ):
            with self.subTest(value=value), self.assertRaises(self.registry.RegistryError):
                self.layered(game_exe_patch=value)

    def test_game_exe_patch_belongs_to_its_title_and_changes_the_executable(self):
        good = {"file": "tmu/fast.json", "sha256_before": "3" * 64}
        with self.assertRaisesRegex(self.registry.RegistryError, "requires installer kind"):
            self.validate(game_exe_patch=good)
        with self.assertRaisesRegex(self.registry.RegistryError, "in the folder tmu"):
            self.layered(game_exe_patch=dict(good, file="tmnf/fast.json"))
        with self.assertRaisesRegex(self.registry.RegistryError, "must differ"):
            self.layered(game_exe_patch=dict(good, sha256_before="0" * 64))

    def test_game_exe_patch_is_not_exported_to_the_shell(self):
        good = {"file": "tmu/fast.json", "sha256_before": "3" * 64}
        pairs = dict(self.registry.shell_assignments(self.layered(game_exe_patch=good)))
        self.assertNotIn("TRACKMANIA_MAC_TITLE_GAME_EXE_PATCH", pairs)
        self.assertEqual(pairs["TRACKMANIA_MAC_TITLE_COMPONENTS"].split()[-1], "game-exe-patch")

    def source_patch(self, **changes):
        patch = {
            "header_sha256": "4" * 64,
            "edits": [{"offset": "0x3aed9", "before": "14", "after": "34"}],
            "sha256_after": "5" * 64,
        }
        patch.update(changes)
        return patch

    def test_source_patch_holds_byte_edits_and_two_hashes(self):
        title = self.layered(source_patch=self.source_patch())
        self.assertEqual(
            title.source_patch,
            self.registry.SourcePatch(
                "4" * 64, (self.registry.SetupEdit(0x3AED9, b"\x14", b"\x34"),), "5" * 64
            ),
        )
        self.assertIn("source-patch", title.components)
        self.assertNotIn("source-patch", self.layered().components)
        two = [
            {"offset": "0x20", "before": "aabb", "after": "aacc"},
            {"offset": "0x10", "before": "01", "after": "02"},
        ]
        edits = self.layered(source_patch=self.source_patch(edits=two)).source_patch.edits
        self.assertEqual([edit.offset for edit in edits], [0x10, 0x20])
        edit = {"offset": "0x10", "before": "14", "after": "34"}
        for value in (
            "patch",
            {},
            {key: item for key, item in self.source_patch().items() if key != "edits"},
            dict(self.source_patch(), extra=1),
            self.source_patch(header_sha256="4" * 63),
            self.source_patch(sha256_after="A" * 64),
            self.source_patch(edits=[]),
            self.source_patch(edits=edit),
            self.source_patch(edits=[dict(edit, extra=1)]),
            self.source_patch(edits=[dict(edit, offset=16)]),
            self.source_patch(edits=[dict(edit, offset="16")]),
            self.source_patch(edits=[dict(edit, offset="0x3AED9")]),
            self.source_patch(edits=[dict(edit, offset="0x123456789")]),
            self.source_patch(edits=[dict(edit, before="1")]),
            self.source_patch(edits=[dict(edit, before="")]),
            self.source_patch(edits=[dict(edit, after="3G")]),
            self.source_patch(edits=[dict(edit, after="3434")]),
            self.source_patch(edits=[dict(edit, after="14")]),
            self.source_patch(edits=[edit, dict(edit, offset="0x10")]),
            self.source_patch(
                edits=[
                    {"offset": "0x10", "before": "0102", "after": "0103"},
                    dict(edit, offset="0x11"),
                ]
            ),
        ):
            with self.subTest(value=value), self.assertRaises(self.registry.RegistryError):
                self.layered(source_patch=value)

    def test_source_patch_needs_the_online_kind_and_changes_the_setup(self):
        with self.assertRaisesRegex(self.registry.RegistryError, "requires installer kind"):
            self.validate(source_patch=self.source_patch())
        with self.assertRaisesRegex(self.registry.RegistryError, "must differ"):
            self.layered(source_patch=self.source_patch(sha256_after=self.data["source_sha256"]))

    def test_source_patch_is_not_exported_to_the_shell(self):
        pairs = dict(
            self.registry.shell_assignments(self.layered(source_patch=self.source_patch()))
        )
        self.assertNotIn("TRACKMANIA_MAC_TITLE_SOURCE_PATCH", pairs)
        self.assertIn("source-patch", pairs["TRACKMANIA_MAC_TITLE_COMPONENTS"].split())

    def test_every_game_exe_patch_file_exists(self):
        registry = load_registry()
        for title in registry.load_all(TITLES):
            if title.game_exe_patch is not None:
                with self.subTest(title=title.id):
                    self.assertTrue(
                        (ROOT / "config/exe-patches" / title.game_exe_patch.file).is_file()
                    )

    def test_nx_compat_must_not_list_files_the_overlay_removes(self):
        with self.assertRaisesRegex(self.registry.RegistryError, "overlay removes"):
            self.layered(nx_compat=["OLD.dat"])

    def test_nx_compat_is_exported_as_one_scalar(self):
        title = self.layered(nx_compat=["a.exe", "b.dll"])
        pairs = dict(self.registry.shell_assignments(title))
        self.assertEqual(pairs["TRACKMANIA_MAC_TITLE_NX_COMPAT"], "a.exe b.dll")
        self.assertEqual(pairs["TRACKMANIA_MAC_TITLE_COMPONENTS"].split()[-1], "nx-compat")

    def test_a_trailing_newline_is_never_accepted(self):
        good = {"url": "https://example.com/u/update.exe", "sha256": "1" * 64, "size": 5}
        overlay = {"url": "https://example.com/o/overlay.tar.gz", "sha256": "2" * 64, "size": 7}
        cases = (
            {"overlay": dict(overlay, remove=["tmn.exe\n"])},
            {"updates": [dict(good, url=good["url"] + "\n")]},
            {"updates": [dict(good, sha256=good["sha256"] + "\n")]},
            {"overlay": dict(overlay, url=overlay["url"] + "\n")},
            {"source_url": self.data["source_url"] + "\n"},
            {"source_sha256": "0" * 64 + "\n"},
            {"game_exe_sha256": "0" * 64 + "\n"},
            {"game_version": "1.0\n"},
            {"release_date": "2006-01-26\n"},
            {"bundle_id_game": "local.trackmania-mac.x.game\n"},
        )
        for changes in cases:
            with (
                self.subTest(changes=changes),
                self.assertRaises(self.registry.RegistryError),
            ):
                self.layered(**changes)

    def test_layered_downloads_need_distinct_file_names(self):
        source_name = self.data["source_url"].rsplit("/", 1)[-1]
        clash = {"url": "https://example.com/other/" + source_name, "sha256": "1" * 64, "size": 5}
        with self.assertRaisesRegex(self.registry.RegistryError, "distinct file names"):
            self.layered(updates=[clash])
        update = {"url": "https://example.com/u/Overlay.tar.gz", "sha256": "1" * 64, "size": 5}
        with self.assertRaisesRegex(self.registry.RegistryError, "distinct file names"):
            self.layered(updates=[update])

    def test_layered_install_fields_need_the_online_kind(self):
        update = [{"url": "https://example.com/u/update.exe", "sha256": "1" * 64, "size": 5}]
        overlay = {"url": "https://example.com/o/overlay.tar.gz", "sha256": "2" * 64, "size": 7}
        for field, value in (
            ("source_interactive", True),
            ("updates", update),
            ("overlay", overlay),
            ("nx_compat", ["a.dll"]),
        ):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(self.registry.RegistryError, "requires installer kind"),
            ):
                self.validate(**{field: value})
        self.assertFalse(self.validate(source_interactive=False).source_interactive)

    def test_modloader_titles_must_run_the_forever_exe(self):
        with self.assertRaisesRegex(self.registry.RegistryError, "needs_tmloader requires"):
            self.validate(game_exe="ManiaPlanet.exe")
        self.validate(game_exe="ManiaPlanet.exe", needs_tmloader=False)

    def test_value_types_are_checked(self):
        for field, value in (
            ("needs_tmloader", "yes"),
            ("name", ""),
            ("name", 5),
            ("notes", 5),
            ("source_size", -1),
            ("source_size", True),
            ("source_sha256", 5),
            ("save_dirs", "TrackMania"),
        ):
            with (
                self.subTest(field=field, value=value),
                self.assertRaises(self.registry.RegistryError),
            ):
                self.validate(**{field: value})

    def test_unsafe_names_are_rejected(self):
        bad = [".", "..", "~root", "a\\b", "a\nb", "a\x00b", "a\x7fb", "a/b"]
        for field in ("short_name", "game_exe", "launcher_exe", "game_dir_name"):
            for value in bad:
                with (
                    self.subTest(field=field, value=value),
                    self.assertRaises(self.registry.RegistryError),
                ):
                    self.validate(**{field: value})
        for field, value in (
            ("app_name", "~x.app"),
            ("settings_app_name", ".."),
            ("app_name", "a\\b.app"),
        ):
            with (
                self.subTest(field=field, value=value),
                self.assertRaises(self.registry.RegistryError),
            ):
                self.validate(**{field: value})
        for value in (".", "..", "~x", "a\\b", "a\tb"):
            with self.subTest(save_dir=value), self.assertRaises(self.registry.RegistryError):
                self.validate(save_dirs=[value])
        for value in ("TMNF/..", "/abs", "a//b", "~/x", "a/./b", "a\\b", "TMNF/a\nb"):
            with (
                self.subTest(legacy_prefix_subdir=value),
                self.assertRaises(self.registry.RegistryError),
            ):
                self.validate(legacy_prefix_subdir=value)

    def test_user_subdir_must_be_a_declared_save_dir(self):
        for value in ("Elsewhere", "TmForever/x", "..", "~x"):
            with self.subTest(user_subdir=value), self.assertRaises(self.registry.RegistryError):
                self.validate(user_subdir=value)
        missing = dict(self.data)
        del missing["user_subdir"]
        with self.assertRaises(self.registry.RegistryError):
            self.registry._validate(missing, Path("/x/tmu.json"))
        self.assertEqual(self.validate().user_subdir, "TrackMania")

    def test_bundle_ids_and_urls(self):
        for value in ("a b", "a;b", "a$b", "a/b", "a_b"):
            with self.subTest(bundle=value), self.assertRaises(self.registry.RegistryError):
                self.validate(bundle_id_game=value)
        for value in ("ftp://x/y", "file:///etc/passwd", "https://x/a b", "https://x/'"):
            with self.subTest(url=value), self.assertRaises(self.registry.RegistryError):
                self.validate(source_url=value)
        self.validate(source_url="https://example.test/x.exe")
        with self.assertRaises(self.registry.RegistryError):
            self.validate(source_url="http://example.test/x.exe", source_sha256=None)

    def test_export_round_trips_hostile_but_valid_values(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            hostile = dict(
                self.data,
                id="evil",
                name="$(touch pwned) `id` ' \" ; &",
                notes="line1\nline2 $HOME \\ *",
                short_name="a b$c",
            )
            titles = Path(directory) / "titles"
            titles.mkdir()
            (titles / "evil.json").write_text(json.dumps(hostile))
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-euc",
                    ". src/shell/lib/common.sh; "
                    'printf "%s\\0" "$TRACKMANIA_MAC_TITLE_NAME" "$TRACKMANIA_MAC_TITLE_NOTES" "$TRACKMANIA_MAC_TITLE_SHORT_NAME"',
                ],
                cwd=ROOT,
                capture_output=True,
                timeout=SUBPROCESS_TIMEOUT,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": directory,
                    "TRACKMANIA_MAC_TITLE": "evil",
                    "TRACKMANIA_MAC_TITLES_DIR": str(titles),
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            name, notes, short = result.stdout.decode().split("\0")[:3]
            self.assertEqual(
                (name, notes, short), (hostile["name"], hostile["notes"], hostile["short_name"])
            )
            self.assertFalse((ROOT / "pwned").exists())
            self.assertFalse((Path(directory) / "pwned").exists())

    def test_shell_honors_titles_dir_like_python(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            titles = Path(directory) / "titles"
            titles.mkdir()
            (titles / "tmu.json").write_text(json.dumps(dict(self.data, name="Custom")))
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-euc",
                    '. src/shell/lib/common.sh; printf "%s" "$TRACKMANIA_MAC_TITLE_NAME"',
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=SUBPROCESS_TIMEOUT,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": directory,
                    "TRACKMANIA_MAC_TITLE": "tmu",
                    "TRACKMANIA_MAC_TITLES_DIR": str(titles),
                },
            )
            self.assertEqual(result.stdout, "Custom", result.stderr)


if __name__ == "__main__":
    unittest.main()
