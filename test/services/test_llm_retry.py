"""The retry loops in llm.py were dead for the commonest failure.

_generate_response reports every provider failure as a string starting with
"Error: " rather than raising. A non-empty string reads as a perfectly good
answer to `if response:`, so both loops broke on the first attempt: five
retries configured, none ever run, and a one-second blip cost a whole render.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services import llm


class TestRetryableClassification(unittest.TestCase):
    def test_a_rate_limit_is_worth_another_attempt(self):
        self.assertTrue(llm._is_retryable("Error: HTTP 429: rate limit exceeded"))

    def test_a_server_error_is_worth_another_attempt(self):
        self.assertTrue(llm._is_retryable("Error: HTTP 503 Service Unavailable"))

    def test_a_timeout_is_worth_another_attempt(self):
        self.assertTrue(llm._is_retryable("Error: request timed out"))

    def test_a_bad_key_is_not(self):
        """Retrying a 401 only spends quota to be refused five times."""
        self.assertFalse(llm._is_retryable("Error: HTTP 401: A valid API key is required"))

    def test_an_unknown_model_is_not(self):
        self.assertFalse(llm._is_retryable("Error: model 'nope' does not exist"))

    def test_a_real_answer_is_never_retryable(self):
        self.assertFalse(llm._is_retryable("A lone figure stood in the field."))

    def test_the_marker_must_be_the_error_prefix(self):
        """A script that merely mentions a timeout is a script, not a failure."""
        self.assertFalse(llm._is_retryable("He waited past the timeout, watching."))


class TestScriptRetries(unittest.TestCase):
    def test_a_transient_failure_is_retried_and_can_succeed(self):
        calls = []

        def flaky(*_args, **_kwargs):
            calls.append(1)
            if len(calls) < 3:
                return "Error: HTTP 503 Service Unavailable"
            return "A lone figure stood in a moonlit field."

        with patch.object(llm, "_generate_response", side_effect=flaky):
            with patch.object(llm, "_RETRY_BACKOFF_SECONDS", 0):
                script = llm.generate_script("ufo", "prompt", 1, "en")
        self.assertEqual(len(calls), 3)
        self.assertIn("moonlit field", script)

    def test_a_permanent_failure_is_not_retried(self):
        calls = []

        def refused(*_args, **_kwargs):
            calls.append(1)
            return "Error: HTTP 401: A valid API key is required"

        with patch.object(llm, "_generate_response", side_effect=refused):
            script = llm.generate_script("ufo", "prompt", 1, "en")
        self.assertEqual(len(calls), 1)
        self.assertIn("Error: ", script)

    def test_the_reason_survives_every_retry(self):
        """The caller reads this text to decide which stage failed and why,
        and there is no log file to fall back on."""
        with patch.object(llm, "_generate_response", return_value="Error: HTTP 429 rate limit"):
            with patch.object(llm, "_RETRY_BACKOFF_SECONDS", 0):
                script = llm.generate_script("ufo", "prompt", 1, "en")
        self.assertIn("429", script)


class TestTermsRetries(unittest.TestCase):
    def test_a_transient_failure_is_retried(self):
        calls = []

        def flaky(*_args, **_kwargs):
            calls.append(1)
            if len(calls) < 2:
                return "Error: HTTP 502 Bad Gateway"
            return '["a field at night", "a bright light"]'

        with patch.object(llm, "_generate_response", side_effect=flaky):
            with patch.object(llm, "_RETRY_BACKOFF_SECONDS", 0):
                terms = llm.generate_terms("ufo", "a script", 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(terms), 2)

    def test_a_permanent_failure_gives_up_at_once(self):
        calls = []

        def refused(*_args, **_kwargs):
            calls.append(1)
            return "Error: HTTP 401: A valid API key is required"

        with patch.object(llm, "_generate_response", side_effect=refused):
            terms = llm.generate_terms("ufo", "a script", 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(terms, [])


if __name__ == "__main__":
    unittest.main()
