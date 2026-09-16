import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import cli
from app.models.schema import VideoParams
from growth import plan as plan_module
from growth import produce as produce_module
from growth.niche import NicheError, load_all_niches, load_niche, parse_niche

_VALID_PACK = {
    "niche": {
        "id": "demo",
        "name": "Demo Niche",
        "language": "en",
        "platforms": ["tiktok"],
    },
    "economics": {"cpm_low": 5.0, "cpm_high": 10.0, "competition": "low"},
    "audience": {"description": "demo audience", "pain_points": ["a pain"]},
    "content": {
        "angles": ["contrarian", "mechanism"],
        "system_prompt": "Write plainly.",
        "visual_terms": ["city skyline morning", "person typing keyboard closeup"],
    },
}


def _pack(**overrides):
    """Deep-ish copy of the valid pack with section overrides applied."""
    data = {key: dict(value) for key, value in _VALID_PACK.items()}
    for section, values in overrides.items():
        data.setdefault(section, {}).update(values)
    return data


class TestNichePacks(unittest.TestCase):
    def test_shipped_packs_are_valid_and_ranked_by_rpm(self):
        """A malformed pack must never reach a render, so every pack parses."""
        packs = load_all_niches()
        self.assertGreaterEqual(len(packs), 1)
        scores = [pack.score for pack in packs]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for pack in packs:
            self.assertTrue(pack.system_prompt.strip())
            self.assertGreaterEqual(len(pack.angles), 2)
            self.assertTrue(pack.video.voice_names)

    def test_shipped_pack_ids_match_filenames(self):
        for path in (Path(__file__).parent.parent.parent / "niches").glob("*.toml"):
            self.assertEqual(load_niche(path.stem).id, path.stem)

    def test_rpm_is_the_creator_share_of_cpm(self):
        niche = parse_niche(_pack())
        self.assertEqual(niche.economics.rpm_range, (2.75, 5.5))

    def test_invalid_packs_are_rejected(self):
        cases = {
            "unknown platform": {"niche": {"platforms": ["myspace"]}},
            "inverted cpm": {"economics": {"cpm_low": 40.0, "cpm_high": 1.0}},
            "empty angles": {"content": {"angles": []}},
        }
        for label, override in cases.items():
            with self.subTest(label):
                with self.assertRaises(NicheError):
                    parse_niche(_pack(**override))

    def test_missing_required_field_names_the_field(self):
        data = _pack()
        del data["audience"]["description"]
        with self.assertRaises(NicheError) as context:
            parse_niche(data)
        self.assertIn("audience", str(context.exception))

    def test_unknown_niche_id_lists_available_packs(self):
        with self.assertRaises(NicheError) as context:
            load_niche("not-a-real-niche")
        self.assertIn("available:", str(context.exception))


def _briefs_json(count=2):
    return json.dumps(
        [
            {
                "subject": f"Subject number {index}",
                "angle": "contrarian",
                "hook": f"Hook number {index}.",
                "key_points": ["point one", "point two"],
                "search_terms": ["city skyline morning"],
                "call_to_action": "Do the specific thing.",
            }
            for index in range(count)
        ]
    )


class TestBriefParsing(unittest.TestCase):
    def setUp(self):
        self.niche = parse_niche(_pack())

    def test_parses_plain_array(self):
        briefs = plan_module.parse_briefs(_briefs_json(3), self.niche, 3)
        self.assertEqual(len(briefs), 3)
        self.assertEqual(briefs[0].subject, "Subject number 0")

    def test_parses_through_a_code_fence(self):
        fenced = f"```json\n{_briefs_json(2)}\n```"
        self.assertEqual(len(plan_module.parse_briefs(fenced, self.niche, 2)), 2)

    def test_parses_when_the_model_adds_prose(self):
        noisy = f"Here are your briefs:\n{_briefs_json(2)}\nHope that helps."
        self.assertEqual(len(plan_module.parse_briefs(noisy, self.niche, 2)), 2)

    def test_duplicate_subjects_collapse(self):
        """Two identical subjects would render two near-identical videos."""
        raw = json.loads(_briefs_json(2))
        raw[1]["subject"] = raw[0]["subject"].upper() + "!"
        briefs = plan_module.parse_briefs(json.dumps(raw), self.niche, 2)
        self.assertEqual(len(briefs), 1)

    def test_entries_without_a_hook_or_points_are_dropped(self):
        raw = json.loads(_briefs_json(3))
        raw[1]["hook"] = ""
        raw[2]["key_points"] = []
        briefs = plan_module.parse_briefs(json.dumps(raw), self.niche, 3)
        self.assertEqual(len(briefs), 1)

    def test_missing_search_terms_fall_back_to_pack_visuals(self):
        raw = json.loads(_briefs_json(1))
        raw[0]["search_terms"] = []
        briefs = plan_module.parse_briefs(json.dumps(raw), self.niche, 1)
        self.assertTrue(set(briefs[0].search_terms) <= set(self.niche.visual_terms))

    def test_non_json_response_raises(self):
        for response in ("no json here", "[]", ""):
            with self.subTest(response):
                with self.assertRaises(plan_module.PlanError):
                    plan_module.parse_briefs(response, self.niche, 2)

    def test_script_prompt_carries_the_angle_and_stays_in_bounds(self):
        brief = plan_module.parse_briefs(_briefs_json(1), self.niche, 1)[0]
        prompt = brief.script_prompt(self.niche)
        self.assertIn("contrarian", prompt)
        self.assertIn("point one", prompt)
        self.assertLessEqual(len(prompt), 2000)

    def test_oversized_briefs_are_truncated_to_the_field_limit(self):
        raw = json.loads(_briefs_json(1))
        raw[0]["key_points"] = ["x" * 900 for _ in range(5)]
        brief = plan_module.parse_briefs(json.dumps(raw), self.niche, 1)[0]
        self.assertLessEqual(len(brief.script_prompt(self.niche)), 2000)


class TestManifestCompatibility(unittest.TestCase):
    """The manifest is rejected wholesale if one field name is wrong."""

    def test_entries_satisfy_the_batch_validators(self):
        allowed = set(VideoParams.model_fields.keys())
        for niche in load_all_niches():
            with self.subTest(niche.id):
                briefs = plan_module.parse_briefs(_briefs_json(2), niche, 2)
                plan_module.assign_voices(briefs, niche, seed=1)
                for index, brief in enumerate(briefs, start=1):
                    entry = plan_module.to_manifest_entry(brief, niche, seed=1)
                    cli._validate_batch_entry_fields(
                        entry, index=index, allowed_fields=allowed
                    )
                    params = VideoParams(**entry)
                    cli._validate_batch_task_params(
                        params,
                        stop_at="video",
                        custom_position_is_explicit=False,
                        wavespeed_charge_confirmed=False,
                        seedance_charge_confirmed=False,
                        ofox_charge_confirmed=False,
                        metaso_minimax_charge_confirmed=False,
                        muapi_charge_confirmed=False,
                    )

    def test_voices_rotate_across_a_batch(self):
        niche = load_all_niches()[0]
        briefs = plan_module.parse_briefs(_briefs_json(4), niche, 4)
        plan_module.assign_voices(briefs, niche, seed=3)
        voices = [brief.voice_name for brief in briefs]
        self.assertEqual(len(set(voices)), min(len(voices), len(niche.video.voice_names)))

    def test_aspect_and_length_overrides_reach_the_task(self):
        """The long-form cut is the same pack rendered 16:9 and longer."""
        niche = load_all_niches()[0]
        brief = plan_module.parse_briefs(_briefs_json(1), niche, 1)[0]
        entry = plan_module.to_manifest_entry(brief, niche, aspect="16:9", paragraphs=9)
        self.assertEqual(entry["video_aspect"], "16:9")
        self.assertEqual(entry["paragraph_number"], 9)
        params = VideoParams(**entry)
        self.assertEqual(params.video_aspect.value, "16:9")

    def test_overrides_default_to_the_pack(self):
        niche = load_all_niches()[0]
        brief = plan_module.parse_briefs(_briefs_json(1), niche, 1)[0]
        entry = plan_module.to_manifest_entry(brief, niche)
        self.assertEqual(entry["video_aspect"], niche.video.aspect)
        self.assertEqual(entry["paragraph_number"], niche.video.paragraph_number)

    def test_system_prompt_is_carried_into_every_task(self):
        niche = load_all_niches()[0]
        brief = plan_module.parse_briefs(_briefs_json(1), niche, 1)[0]
        entry = plan_module.to_manifest_entry(brief, niche)
        self.assertEqual(entry["custom_system_prompt"], niche.system_prompt.strip())


class TestPlanCreation(unittest.TestCase):
    def test_plan_writes_manifest_and_dedupes_the_next_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (
                patch.object(plan_module, "HISTORY_DIR", root / "history"),
                patch.object(plan_module, "PLANS_DIR", root / "plans"),
                patch("app.services.llm._generate_response", return_value=_briefs_json(2)),
            ):
                first = plan_module.create_plan(
                    "personal-finance", count=2, out_dir=root / "batch-1"
                )
                self.assertEqual(first["count"], 2)
                manifest = Path(first["manifest"])
                lines = [
                    json.loads(line)
                    for line in manifest.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                self.assertEqual(len(lines), 2)
                self.assertTrue(all("video_subject" in line for line in lines))

                history = plan_module.load_history("personal-finance")
                self.assertEqual(len(history), 2)

                # Same subjects returned again: the planner must notice they
                # are already covered rather than queue duplicates.
                second = plan_module.create_plan(
                    "personal-finance",
                    count=2,
                    out_dir=root / "batch-2",
                    record_history=False,
                )
                self.assertEqual(second["count"], 2)

    def test_empty_llm_response_reports_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch.object(plan_module, "HISTORY_DIR", Path(temp) / "history"),
                patch("app.services.llm._generate_response", return_value="   "),
            ):
                with self.assertRaises(plan_module.PlanError) as context:
                    plan_module.create_plan(
                        "ai-tools", count=1, out_dir=Path(temp) / "batch"
                    )
        self.assertIn("config.toml", str(context.exception))

    def test_count_must_be_positive(self):
        with self.assertRaises(plan_module.PlanError):
            plan_module.create_plan("ai-tools", count=0)


class TestCollectResults(unittest.TestCase):
    def _plan_file(self, root: Path, count: int) -> Path:
        plan = {
            "niche_id": "personal-finance",
            "niche_name": "Personal Finance",
            "language": "en",
            "hashtags": ["#a", "#b"],
            "briefs": [
                {
                    "subject": f"Subject {index}",
                    "angle": "contrarian",
                    "hook": f"Hook {index}.",
                    "call_to_action": "Do the thing.",
                    "voice_name": "en-US-JennyNeural",
                }
                for index in range(count)
            ],
        }
        path = root / "plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        return path

    def test_one_based_batch_indexes_map_to_the_right_brief(self):
        """cli.py numbers tasks from 1; an off-by-one mislabels every video."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_file = self._plan_file(root, 3)
            videos = []
            for number in range(1, 4):
                video = root / f"final-{number}.mp4"
                video.write_bytes(b"fake mp4")
                videos.append(video)
            summary = {
                "tasks": [
                    {
                        "index": number,
                        "task_id": f"task-{number}",
                        "status": "succeeded",
                        "result": {"videos": [str(videos[number - 1])]},
                    }
                    for number in range(1, 4)
                ]
            }
            with patch.object(produce_module, "OUT_DIR", root / "out"):
                records = produce_module.collect(plan_file, summary)

        self.assertEqual(
            [record["subject"] for record in records],
            ["Subject 0", "Subject 1", "Subject 2"],
        )
        self.assertTrue(all(record["files"] for record in records))

    def test_failed_task_is_recorded_without_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_file = self._plan_file(root, 1)
            summary = {
                "tasks": [
                    {
                        "index": 1,
                        "task_id": "task-1",
                        "status": "failed",
                        "failed_stage": "audio",
                        "error": "tts unreachable",
                        "result": {},
                    }
                ]
            }
            with patch.object(produce_module, "OUT_DIR", root / "out"):
                records = produce_module.collect(plan_file, summary)

        self.assertEqual(records[0]["status"], "failed")
        self.assertEqual(records[0]["failed_stage"], "audio")
        self.assertEqual(records[0]["files"], [])

    def test_caption_contains_the_hook_the_action_and_the_tags(self):
        caption = produce_module._caption(
            {"hook": "A surprising claim.", "call_to_action": "Check one number."},
            ["#finance", "#money"],
        )
        self.assertIn("A surprising claim.", caption)
        self.assertIn("Check one number.", caption)
        self.assertTrue(caption.endswith("#finance #money"))

    def test_diagnostics_prefers_error_lines_over_log_noise(self):
        message = produce_module._diagnostics(
            "\x1b[32mINFO\x1b[0m loading config\nERROR local material file does not exist",
            "",
        )
        self.assertEqual(message, "ERROR local material file does not exist")


if __name__ == "__main__":
    unittest.main()
