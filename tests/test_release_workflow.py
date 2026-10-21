from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowTests(unittest.TestCase):
    def test_tag_release_is_gated_on_matching_runtime_and_notarization(self):
        workflow = (ROOT / ".github/workflows/release-thin.yml").read_text()
        gate = workflow.index("Verify tag, runtime release asset and signing configuration")
        build = workflow.index("Build hub app archive")
        attach = workflow.index("Attach app archive to GitHub Release")

        self.assertLess(gate, build)
        self.assertLess(build, attach)
        self.assertIn('test "$GITHUB_REF_NAME" = "$tag"', workflow)
        self.assertIn('asset="tmnf-${version}-runtime.tar.xz"', workflow)
        self.assertIn('gh release download "$tag"', workflow)
        self.assertIn('test "$TMNF_NOTARIZE" = 1', workflow)
        self.assertIn("TMNF_SIGN_IDENTITY is required for a tag release", workflow)


if __name__ == "__main__":
    unittest.main()
