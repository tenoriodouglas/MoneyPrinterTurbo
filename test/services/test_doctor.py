import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from growth import doctor
from growth.doctor import FAIL, OK, WARN


class TestLocalChecks(unittest.TestCase):
    def test_python_check_reflects_the_running_interpreter(self):
        result = doctor._check_python()
        expected = OK if sys.version_info[:2] in doctor.SUPPORTED_PYTHON else FAIL
        self.assertEqual(result.status, expected)

    def test_missing_ffmpeg_fails_with_the_install_command(self):
        with patch("growth.doctor.shutil.which", return_value=None):
            result = doctor._check_ffmpeg()
        self.assertEqual(result.status, FAIL)
        self.assertIn("apt-get install", result.fix)

    def test_missing_config_fails(self):
        with patch.object(doctor, "REPO_ROOT", Path("/nonexistent")):
            self.assertEqual(doctor._check_config().status, FAIL)

    def test_low_disk_warns_rather_than_fails(self):
        """Little space slows a batch down; it does not make one impossible."""
        import shutil as shutil_module

        usage = shutil_module.disk_usage(Path.cwd())
        tiny = type(usage)(total=usage.total, used=usage.used, free=1024**3)
        with patch("growth.doctor.shutil.disk_usage", return_value=tiny):
            self.assertEqual(doctor._check_disk().status, WARN)

    def test_font_referenced_by_a_pack_must_exist(self):
        with patch.object(doctor, "REPO_ROOT", Path("/nonexistent")):
            result = doctor._check_fonts()
        self.assertEqual(result.status, FAIL)
        self.assertIn("font", result.detail.lower())

    def test_fonts_present_in_the_real_repo(self):
        self.assertEqual(doctor._check_fonts().status, OK)


class TestLlmCheck(unittest.TestCase):
    def test_unset_provider_fails_before_any_request(self):
        with patch("app.config.config.app", {}):
            result = doctor._check_llm()
        self.assertEqual(result.status, FAIL)
        self.assertIn("llm_provider", result.detail)

    def test_empty_key_fails_before_any_request(self):
        with patch("app.config.config.app", {"llm_provider": "gemini"}):
            with patch("app.services.llm.test_connection") as connect:
                result = doctor._check_llm()
        connect.assert_not_called()
        self.assertEqual(result.status, FAIL)
        self.assertIn("gemini_api_key", result.detail)

    def test_rejected_key_reports_the_provider_message(self):
        config = {"llm_provider": "gemini", "gemini_api_key": "bad"}
        with patch("app.config.config.app", config):
            with patch(
                "app.services.llm.test_connection",
                return_value=(False, "invalid api key", 0.4),
            ):
                result = doctor._check_llm()
        self.assertEqual(result.status, FAIL)
        self.assertIn("invalid api key", result.detail)

    def test_working_provider_passes(self):
        config = {"llm_provider": "gemini", "gemini_api_key": "good"}
        with patch("app.config.config.app", config):
            with patch("app.services.llm.test_connection", return_value=(True, "", 1.2)):
                result = doctor._check_llm()
        self.assertEqual(result.status, OK)

    def test_provider_sdk_exception_is_caught(self):
        config = {"llm_provider": "gemini", "gemini_api_key": "good"}
        with patch("app.config.config.app", config):
            with patch("app.services.llm.test_connection", side_effect=RuntimeError("boom")):
                result = doctor._check_llm()
        self.assertEqual(result.status, FAIL)
        self.assertIn("boom", result.detail)


class TestMaterialsCheck(unittest.TestCase):
    def test_missing_key_fails_with_where_to_get_one(self):
        with patch("app.config.config.app", {"video_source": "pexels"}):
            result = doctor._check_materials()
        self.assertEqual(result.status, FAIL)
        self.assertIn("pexels.com/api", result.fix)

    def test_key_that_returns_nothing_is_treated_as_rejected(self):
        config = {"video_source": "pexels", "pexels_api_keys": ["bad"]}
        with patch("app.config.config.app", config):
            with patch("app.services.material.search_videos_pexels", return_value=[]):
                result = doctor._check_materials()
        self.assertEqual(result.status, FAIL)

    def test_working_key_passes(self):
        config = {"video_source": "pexels", "pexels_api_keys": ["good"]}
        with patch("app.config.config.app", config):
            with patch(
                "app.services.material.search_videos_pexels", return_value=[object()] * 7
            ):
                result = doctor._check_materials()
        self.assertEqual(result.status, OK)
        self.assertIn("7", result.detail)

    def test_provider_without_a_preflight_warns_instead_of_failing(self):
        """An unprobed source is unknown, not broken."""
        with patch("app.config.config.app", {"video_source": "local"}):
            self.assertEqual(doctor._check_materials().status, WARN)


class TestRunChecks(unittest.TestCase):
    def test_skip_network_runs_only_local_checks(self):
        names = {check.name for check in doctor.run_checks(skip_network=True)}
        self.assertIn("ffmpeg", names)
        self.assertNotIn("llm", names)
        self.assertNotIn("materials", names)

    def test_a_check_that_raises_is_reported_and_the_rest_still_run(self):
        def exploding():
            raise RuntimeError("kaboom")

        with patch.object(doctor, "_LOCAL_CHECKS", (exploding, doctor._check_python)):
            results = doctor.run_checks(skip_network=True)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].status, FAIL)
        self.assertIn("kaboom", results[0].detail)
        self.assertEqual(results[1].name, "python")


if __name__ == "__main__":
    unittest.main()
