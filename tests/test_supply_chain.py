from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    p
    for p in sorted((ROOT / "src").rglob("*"))
    if p.is_file()
    and "swift" not in p.relative_to(ROOT / "src").parts
    and p.suffix not in (".pyc", ".png", ".svg")
]
SHELL_CURL = re.compile(r"(?<![\w./-])curl\s+(?![\s]|-q\s)")
PYTHON_CURL = re.compile(r"""['"]curl['"]\s*,\s*(?!\s|['"]-q['"])""")


class CurlIgnoresUserConfig(unittest.TestCase):
    """curl reads ~/.curlrc unless -q is its first argument."""

    def test_every_curl_invocation_starts_with_q(self):
        invocations = 0
        offenders = []
        for path in SOURCES:
            try:
                lines = path.read_text().splitlines()
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(lines, 1):
                if line.lstrip().startswith("#"):
                    continue
                if path.suffix == ".py":
                    invocations += len(re.findall(r"""['"]curl['"]\s*,""", line))
                    bad = PYTHON_CURL.search(line)
                else:
                    invocations += len(re.findall(r"(?<![\w./-])curl\s+-", line))
                    bad = SHELL_CURL.search(line)
                if bad:
                    offenders.append(
                        "{}:{}: {}".format(path.relative_to(ROOT), number, line.strip())
                    )
        self.assertGreaterEqual(invocations, 3)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
