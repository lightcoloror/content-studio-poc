import importlib.util
import unittest
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "article_visual_pipeline.py"
module_spec = importlib.util.spec_from_file_location("article_visual_pipeline", MODULE_PATH)
pipeline = importlib.util.module_from_spec(module_spec)
assert module_spec.loader is not None
module_spec.loader.exec_module(pipeline)


class ArticleVisualPipelineTests(unittest.TestCase):
    def item(self, rights_status="verified"):
        return {
            "content_id": "synthetic-community-workshop",
            "visual": {
                "assets": [{
                    "ref": "assets/workshop-step.svg",
                    "asset_type": "illustration",
                    "rights_status": rights_status,
                    "alt_text": "A fictional workshop registration step",
                }],
                "article_visual_plan": {
                    "article_ref": "article.md",
                    "workflow": "human-in-the-loop",
                    "ai_generation_policy": "explicit-opt-in-only",
                    "slots": [{
                        "slot_id": "registration-step",
                        "anchor": {"kind": "heading", "value": "Registration"},
                        "purpose": "Explain a fictional registration action",
                        "media_role": "explanatory",
                        "route": "stock-photo",
                        "status": "selected",
                        "layout": "text-left",
                        "search": {
                            "query_zh": "社区 工作坊 报名",
                            "query_en": "community workshop registration",
                            "negative_terms": ["medical procedure"],
                            "preferred_sources": ["openverse"],
                        },
                        "selected_asset_ref": "assets/workshop-step.svg",
                        "caption": "This is a fully synthetic workflow example.",
                    }],
                },
            },
        }

    def test_openverse_query_uses_modifiable_commercial_licenses(self):
        url = pipeline.build_openverse_url("community workshop", per_page=8)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(["by,by-sa,cc0,pdm"], query["license"])
        self.assertEqual(["false"], query["mature"])
        self.assertEqual(["8"], query["page_size"])

    def test_candidate_collection_filters_small_and_negative_results(self):
        def fake_fetcher(_url):
            return {"results": [
                {"id": "small", "title": "Workshop", "width": 320, "height": 240},
                {"id": "wrong", "title": "Medical procedure", "width": 1600, "height": 1200},
                {
                    "id": "good", "title": "Community workshop sign-in", "creator": "Example",
                    "width": 1600, "height": 1200, "url": "https://example.test/image.jpg",
                    "thumbnail": "https://example.test/thumb.jpg",
                    "foreign_landing_url": "https://example.test/source", "license": "by",
                },
            ]}

        manifest = pipeline.collect_openverse_candidates(
            self.item(), per_slot=3, min_width=800, fetcher=fake_fetcher
        )
        candidates = manifest["slots"][0]["candidates"]
        self.assertEqual(["openverse:good"], [item["candidate_id"] for item in candidates])
        self.assertEqual("needs-review", candidates[0]["rights_status"])
        self.assertEqual("human-review-required", manifest["selection_boundary"])

    def test_insert_selected_visual_after_heading(self):
        markdown = "# Guide\n\n## Registration\n\nFollow the fictional steps.\n"
        output = ROOT / ".release-tmp" / "article-with-visuals.md"
        rendered = pipeline.insert_selected_visuals(self.item(), markdown, output)
        self.assertLess(rendered.index("## Registration"), rendered.index('data-visual-slot-id="registration-step"'))
        self.assertLess(rendered.index('data-visual-slot-id="registration-step"'), rendered.index("Follow the fictional steps."))
        self.assertIn("This is a fully synthetic workflow example.", rendered)

    def test_existing_slot_marker_is_not_duplicated(self):
        markdown = "## Registration\n\n<!-- visual-slot:registration-step -->\n"
        rendered = pipeline.insert_selected_visuals(
            self.item(), markdown, ROOT / ".release-tmp" / "article.md"
        )
        self.assertEqual(1, rendered.count("<!-- visual-slot:registration-step -->"))

    def test_unverified_asset_cannot_be_inserted(self):
        with self.assertRaisesRegex(ValueError, "rights are not verified"):
            pipeline.insert_selected_visuals(
                self.item("needs-review"), "## Registration\n", ROOT / ".release-tmp" / "article.md"
            )

    def test_responsive_html_renderer_preserves_figure_blocks(self):
        markdown = '# Guide\n\n<figure class="article-visual article-visual--text-left"><img src="icon.svg" alt="Step icon"></figure>\n'
        page = pipeline.render_article_html(markdown, "Guide")
        self.assertIn("article-visual--text-left", page)
        self.assertIn('<img src="icon.svg" alt="Step icon">', page)
        self.assertIn("@media (max-width:640px)", page)

    def test_review_page_keeps_license_warning_and_source_link(self):
        manifest = {"content_id": "synthetic-guide", "slots": [{
            "slot_id": "slot-one", "purpose": "Explain an action", "candidates": [{
                "title": "Candidate", "creator": "Creator",
                "ref": "https://example.test/image.jpg", "thumbnail": "https://example.test/thumb.jpg",
                "landing_page_url": "https://example.test/source", "license": "by",
            }],
        }]}
        page = pipeline.render_review_html(manifest)
        self.assertIn("候选图片尚未通过权利复核", page)
        self.assertIn("https://example.test/source", page)


if __name__ == "__main__":
    unittest.main()
