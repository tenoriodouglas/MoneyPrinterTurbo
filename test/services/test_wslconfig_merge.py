import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

MERGE_AWK = Path(__file__).parent.parent.parent / "deploy" / "lib" / "wslconfig-merge.awk"


@unittest.skipUnless(shutil.which("awk"), "awk is not available")
class TestWslConfigMerge(unittest.TestCase):
    """The merge edits a file on the user's Windows profile that may hold
    unrelated settings, so losing a line is worse than not running at all."""

    def merge(self, source: str, memory="8GB", swap="2GB", processors="") -> str:
        result = subprocess.run(
            [
                "awk",
                "-f",
                str(MERGE_AWK),
                "-v",
                f"memory={memory}",
                "-v",
                f"swap={swap}",
                "-v",
                f"processors={processors}",
            ],
            input=source,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def test_empty_input_creates_the_section_without_leading_blanks(self):
        out = self.merge("")
        self.assertEqual(
            out.splitlines(), ["[wsl2]", "memory=8GB", "swap=2GB"]
        )

    def test_existing_value_is_replaced_in_place(self):
        out = self.merge("[wsl2]\nmemory=512MB\n")
        self.assertIn("memory=8GB", out)
        self.assertNotIn("512MB", out)

    def test_unrelated_keys_in_the_section_survive(self):
        out = self.merge("[wsl2]\nmemory=512MB\nlocalhostForwarding=true\n")
        self.assertIn("localhostForwarding=true", out)

    def test_other_sections_and_comments_survive(self):
        source = "# keep me\n[wsl2]\nmemory=512MB\n\n[experimental]\nsparseVhd=true\n"
        out = self.merge(source)
        self.assertIn("# keep me", out)
        self.assertIn("[experimental]", out)
        self.assertIn("sparseVhd=true", out)

    def test_keys_land_inside_wsl2_not_in_a_later_section(self):
        source = "[wsl2]\nmemory=512MB\n[experimental]\nsparseVhd=true\n"
        lines = [line.strip() for line in self.merge(source).splitlines() if line.strip()]
        self.assertLess(lines.index("swap=2GB"), lines.index("[experimental]"))

    def test_missing_section_is_appended_after_existing_content(self):
        out = self.merge("[experimental]\nautoMemoryReclaim=gradual\n")
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        self.assertEqual(lines[0], "[experimental]")
        self.assertIn("[wsl2]", lines)
        self.assertLess(lines.index("[experimental]"), lines.index("[wsl2]"))

    def test_duplicate_keys_collapse_to_one(self):
        out = self.merge("[wsl2]\nmemory=512MB\nmemory=256MB\n", swap="", processors="")
        self.assertEqual(out.count("memory="), 1)
        self.assertIn("memory=8GB", out)

    def test_empty_value_leaves_that_key_alone(self):
        out = self.merge("[wsl2]\nswap=1GB\n", memory="8GB", swap="", processors="")
        self.assertIn("swap=1GB", out)
        self.assertIn("memory=8GB", out)
        self.assertNotIn("processors", out)

    def test_section_header_is_matched_case_insensitively(self):
        out = self.merge("[WSL2]\nmemory=512MB\n")
        self.assertIn("memory=8GB", out)
        # A second section would mean the original one was never recognised.
        self.assertEqual(out.lower().count("[wsl2]"), 1)

    def test_spaced_section_header_and_key_are_handled(self):
        out = self.merge("[ wsl2 ]\n  memory = 512MB\n")
        self.assertIn("memory=8GB", out)
        self.assertNotIn("512MB", out)

    def test_nothing_is_lost_from_a_config_it_does_not_understand(self):
        source = "[wsl2]\nkernelCommandLine=quiet\nnestedVirtualization=true\n"
        out = self.merge(source)
        for line in ("kernelCommandLine=quiet", "nestedVirtualization=true"):
            self.assertIn(line, out)


if __name__ == "__main__":
    unittest.main()
