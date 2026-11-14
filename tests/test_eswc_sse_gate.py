"""The verdicts of trackmania_mac.probes.eswc_sse_gate, on made-up harness lines."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from trackmania_mac.probes import eswc_sse_gate as gate  # noqa: E402


def line(call=0, sse="a", x87="b", state="c", handed=0, after="1f80"):
    # The format tests/eswc-sse-particles.c prints.
    return f"H {call} game 12 {handed} sse {sse} x87 {x87} state {state} mxcsr 1f80 {after}"


class GateTests(unittest.TestCase):
    def verdict(self, **runs):
        results = {(impl, "stock"): gate.calls([line()]) for impl in ("orig", "stub")}
        results.update({(impl, "sidecar"): gate.calls([line()]) for impl in ("orig", "stub")})
        for key, text in runs.items():
            impl, translator = key.split("_")
            results[(impl, translator)] = gate.calls([text])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            status = gate.compare(results)
        return status, out.getvalue()

    def test_parses_the_harness_line(self):
        self.assertEqual(
            gate.calls([line(call=3, handed=2, after="1fa0")])["3"],
            dict(
                group="game",
                count="12",
                handed="2",
                sse="a",
                x87="b",
                state="c",
                before="1f80",
                after="1fa0",
            ),
        )

    def test_identical_runs_pass(self):
        status, out = self.verdict()
        self.assertEqual(status, 0)
        self.assertIn("1 calls, 12 particles, 0 handed to the x87 loop", out)
        self.assertTrue(out.endswith("PASS\n"))

    def test_the_patch_must_match_stock_rosetta_exactly(self):
        for changed in (line(sse="z"), line(x87="z"), line(state="z")):
            with self.subTest(changed=changed):
                self.assertEqual(self.verdict(stub_stock=changed)[0], 1)

    def test_under_a_translator_only_the_patch_particles_must_match(self):
        self.assertEqual(self.verdict(orig_sidecar=line(sse="z", x87="z"))[0], 0)
        self.assertEqual(self.verdict(stub_sidecar=line(x87="z"))[0], 0)
        self.assertEqual(self.verdict(stub_sidecar=line(sse="z"))[0], 1)

    def test_the_patch_restores_mxcsr(self):
        self.assertEqual(self.verdict(stub_stock=line(after="1fa0"))[0], 1)
        # Particles the x87 loop ran may leave flags the translator set.
        self.assertEqual(self.verdict(stub_sidecar=line(handed=1, after="1fa0"))[0], 0)


if __name__ == "__main__":
    unittest.main()
