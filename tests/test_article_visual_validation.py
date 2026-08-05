import copy
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "article_visual_validation.py"
module_spec = importlib.util.spec_from_file_location("article_visual_validation", MODULE_PATH)
validation = importlib.util.module_from_spec(module_spec)
assert module_spec.loader is not None
module_spec.loader.exec_module(validation)


class ArticleVisualValidationTests(unittest.TestCase):
    def item(self):
        return {
            "visual": {
                "assets": [{
                    "ref": "assets/workshop-step.svg",
                    "asset_type": "illustration",
                    "rights_status": "verified",
                    "alt_text": "A fictional workshop registration step",
                }],
                "article_visual_plan": {
                    "article_ref": "article.md",
                    "workflow": "human-in-the-loop",
                    "ai_generation_policy": "explicit-opt-in-only",
                    "slots": [{
                        "slot_id": "registration-step",
                        "anchor": {"kind": "heading", "value": "Registration"},
                        "purpose": "Explain a fictional action",
                        "media_role": "explanatory",
                        "route": "icon",
                        "status": "selected",
                        "layout": "text-left",
                        "search": {"query_en": "workshop registration", "preferred_sources": ["iconify"]},
                        "selected_asset_ref": "assets/workshop-step.svg",
                    }],
                },
            }
        }

    def test_valid_plan_passes(self):
        self.assertEqual([], validation.validate_article_visual_plan(self.item()))

    def test_selected_asset_requires_verified_rights(self):
        item = self.item()
        item["visual"]["assets"][0]["rights_status"] = "needs-review"
        errors = validation.validate_article_visual_plan(item)
        self.assertTrue(any("verified rights" in message for _, message in errors))

    def test_stock_photo_cannot_be_factual_evidence(self):
        item = self.item()
        slot = item["visual"]["article_visual_plan"]["slots"][0]
        slot["media_role"] = "evidence"
        slot["route"] = "stock-photo"
        errors = validation.validate_article_visual_plan(item)
        self.assertTrue(any("cannot serve as factual evidence" in message for _, message in errors))

    def test_generated_image_requires_explicit_opt_in(self):
        item = self.item()
        item["visual"]["article_visual_plan"]["slots"][0]["route"] = "generated-image"
        errors = validation.validate_article_visual_plan(item)
        self.assertTrue(any("explicit user opt-in" in message for _, message in errors))

    def test_editorial_photo_mix_rejects_icon_only_plan(self):
        item = self.item()
        item["visual"]["article_visual_plan"]["visual_mode"] = "editorial-photo-mix"
        errors = validation.validate_article_visual_plan(item)
        self.assertTrue(any("requires a selected photo" in message for _, message in errors))

    def test_duplicate_slot_ids_fail(self):
        item = self.item()
        duplicate = copy.deepcopy(item["visual"]["article_visual_plan"]["slots"][0])
        item["visual"]["article_visual_plan"]["slots"].append(duplicate)
        errors = validation.validate_article_visual_plan(item)
        self.assertTrue(any("duplicates article visual slot" in message for _, message in errors))


if __name__ == "__main__":
    unittest.main()
