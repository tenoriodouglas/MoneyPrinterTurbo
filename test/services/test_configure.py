import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from growth import configure
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


class TestPlaceholderKeys(unittest.TestCase):
    """An example pasted instead of a key is accepted by TOML and rejected by
    the provider several commands later, as an unrelated-looking 401."""

    def test_the_portuguese_placeholder_is_refused(self):
        with self.assertRaises(configure.ConfigError) as ctx:
            configure.clean_key("sk_SUA_CHAVE", "image")
        self.assertIn("looks like the example", str(ctx.exception))

    def test_the_english_placeholder_is_refused(self):
        with self.assertRaises(configure.ConfigError):
            configure.clean_key("YOUR_KEY_HERE", "llm")

    def test_angle_brackets_are_refused(self):
        with self.assertRaises(configure.ConfigError):
            configure.clean_key("<key>", "llm")

    def test_a_real_key_is_accepted(self):
        self.assertEqual(
            configure.clean_key("sk_PV6GoBh65PXsFkrMa5c5riVC36", "llm"),
            "sk_PV6GoBh65PXsFkrMa5c5riVC36",
        )

    def test_a_key_that_merely_contains_x_characters_is_accepted(self):
        """The guard must not reject a real key for its letters."""
        self.assertEqual(configure.clean_key("sk_axbxcxd", "llm"), "sk_axbxcxd")


class TestFreshConfigRead(unittest.TestCase):
    """app/config loads config.toml once at import, so a long-lived process
    never sees an edit. The bot is exactly that, and an operator who fixes
    config.toml would otherwise be refused by the same stale value forever."""

    def _write(self, directory, body):
        path = Path(directory) / "config.toml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_it_returns_the_app_table(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self._write(temp, '[app]\nllm_provider = "pollinations"\n')
            self.assertEqual(
                configure.load_app_config(path)["llm_provider"], "pollinations"
            )

    def test_it_sees_an_edit_the_process_never_loaded(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self._write(temp, '[app]\nllm_provider = "gemini"\n')
            self.assertEqual(configure.load_app_config(path)["llm_provider"], "gemini")
            self._write(temp, '[app]\nllm_provider = "pollinations"\n')
            self.assertEqual(
                configure.load_app_config(path)["llm_provider"], "pollinations"
            )

    def test_a_byte_order_mark_does_not_defeat_it(self):
        """The engine opens with utf-8-sig and renders happily; bare tomllib
        rejects the same file."""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            path.write_bytes(b"\xef\xbb\xbf[app]\nllm_provider = \"openai\"\n")
            self.assertEqual(configure.load_app_config(path)["llm_provider"], "openai")

    def test_a_missing_file_falls_back_rather_than_failing(self):
        """None means "use the in-process snapshot". Refusing a render over a
        stray byte would be worse than the staleness this fixes."""
        self.assertIsNone(configure.load_app_config(Path("/nonexistent/config.toml")))

    def test_broken_toml_falls_back_rather_than_failing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self._write(temp, "[app\nbroken = ")
            self.assertIsNone(configure.load_app_config(path))

    def test_a_file_with_no_app_table_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self._write(temp, '[whisper]\nmodel = "base"\n')
            self.assertEqual(configure.load_app_config(path), {})
