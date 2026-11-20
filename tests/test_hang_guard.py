from __future__ import annotations

import ast
import unittest
from pathlib import Path

from hang_guard import SUBPROCESS_TIMEOUT

TESTS = Path(__file__).resolve().parent
# Calls that start or wait on a real subprocess. Mocks, sockets and futures
# take a timeout too, but those do not run on the shared CI runner's clock.
SUBPROCESS_CALLS = (".run", ".communicate", ".wait", ".check_output", ".check_call", ".call")


def short_limits(name: str, source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source, name)):
        values = []
        if isinstance(node, ast.Call):
            func = ast.unparse(node.func)
            if func.startswith("mock.") or not (
                func.endswith(SUBPROCESS_CALLS) or func in ("run", "self.shell")
            ):
                continue
            values = [k.value for k in node.keywords if k.arg == "timeout"]
        elif isinstance(node, ast.FunctionDef) and not node.name.startswith("_fake"):
            pairs = zip(reversed(node.args.args), reversed(node.args.defaults))
            values = [default for arg, default in pairs if arg.arg == "timeout"]
        for value in values:
            if isinstance(value, ast.Constant) and value.value < SUBPROCESS_TIMEOUT:
                found.append(
                    (value.lineno, "{}:{}: timeout={}".format(name, value.lineno, value.value))
                )
    return [line for _, line in sorted(found)]


class HangGuardTests(unittest.TestCase):
    def test_subprocess_limits_use_the_shared_hang_guard(self):
        found = [
            line
            for path in sorted(TESTS.glob("test_*.py"))
            for line in short_limits(path.name, path.read_text())
        ]
        self.assertEqual(
            found,
            [],
            "use hang_guard.SUBPROCESS_TIMEOUT: CI oversubscribes its runner, so a "
            "short literal limit fails healthy commands",
        )

    def test_detects_short_literals_in_calls_and_defaults(self):
        source = (
            "import subprocess\n"
            "subprocess.run(['true'], timeout=15)\n"
            "def shell(command, timeout=8):\n"
            "    pass\n"
            "mock.call.process.wait(timeout=1)\n"
        )
        self.assertEqual(
            short_limits("probe.py", source), ["probe.py:2: timeout=15", "probe.py:3: timeout=8"]
        )


if __name__ == "__main__":
    unittest.main()
