import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from growth.configure import (
    ConfigError,
    apply_updates,
    build_updates,
    clean_key,
    mask,
    set_app_values,
)

SAMPLE = """log_level = "DEBUG"

[app]
# Register at https://www.pexels.com/api/
pexels_api_keys = []
llm_provider = "moonshot"
gemini_api_key = ""

[whisper]
# a different table with a same-named key
llm_provider = "leave-me"
"""


class TestCleanKey(unittest.TestCase):
    def test_strips_whitespace_and_pasted_quotes(self):
        for raw in ('  abc123  ', '"abc123"', "'abc123'", "“abc123”"):
            with self.subTest(raw):
                self.assertEqual(clean_key(raw, "test"), "abc123")

    def test_rejects_empty(self):
        for raw in ("", "   ", '""'):
            with self.subTest(raw):
                with self.assertRaises(ConfigError):
                    clean_key(raw, "test")

    def test_rejects_internal_whitespace(self):
        """A key split by a stray space is reported by providers as invalid."""
        with self.assertRaises(ConfigError) as context:
            clean_key("abc 123", "pexels")
        self.assertIn("whitespace", str(context.exception))


class TestSetAppValues(unittest.TestCase):
    def test_rewrites_only_the_named_keys(self):
        out = set_app_values(SAMPLE, {"llm_provider": "gemini"})
        data = tomllib.loads(out)
        self.assertEqual(data["app"]["llm_provider"], "gemini")
        self.assertEqual(data["app"]["gemini_api_key"], "")

    def test_same_key_in_another_table_is_untouched(self):
        out = set_app_values(SAMPLE, {"llm_provider": "gemini"})
        self.assertEqual(tomllib.loads(out)["whisper"]["llm_provider"], "leave-me")

    def test_comments_survive(self):
        out = set_app_values(SAMPLE, {"pexels_api_keys": ["k"]})
        self.assertIn("# Register at https://www.pexels.com/api/", out)

    def test_list_values_are_written_as_a_toml_array(self):
        out = set_app_values(SAMPLE, {"pexels_api_keys": ["one", "two"]})
        self.assertEqual(tomllib.loads(out)["app"]["pexels_api_keys"], ["one", "two"])

    def test_key_missing_from_the_table_is_appended_inside_it(self):
        out = set_app_values(SAMPLE, {"deepseek_api_key": "x"})
        data = tomllib.loads(out)
        self.assertEqual(data["app"]["deepseek_api_key"], "x")
        self.assertEqual(data["whisper"]["llm_provider"], "leave-me")

    def test_duplicate_assignments_collapse(self):
        source = '[app]\nllm_provider = "a"\nllm_provider = "b"\n'
        out = set_app_values(source, {"llm_provider": "gemini"})
        self.assertEqual(out.count("llm_provider"), 1)

    def test_quotes_and_backslashes_are_escaped(self):
        out = set_app_values(SAMPLE, {"gemini_api_key": 'a"b\\c'})
        self.assertEqual(tomllib.loads(out)["app"]["gemini_api_key"], 'a"b\\c')

    def test_no_updates_returns_the_file_unchanged(self):
        self.assertEqual(set_app_values(SAMPLE, {}), SAMPLE)


class TestBuildUpdates(unittest.TestCase):
    def test_pexels_key_becomes_a_list(self):
        self.assertEqual(
            build_updates(pexels="abc")["pexels_api_keys"], ["abc"]
        )

    def test_provider_sets_both_provider_and_its_key(self):
        updates = build_updates(provider="gemini", provider_key="abc")
        self.assertEqual(updates["llm_provider"], "gemini")
        self.assertEqual(updates["gemini_api_key"], "abc")

    def test_model_name_is_set_alongside_the_provider(self):
        updates = build_updates(provider="gemini", provider_model="gemini-3.1-flash-lite")
        self.assertEqual(updates["gemini_model_name"], "gemini-3.1-flash-lite")

    def test_model_without_a_provider_is_rejected(self):
        with self.assertRaises(ConfigError):
            build_updates(provider_model="gemini-3.1-flash-lite")

    def test_unknown_provider_is_rejected_with_the_known_list(self):
        with self.assertRaises(ConfigError) as context:
            build_updates(provider="not-a-provider", provider_key="abc")
        self.assertIn("known:", str(context.exception))

    def test_key_without_a_provider_is_rejected(self):
        with self.assertRaises(ConfigError):
            build_updates(provider_key="abc")

    def test_nothing_requested_is_rejected(self):
        with self.assertRaises(ConfigError):
            build_updates()


class TestApplyUpdates(unittest.TestCase):
    def test_writes_backs_up_and_restricts_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text(SAMPLE, encoding="utf-8")
            backup = apply_updates({"llm_provider": "gemini"}, config_path=path)

            self.assertEqual(backup.read_text(encoding="utf-8"), SAMPLE)
            self.assertEqual(
                tomllib.loads(path.read_text(encoding="utf-8"))["app"]["llm_provider"],
                "gemini",
            )
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_missing_config_names_the_command_that_creates_it(self):
        with self.assertRaises(ConfigError) as context:
            apply_updates({"llm_provider": "gemini"}, config_path=Path("/nope/config.toml"))
        self.assertIn("config.example.toml", str(context.exception))

    def test_a_result_that_would_not_parse_is_refused(self):
        """Better to refuse than to leave an unloadable config behind."""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text("[app]\nbroken = [[[\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                apply_updates({"llm_provider": "gemini"}, config_path=path)


class TestBackupsStayOutOfGit(unittest.TestCase):
    """A backup is a copy of config.toml, so it holds the same api keys.
    Committing one would publish them."""

    REPO = Path(__file__).parent.parent.parent

    @unittest.skipUnless(shutil.which("git"), "git is not available")
    def test_the_backup_name_this_code_writes_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text(SAMPLE, encoding="utf-8")
            backup = apply_updates({"llm_provider": "gemini"}, config_path=path)

        # Ask git itself rather than reimplementing gitignore matching.
        result = subprocess.run(
            ["git", "check-ignore", "-q", backup.name],
            cwd=self.REPO,
            capture_output=True,
        )
        self.assertEqual(
            result.returncode, 0, f"{backup.name} is not covered by .gitignore"
        )

    @unittest.skipUnless(shutil.which("git"), "git is not available")
    def test_config_itself_is_ignored(self):
        result = subprocess.run(
            ["git", "check-ignore", "-q", "config.toml"],
            cwd=self.REPO,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0)

    def test_the_backup_inherits_restrictive_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_text(SAMPLE, encoding="utf-8")
            path.chmod(0o600)
            backup = apply_updates({"llm_provider": "gemini"}, config_path=path)
            # Read the mode before the temporary directory is removed.
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)


class TestMask(unittest.TestCase):
    def test_long_key_keeps_only_its_ends(self):
        masked = mask("AIzaSyEXAMPLEKEY1234")
        self.assertTrue(masked.startswith("AIza"))
        self.assertTrue(masked.endswith("1234"))
        self.assertNotIn("EXAMPLEKEY", masked)

    def test_short_key_is_fully_hidden(self):
        self.assertEqual(mask("abc"), "***")


if __name__ == "__main__":
    unittest.main()
