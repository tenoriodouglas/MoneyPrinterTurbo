import json
import sys
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import cli
from app.models.schema import VideoParams
from growth import doctor
from growth.configure import ConfigError, build_updates
from growth.doctor import FAIL, OK
from growth.niche import NicheError, load_niche, parse_niche
from growth.plan import parse_briefs, to_manifest_entry

BRIEF = json.dumps(
    [
        {
            "subject": "A documented sighting",
            "angle": "single-case",
            "hook": "Several people reported the same formation.",
            "key_points": ["a point", "another point"],
            "search_terms": ["a lone figure in a moonlit field looking up"],
            "call_to_action": "Read the witness statements.",
        }
    ]
)

_BASE_PACK = {
    "niche": {
        "id": "demo",
        "name": "Demo",
        "language": "en",
        "platforms": ["tiktok"],
    },
    "economics": {"cpm_low": 3.0, "cpm_high": 8.0},
    "audience": {"description": "demo", "pain_points": ["a pain"]},
    "content": {
        "angles": ["one", "two"],
        "system_prompt": "Write plainly.",
        "visual_terms": ["a field at night", "a radar room"],
    },
}


def _pack(images=None, video=None):
    data = {key: dict(value) for key, value in _BASE_PACK.items()}
    if images is not None:
        data["images"] = images
    if video is not None:
        data["video"] = video
    return data


class TestUfoPack(unittest.TestCase):
    """The pack that generates its visuals is the one most likely to break
    silently, because its settings live in two places."""

    def setUp(self):
        self.niche = load_niche("ufo-sightings")

    def test_it_generates_visuals_rather_than_searching_stock(self):
        self.assertEqual(self.niche.video.video_source, "openai_image")
        self.assertTrue(self.niche.images.configured)

    def test_the_style_template_carries_the_placeholder(self):
        self.assertIn("{term}", self.niche.images.prompt_template)

    def test_visual_terms_describe_scenes_not_keywords(self):
        """The image model draws what it is told, so a bare noun wastes a frame."""
        for term in self.niche.visual_terms:
            with self.subTest(term):
                self.assertGreaterEqual(len(term.split()), 5)

    def test_script_length_targets_the_paid_threshold(self):
        self.assertGreaterEqual(self.niche.video.paragraph_number, 5)

    def test_accuracy_guardrails_are_in_the_prompt(self):
        prompt = self.niche.system_prompt.lower()
        self.assertIn("never invent", prompt)
        self.assertIn("attribute", prompt)


class TestScriptMatching(unittest.TestCase):
    def test_every_shipped_pack_orders_material_by_the_script(self):
        """Without this the visuals for a later point appear while an earlier
        one is still being narrated."""
        from growth.niche import load_all_niches

        for niche in load_all_niches():
            with self.subTest(niche.id):
                self.assertTrue(niche.video.match_materials_to_script)

    def test_the_flag_reaches_the_manifest(self):
        niche = load_niche("ufo-sightings")
        brief = parse_briefs(BRIEF, niche, 1)[0]
        entry = to_manifest_entry(brief, niche)
        self.assertTrue(entry["match_materials_to_script"])
        VideoParams(**entry)

    def test_a_pack_may_turn_it_off(self):
        niche = parse_niche(_pack(video={"match_materials_to_script": False}))
        self.assertFalse(niche.video.match_materials_to_script)


class TestTermSizing(unittest.TestCase):
    """A generating source makes one image per term and stops when the terms
    run out, so the term count decides how much of the narration has a
    picture."""

    def test_a_generating_pack_gets_a_term_per_clip(self):
        niche = load_niche("ufo-sightings")
        brief = parse_briefs(BRIEF, niche, 1)[0]
        entry = to_manifest_entry(brief, niche, seed=1)
        covered = len(entry["video_terms"]) * entry["video_clip_duration"]
        self.assertGreaterEqual(covered, niche.video.target_seconds)

    def test_terms_cycle_when_there_are_not_enough_distinct_scenes(self):
        niche = load_niche("ufo-sightings")
        brief = parse_briefs(BRIEF, niche, 1)[0]
        entry = to_manifest_entry(brief, niche, seed=1)
        available = len(set(brief.search_terms) | set(niche.visual_terms))
        self.assertGreater(len(entry["video_terms"]), available)

    def test_a_stock_pack_keeps_a_small_term_list(self):
        """Stock search returns many clips per term, so more terms buy nothing."""
        niche = load_niche("ai-tools")
        brief = parse_briefs(BRIEF, niche, 1)[0]
        entry = to_manifest_entry(brief, niche, seed=1)
        self.assertLessEqual(len(entry["video_terms"]), 8)

    def test_clip_length_matches_what_the_terms_were_sized_for(self):
        niche = load_niche("ufo-sightings")
        brief = parse_briefs(BRIEF, niche, 1)[0]
        for seed in range(5):
            with self.subTest(seed=seed):
                entry = to_manifest_entry(brief, niche, seed=seed)
                covered = len(entry["video_terms"]) * entry["video_clip_duration"]
                self.assertGreaterEqual(covered, niche.video.target_seconds)


class TestImageStyleValidation(unittest.TestCase):
    def test_template_without_the_placeholder_is_rejected(self):
        with self.assertRaises(NicheError) as context:
            parse_niche(_pack(images={"base_url": "u", "model": "m", "prompt_template": "cartoon"}))
        self.assertIn("{term}", str(context.exception))

    def test_half_configured_images_are_rejected(self):
        for half in ({"base_url": "u"}, {"model": "m"}):
            with self.subTest(half):
                with self.assertRaises(NicheError):
                    parse_niche(_pack(images=half))

    def test_a_pack_without_images_is_valid(self):
        self.assertFalse(parse_niche(_pack()).images.configured)


class TestManifestForGeneratedVisuals(unittest.TestCase):
    def test_the_task_passes_the_batch_validators(self):
        niche = load_niche("ufo-sightings")
        brief = parse_briefs(BRIEF, niche, 1)[0]
        entry = to_manifest_entry(brief, niche)
        cli._validate_batch_entry_fields(
            entry, index=1, allowed_fields=set(VideoParams.model_fields.keys())
        )
        params = VideoParams(**entry)
        cli._validate_batch_task_params(
            params,
            stop_at="video",
            custom_position_is_explicit="custom_position" in entry,
            wavespeed_charge_confirmed=False,
            seedance_charge_confirmed=False,
            ofox_charge_confirmed=False,
            metaso_minimax_charge_confirmed=False,
            muapi_charge_confirmed=False,
        )
        self.assertEqual(params.video_source, "openai_image")


class TestApplyNicheStyle(unittest.TestCase):
    def test_niche_publishes_its_style_to_global_config(self):
        updates = build_updates(niche="ufo-sightings", image_key="abc")
        self.assertEqual(
            updates["openai_image_base_url"], "https://gen.pollinations.ai/v1"
        )
        self.assertEqual(updates["openai_image_model"], "flux")
        self.assertIn("{term}", updates["openai_image_prompt_template"])
        self.assertEqual(updates["openai_image_api_keys"], ["abc"])

    def test_a_pack_without_a_style_is_rejected(self):
        with self.assertRaises(ConfigError) as context:
            build_updates(niche="ai-tools")
        self.assertIn("[images]", str(context.exception))

    def test_image_key_without_a_niche_is_rejected(self):
        with self.assertRaises(ConfigError):
            build_updates(image_key="abc")

    def test_the_written_config_stays_valid_toml(self):
        from growth.configure import set_app_values

        source = '[app]\nopenai_image_model = ""\n'
        result = set_app_values(source, build_updates(niche="ufo-sightings"))
        self.assertEqual(tomllib.loads(result)["app"]["openai_image_model"], "flux")


class TestDoctorForGeneratedImages(unittest.TestCase):
    def test_unconfigured_endpoint_fails_with_the_command_to_fix_it(self):
        with patch("app.services.material.is_openai_image_enabled", return_value=False):
            result = doctor._check_generated_images()
        self.assertEqual(result.status, FAIL)
        self.assertIn("--niche", result.fix)

    def test_endpoint_returning_nothing_fails(self):
        with patch("app.services.material.is_openai_image_enabled", return_value=True):
            with patch("app.services.material.generate_images_openai", return_value=[]):
                with patch("app.config.config.app", {"openai_image_model": "flux"}):
                    result = doctor._check_generated_images()
        self.assertEqual(result.status, FAIL)

    def test_a_generated_image_passes(self):
        with patch("app.services.material.is_openai_image_enabled", return_value=True):
            with patch(
                "app.services.material.generate_images_openai", return_value=[object()]
            ):
                with patch("app.config.config.app", {"openai_image_model": "flux"}):
                    result = doctor._check_generated_images()
        self.assertEqual(result.status, OK)

    def test_niche_selects_the_source_actually_used(self):
        """Checking the global default would test a provider the batch never calls."""
        with patch.object(doctor, "_check_generated_images") as generated:
            generated.return_value = doctor.Check("materials", OK, "stub")
            results = doctor.run_checks(skip_network=True, niche_id="ufo-sightings")
            self.assertTrue(results)

        with patch.object(doctor, "_NETWORK_CHECKS", (doctor._check_materials,)):
            with patch.object(doctor, "_check_generated_images") as generated:
                generated.return_value = doctor.Check("materials", OK, "stub")
                doctor.run_checks(niche_id="ufo-sightings")
        generated.assert_called_once()

    def test_stock_pack_does_not_take_the_image_path(self):
        with patch.object(doctor, "_NETWORK_CHECKS", (doctor._check_materials,)):
            with patch.object(doctor, "_check_generated_images") as generated:
                with patch("app.config.config.app", {"video_source": "pexels"}):
                    doctor.run_checks(niche_id="ai-tools")
        generated.assert_not_called()


if __name__ == "__main__":
    unittest.main()
