import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from trackmania_mac import legacy
from trackmania_mac.app import launcher_config


ROOT = Path(__file__).resolve().parents[1]
WRAPPERS = ROOT / "src/shell/lib/apps/wrappers.sh"
COMMON = ROOT / "src/shell/lib/common.sh"
VALUES = (
    "/Users/me/Library/TrackManiaMacOS/runtime/0.5.0",
    "/Applications/TrackMania Nations Forever.app",
    'it\'s a "test" $HOME `x` ; & | < > ( ) { } * ? ! [ ]',
    "trailing\\backslash",
    "café über",
    "",
    "1280x720",
)


def _function(path: Path, name: str) -> str:
    """One shell function's source, cut from PATH."""
    lines = path.read_text().splitlines()
    start = lines.index(f"{name}() {{")
    end = lines.index("}", start)
    return "\n".join(lines[start : end + 1])


def shell(script: str, *arguments: str, locale: str = "C") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", "-c", script, "_", *arguments],
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": locale},
        timeout=30,
    )


class WordValueTests(unittest.TestCase):
    def quoted(self, value: str, locale: str) -> str:
        result = shell('printf "%q" "$1"', value, locale=locale)
        self.assertEqual(result.returncode, 0)
        return result.stdout.decode("utf-8")

    def test_decodes_what_printf_q_writes_in_both_locales(self):
        for locale in ("C", "en_US.UTF-8"):
            for value in VALUES:
                with self.subTest(locale=locale, value=value):
                    self.assertEqual(launcher_config.word_value(self.quoted(value, locale)), value)

    def test_agrees_with_the_shell_decoder(self):
        decoder = _function(WRAPPERS, "shell_word_value")
        words = [self.quoted(value, "C") for value in VALUES] + [
            "plain",
            "$(rm -rf ~)",
            "a;b",
            "$'\\001'",
            "$'unterminated",
            "a\\",
            "'single'",
            "$'\\101\\'\\\\'",
            "",
            # Bash 3.2 leaves these bare; both decoders refuse them (fail closed).
            "a#b",
            "a~b",
        ]
        for word in words:
            with self.subTest(word=word):
                result = shell(decoder + '\nshell_word_value "$1"', word)
                try:
                    ours = launcher_config.word_value(word)
                except ValueError:
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    continue
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.decode("utf-8"), ours + "\n")


class ReadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "installation.sh"

    def write(self, *settings: str) -> None:
        lines = []
        for name, value in (setting.split("=", 1) for setting in settings):
            word = shell('printf "%q" "$1"', value).stdout.decode()
            lines.append(f'if test -z "${{{name}:-}}"; then export {name}={word}; fi')
        self.path.write_text("\n".join(lines) + "\n")

    def test_reads_settings_without_their_prefix(self):
        self.write(
            "TRACKMANIA_MAC_TITLE=tmnf",
            "TRACKMANIA_MAC_APP=/Applications/TrackMania Nations Forever.app",
            "TRACKMANIA_MAC_WINDOW_SIZE=1280x720",
        )
        settings = launcher_config.read(self.path)
        self.assertEqual(settings["TITLE"], "tmnf")
        self.assertEqual(settings["APP"], "/Applications/TrackMania Nations Forever.app")
        self.assertEqual(settings["WINDOW_SIZE"], "1280x720")

    def test_an_earlier_releases_names_are_read_in_their_own_dialect(self):
        old = legacy.ENV_PREFIX
        self.write(f"{old}TITLE=tmnf", f"{old}RUNTIME=/x/runtime/0.3.0")
        self.assertEqual(launcher_config.read(self.path)["RUNTIME"], "/x/runtime/0.3.0")

    def test_refuses_anything_else(self):
        cases = (
            'if test -z "${TRACKMANIA_MAC_TITLE:-}"; then export TRACKMANIA_MAC_TITLE=tmnf; fi\n'
            "echo surprise\n",
            'if test -z "${TRACKMANIA_MAC_TITLE:-}"; then export TRACKMANIA_MAC_APP=tmnf; fi\n',
            'if test -z "${TRACKMANIA_MAC_TITLE:-}"; then export TRACKMANIA_MAC_TITLE=$(id); fi\n',
            'if test -z "${TRACKMANIA_MAC_AUDIO:-}"; then export TRACKMANIA_MAC_AUDIO=on; fi\n',
            'if test -z "${TRACKMANIA_MAC_TITLE:-}"; then export TRACKMANIA_MAC_TITLE=tmnf; fi\n'
            + 'if test -z "${{{0}AUDIO:-}}"; then export {0}AUDIO=on; fi\n'.format(
                legacy.ENV_PREFIX
            ),
        )
        for text in cases:
            with self.subTest(text=text):
                self.path.write_text(text)
                with self.assertRaises(ValueError):
                    launcher_config.read(self.path)

    def test_refuses_a_symlink(self):
        self.write("TRACKMANIA_MAC_TITLE=tmnf")
        link = self.path.with_name("link.sh")
        os.symlink(self.path, link)
        with self.assertRaises(ValueError):
            launcher_config.read(link)


class LaunchOptionTests(unittest.TestCase):
    def test_validity_matches_the_shell(self):
        check = _function(COMMON, "launch_option_valid")
        cases = {
            "WINDOW_SIZE": ("fullscreen", "1280x720", "65535x1", "65536x1", "0x720", "1280", "12x"),
            "AUDIO": ("on", "off", "On", ""),
            "X87": ("on", "off", "yes"),
            "RENDERER": ("d9vk", "wined3d-vulkan", "wined3d-gl", "metal"),
        }
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    result = shell(
                        check + '\nlaunch_option_valid "$1" "$2"', f"TRACKMANIA_MAC_{key}", value
                    )
                    self.assertEqual(
                        launcher_config.valid_launch_option(key, value), result.returncode == 0
                    )


if __name__ == "__main__":
    unittest.main()
