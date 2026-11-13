import json
import re
import subprocess
import unittest
from pathlib import Path

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
                "tmu": "provisional",
                "tm-original": "unsupported",
                "tm-canyon": "unsupported",
                "tm-turbo": "unsupported",
                "tmn-eswc": "unsupported",
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
            timeout=15,
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
            timeout=15,
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
            timeout=15,
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
                timeout=60,
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
                    timeout=15,
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
            timeout=15,
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
                    timeout=15,
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
            timeout=15,
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
        """Every online installer kind gets /DIR equal to GAME_DIR."""
        stubs = (
            "download_pinned_installer() { :; }; "
            'unpack_inner_inno() { printf "/stub/setup.exe\\n"; }; '
            "run_wine() { :; }; "
            'run_released_installer() { printf "%s\\n" "$TRACKMANIA_MAC_INNO_DIR"; TRACKMANIA_MAC_INSTALLER_PID=0; }; '
            "wait_inno_setup() { :; }; verify_installed_game() { :; }; say() { :; }; "
        )
        for path in sorted(TITLES.glob("*.json")):
            data = json.loads(path.read_text())
            if data["installer"] not in ("online", "online-retail", "online-trial"):
                continue
            with self.subTest(title=data["id"]):
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
                    timeout=15,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "HOME": "/tmp/trackmania-mac-no-home",
                        "TRACKMANIA_MAC_TITLE": data["id"],
                    },
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout.strip(), "C:\\Program Files (x86)\\" + data["game_dir_name"]
                )

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
                    timeout=15,
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
        self.assertEqual(components["tmu"], ("competition-patch", "system-config"))
        for title_id in ("tm-canyon", "tm-turbo", "tm-original"):
            self.assertEqual(components[title_id], (), title_id)

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
                    timeout=15,
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
            timeout=15,
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
                    timeout=15,
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
            timeout=15,
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
            timeout=15,
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

    def test_online_installer_requires_an_exe_pin(self):
        with self.assertRaisesRegex(self.registry.RegistryError, "requires game_exe_sha256"):
            self.validate(installer="online")
        self.validate(installer="online", game_version="1.0", game_exe_sha256="0" * 64)

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
                timeout=15,
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
                timeout=15,
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
