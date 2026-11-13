import fcntl
import io
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from trackmania_mac import legacy, markers
from trackmania_mac.guard import operation_guard, storage
from trackmania_mac.install import version as project_version


ROOT = Path(__file__).resolve().parents[1]
CURRENT = project_version.read(ROOT)
REVISION = "0123456789abcdef0123456789abcdef01234567"


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            dir="/private/tmp", prefix="trackmania-mac-storage-"
        )
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        patcher = mock.patch.dict(os.environ, HOME=str(self.home))
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("TRACKMANIA_MAC_SUPPORT", "TRACKMANIA_MAC_CACHE", "TRACKMANIA_MAC_TITLES_DIR"):
            patcher = mock.patch.dict(os.environ)
            patcher.start()
            self.addCleanup(patcher.stop)
            os.environ.pop(name, None)
        self.data = self.home / "Library/TrackManiaMacOS"
        self.cache = self.data / "cache"
        self.runtime = self.data / "runtime"
        self.logs = self.data / "logs"
        for folder in (self.cache, self.runtime, self.logs, self.data / "games/tmnf"):
            folder.mkdir(parents=True)
        self.reports = []

    def file(self, path: Path, text: str = "x") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def launcher(self, title: str, **settings: str) -> Path:
        folder = self.data / "launchers" / title
        self.file(folder / markers.LAUNCHER, "")
        lines = [f"TITLE={title}"] + [f"{key}={value}" for key, value in settings.items()]
        text = "".join(
            f'if test -z "${{TRACKMANIA_MAC_{key}:-}}"; then export TRACKMANIA_MAC_{key}={value}; fi\n'
            for key, value in (line.split("=", 1) for line in lines)
        )
        self.file(folder / "config/installation.sh", text)
        return folder

    def runtime_folder(self, version: str, marker: str = markers.RUNTIME) -> Path:
        folder = self.runtime / version
        self.file(folder / marker, version + "\n")
        self.file(folder / "bin/wine")
        return folder

    def clean(self, kind: str) -> int:
        return storage.clean(kind, self.reports.append)

    # --- downloads ---------------------------------------------------------

    def test_downloads_remove_only_named_files(self):
        old = time.time() - 2 * storage.STALE_SECONDS
        removed = [
            self.file(self.cache / "tmnationsforever_setup.exe"),
            self.file(self.cache / "tmnationsforever_setup.exe.partial.AbC123"),
            self.file(self.cache / "games/tmu/tmunitedforever_setup.exe"),
            self.file(self.cache / "games/tmu/tmunitedforever_setup.inner/Setup.exe"),
            self.file(self.cache / "directx_Jun2010_redist.exe"),
            self.file(self.cache / "TMLoader-1.0.3.zip"),
            self.file(self.cache / "TMLoader-0.9.zip.partial.zzzzzz"),
            self.file(self.cache / "openal-soft-1.25.2-bin.zip"),
            self.file(self.cache / "trackmania-mac-0.4.3-runtime.tar.xz"),
            self.file(self.cache / f"build/{REVISION}/Makefile"),
            self.file(self.cache / "build/.clone.q1w2e3/HEAD"),
            self.file(self.cache / "directx-extract.aaaaaa/x.cab"),
        ]
        os.utime(self.cache / "directx-extract.aaaaaa", (old, old))
        kept = [
            self.file(self.cache / ".app-install.lock", ""),
            self.file(self.cache / "game-state-0123.exe"),
            self.file(self.cache / "notes.txt"),
            self.file(self.cache / "games/tmu/notes.txt"),
            self.file(self.cache / "openal-extract.bbbbbb/fresh"),
            self.file(self.cache / "import-mount.cccccc/inside"),
        ]
        self.file(self.home / "elsewhere.zip")
        os.symlink(self.home / "elsewhere.zip", self.cache / "TMLoader-2.0.zip")

        self.assertGreater(self.clean("downloads"), 0)
        for path in removed:
            self.assertFalse(path.exists(), path)
        for path in kept:
            self.assertTrue(path.exists(), path)
        self.assertFalse((self.cache / "build").exists())
        self.assertTrue((self.cache / "TMLoader-2.0.zip").is_symlink())
        self.assertTrue((self.home / "elsewhere.zip").exists())
        self.assertIn(
            "Kept {} (not a regular file)".format(self.cache / "TMLoader-2.0.zip"), self.reports
        )
        self.assertTrue(self.cache.is_dir())

    def test_preview_labels_and_sizes_what_clean_removes(self):
        self.file(self.cache / "tmnationsforever_setup.exe", "12345")
        self.file(self.cache / "trackmania-mac-0.4.3-runtime.tar.xz", "123")
        self.file(self.cache / f"build/{REVISION}/a", "12")
        self.file(self.cache / "notes.txt", "ignored")
        downloads = storage.preview()["downloads"]
        self.assertEqual(
            {item["label"]: item["bytes"] for item in downloads["items"]},
            {
                "TrackMania Nations Forever installer": 5,
                "Runtime 0.4.3 download": 3,
                "Runtime source build": 2,
            },
        )
        self.assertEqual(downloads["bytes"], 10)
        with mock.patch.object(storage, "SIZE_BUDGET", 2):
            self.assertIsNone(storage.preview()["downloads"]["bytes"])

    def test_a_running_operation_blocks_downloads_and_runtimes(self):
        installer = self.file(self.cache / "directx_Jun2010_redist.exe")
        stale = self.runtime_folder("0.1.0")
        fd = operation_guard.acquire(self.data / "games/tmnf/prefix")
        try:
            for kind in ("downloads", "runtimes"):
                with self.subTest(kind=kind), self.assertRaises(operation_guard.Busy):
                    self.clean(kind)
            with redirect_stderr(io.StringIO()):
                self.assertEqual(storage.main(["clean", "downloads"]), operation_guard.EXIT_BUSY)
        finally:
            os.close(fd)
        self.assertTrue(installer.exists())
        self.assertTrue(stale.exists())
        self.clean("downloads")
        self.assertFalse(installer.exists())

    def test_a_prefix_a_launcher_records_is_locked_too(self):
        custom = self.home / "Games/custom/prefix"
        custom.parent.mkdir(parents=True)
        self.launcher("tmu", PREFIX=str(custom), RUNTIME=str(self.runtime / CURRENT))
        fd = operation_guard.acquire(custom)
        try:
            with self.assertRaises(operation_guard.Busy):
                self.clean("downloads")
        finally:
            os.close(fd)

    # --- runtimes ----------------------------------------------------------

    def test_runtimes_in_use_and_the_current_one_stay(self):
        current = self.runtime_folder(CURRENT)
        pinned = self.runtime_folder("0.4.0")
        own_version = self.runtime_folder("0.3.0")
        earlier = self.runtime_folder("0.2.0", legacy.MARKERS["RUNTIME"])
        stale = self.runtime_folder("0.1.0")
        unmarked = self.file(self.runtime / "0.0.9/bin/wine").parent.parent
        self.file(self.runtime / ".runtime-install.abcdef/x")
        self.launcher("tmnf", RUNTIME=str(pinned))
        relinked = self.launcher("tmu")  # records no runtime: the one of its own version
        self.file(relinked / "VERSION", "0.3.0\n")

        preview = storage.preview()["runtimes"]
        self.assertEqual(preview["current"], CURRENT)
        self.assertIsNone(preview["blocked"])
        self.assertEqual({item["version"] for item in preview["items"]}, {"0.2.0", "0.1.0"})
        self.assertEqual(set(preview["inUse"]), {CURRENT, "0.4.0", "0.3.0"})
        self.assertTrue(all(item["bytes"] > 0 for item in preview["items"]))

        self.assertEqual(self.clean("runtimes"), 2)
        for path in (current, pinned, own_version, unmarked):
            self.assertTrue(path.is_dir(), path)
        for path in (earlier, stale):
            self.assertFalse(path.exists(), path)
        self.assertTrue((self.runtime / ".runtime-install.abcdef").is_dir())

    def test_a_launcher_that_cannot_be_read_blocks_every_runtime_removal(self):
        stale = self.runtime_folder("0.1.0")
        launcher = self.launcher("tmnf")
        with (launcher / "config/installation.sh").open("a") as stream:
            stream.write("echo surprise\n")
        preview = storage.preview()["runtimes"]
        self.assertEqual(preview["items"], [])
        self.assertIn(str(launcher), preview["blocked"])
        with self.assertRaises(ValueError):
            self.clean("runtimes")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(storage.main(["clean", "runtimes"]), 1)
        self.assertTrue(stderr.getvalue().startswith("error: Cannot tell which runtime"))
        self.assertTrue(stale.is_dir())

    def test_a_symlinked_runtime_is_never_followed(self):
        target = self.home / "elsewhere/0.1.0"
        self.file(target / markers.RUNTIME)
        os.symlink(target, self.runtime / "0.1.0")
        self.assertEqual(storage.preview()["runtimes"]["items"], [])
        self.clean("runtimes")
        self.assertTrue((target / markers.RUNTIME).exists())

    # --- logs --------------------------------------------------------------

    def test_logs_a_game_is_writing_are_kept(self):
        for name in ("play.log", "play.log.1", "tmu-play.log", "tmu-play.log.3"):
            self.file(self.logs / name, "log")
        report = self.file(self.logs / "diagnostics-tmnf-20260101-120000.zip")
        other = self.file(self.logs / "notes.txt")
        held = os.open(self.logs / "tmu-play.log.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertEqual(storage.preview()["logs"]["bytes"], 4 * 3 + 1)
            self.clean("logs")
        finally:
            os.close(held)
        self.assertFalse((self.logs / "play.log").exists())
        self.assertFalse((self.logs / "play.log.1").exists())
        self.assertFalse(report.exists())
        self.assertTrue((self.logs / "tmu-play.log").exists())
        self.assertTrue((self.logs / "tmu-play.log.3").exists())
        self.assertTrue((self.logs / "play.log.lock").exists())
        self.assertTrue((self.logs / "tmu-play.log.lock").exists())
        self.assertTrue(other.exists())
        self.assertIn(
            "Kept {} (a game is writing it)".format(self.logs / "tmu-play.log"), self.reports
        )

    def test_unknown_kind_is_refused(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(storage.main(["clean", "everything"]), 1)
        self.assertTrue(stderr.getvalue().startswith("error: Unknown cleanup"))

    def test_preview_never_raises(self):
        with mock.patch.object(storage, "plan_downloads", side_effect=RuntimeError("boom")):
            preview = storage.preview()
        self.assertEqual(preview["downloads"], {"bytes": None, "items": []})
        self.assertIn("boom", preview["error"])
        with mock.patch.object(storage, "locations", side_effect=OSError("gone")):
            self.assertIsNone(storage.preview())


if __name__ == "__main__":
    unittest.main()
