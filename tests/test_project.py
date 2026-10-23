import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def section(text, name):
    match = re.search(r'^\[' + re.escape(name) + r'\]\s*\n(.*?)(?=^\[|\Z)',
                      text, re.M | re.S)
    assert match, 'missing section: ' + name
    return match.group(1)


class ProjectMetadataTests(unittest.TestCase):
    def test_pyproject_version_matches_version_file(self):
        text = (ROOT / 'pyproject.toml').read_text()
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), (ROOT / 'VERSION').read_text().strip())



if __name__ == '__main__':
    unittest.main()
