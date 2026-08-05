from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ADAPTER = load_module("research_output_adapter", SCRIPTS / "research_output_adapter.py")
VISUAL_VALIDATION = load_module(
    "article_visual_validation",
    SCRIPTS / "article_visual_validation.py",
)
UPSTREAM_SCHEMA = ROOT / "schemas/vendor/research-output.v1.schema.json"
UPSTREAM_CANONICAL = ROOT / "fixtures/research-output/verified.synthetic.json"
UPSTREAM_NEGATIVE = ROOT / "fixtures/research-output/upstream-negative-cases.synthetic.json"
NEGATIVE_DESCRIPTOR = ROOT / "fixtures/research-output/negative-cases.json"
CONTENT_SCHEMA = ROOT / "schemas/content-item.schema.json"
VIDEO_SCHEMA = ROOT / "schemas/content-video-brief.v1.schema.json"
EXPECTED_INPUT_SHA256 = "89eb27ba1cafb9b39f974209bc0756934d620ee3bf7028476ad776306bb66a2f"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ResearchOutputAdapterContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        for path in (
            UPSTREAM_SCHEMA,
            UPSTREAM_CANONICAL,
            UPSTREAM_NEGATIVE,
            NEGATIVE_DESCRIPTOR,
            CONTENT_SCHEMA,
            VIDEO_SCHEMA,
        ):
            if not path.is_file():
                raise AssertionError(f"required contract fixture is missing: {path}")

    def setUp(self) -> None:
        self.temp_root = ROOT / "tests/.tmp-research-output-adapter"
        shutil.rmtree(self.temp_root, ignore_errors=True)
        self.temp_root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def generate(self, output_name: str = "positive") -> tuple[dict, Path]:
        output_dir = self.temp_root / output_name
        result = ADAPTER.adapt_research_output(
            UPSTREAM_CANONICAL,
            output_dir,
            UPSTREAM_SCHEMA,
            CONTENT_SCHEMA,
            VIDEO_SCHEMA,
        )
        return result, output_dir

    def test_contract_preflight_uses_canonical_upstream_schema(self) -> None:
        preflight = ADAPTER.contract_preflight(
            UPSTREAM_SCHEMA,
            CONTENT_SCHEMA,
            VIDEO_SCHEMA,
        )
        self.assertEqual("contract_ready", preflight["status"])
        self.assertEqual(
            "https://local.invalid/schemas/research-output.v1.schema.json",
            preflight["upstream_schema_id"],
        )
        self.assertEqual(UPSTREAM_SCHEMA.resolve(), Path(preflight["upstream_schema"]).resolve())

    def test_video_brief_schema_is_valid_draft_202012(self) -> None:
        schema = read_json(VIDEO_SCHEMA)
        Draft202012Validator.check_schema(schema)
        self.assertEqual(schema["properties"]["mode"]["const"], "draft_only")
        self.assertFalse(schema["additionalProperties"])

    def test_missing_canonical_schema_fails_closed(self) -> None:
        missing = ROOT / ".contract-fixtures/does-not-exist/research-output.v1.schema.json"
        with self.assertRaises(ADAPTER.AdapterError) as context:
            ADAPTER.discover_upstream_schema(missing)
        self.assertEqual(context.exception.code, "waiting_upstream_contract")

    def test_hash_and_identifier_material_are_deterministic(self) -> None:
        value_a = {"b": 2, "a": [1, {"z": "值"}]}
        value_b = {"a": [1, {"z": "值"}], "b": 2}
        self.assertEqual(ADAPTER.sha256_value(value_a), ADAPTER.sha256_value(value_b))
        self.assertEqual(
            ADAPTER.stable_suffix("research-1", "adapter-0.1"),
            ADAPTER.stable_suffix("research-1", "adapter-0.1"),
        )

    def test_canonical_fixture_generates_valid_draft_only_artifacts(self) -> None:
        result, output_dir = self.generate()
        self.assertEqual("generated_draft_only", result["status"])
        self.assertEqual(5, len(result["artifacts"]))

        content_path = next(output_dir.glob("*.content-item.json"))
        video_path = next(output_dir.glob("*.content-video-brief.json"))
        markdown_path = next(output_dir.glob("*.article.md"))
        html_path = next(output_dir.glob("*.article.html"))
        manifest_path = next(output_dir.glob("*.manifest.json"))

        content = read_json(content_path)
        video = read_json(video_path)
        manifest = read_json(manifest_path)
        research = read_json(UPSTREAM_CANONICAL)

        Draft202012Validator(read_json(CONTENT_SCHEMA)).validate(content)
        Draft202012Validator(read_json(VIDEO_SCHEMA)).validate(video)
        self.assertEqual([], VISUAL_VALIDATION.validate_article_visual_plan(content))
        self.assertEqual(result["content_id"], content["content_id"])
        self.assertEqual(content["content_id"], video["content_id"])
        self.assertEqual("manual-only", content["publication"]["boundary"])
        self.assertEqual("draft_only", content["channels"]["article"]["platform_metadata"]["mode"])
        self.assertEqual([], content["visual"]["assets"])
        self.assertTrue(
            all(slot["status"] == "planned" for slot in content["visual"]["article_visual_plan"]["slots"])
        )

        verified_claims = [
            claim["text"]
            for claim in research["claims"]
            if claim["claim_id"] in research["guarded_final_answer"]["claim_ids"]
        ]
        self.assertEqual(verified_claims, content["provenance"]["verified_facts"])
        research_refs = content["channels"]["article"]["platform_metadata"]["research_refs"]
        self.assertEqual(
            [item["evidence_id"] for item in research["evidence"]],
            research_refs["evidence_ids"],
        )
        self.assertEqual(research["provenance"], research_refs["upstream_provenance"])
        snapshot = content["channels"]["article"]["platform_metadata"]["research_status_snapshot"]
        self.assertEqual(research["review_status"], snapshot["review_status"])
        self.assertEqual(research["evidence_status"], snapshot["evidence_status"])
        self.assertEqual(
            [item["review_status"] for item in research["sources"]],
            [item["review_status"] for item in snapshot["sources"]],
        )

        self.assertEqual("draft_only", video["mode"])
        self.assertEqual("required", video["review_gate"]["status"])
        self.assertEqual("manual-only", video["review_gate"]["publication_boundary"])
        self.assertTrue(
            all(
                lead["status"] == "candidate"
                for scene in video["scenes"]
                for lead in scene["visual_leads"]
            )
        )
        upstream_visual_statuses = {
            lead["upstream_status"]
            for scene in video["scenes"]
            for lead in scene["visual_leads"]
            if "upstream_status" in lead
        }
        self.assertEqual({item["status"] for item in research["visual_leads"]}, upstream_visual_statuses)
        self.assertTrue(video["provenance"]["offline_only"])
        self.assertFalse(video["provenance"]["external_api_called"])
        self.assertFalse(video["provenance"]["model_memory_used_as_evidence"])
        self.assertEqual(research["provenance"]["input_files"], video["provenance"]["upstream_input_files"])

        markdown = markdown_path.read_text(encoding="utf-8")
        html = html_path.read_text(encoding="utf-8")
        self.assertIn("draft_only", markdown)
        self.assertIn("evidence:evidence_fixture_support", markdown)
        self.assertIn("source:source_fixture_primary", markdown)
        self.assertIn('name="viewport"', html)
        self.assertIn("<article>", html)

        self.assertEqual(EXPECTED_INPUT_SHA256, manifest["input"]["sha256"])
        self.assertEqual(research["provenance"], manifest["provenance"]["upstream"])
        for artifact in manifest["artifacts"]:
            self.assertEqual(artifact["sha256"], file_sha256(output_dir / artifact["path"]))

    def test_double_run_is_byte_for_byte_stable(self) -> None:
        _, output_a = self.generate("run-a")
        snapshot_a = {path.name: path.read_bytes() for path in output_a.iterdir()}
        _, output_b = self.generate("run-b")
        snapshot_b = {path.name: path.read_bytes() for path in output_b.iterdir()}
        self.assertEqual(snapshot_a, snapshot_b)
        self.assertEqual(5, len(snapshot_a))

    def test_fail_closed_negative_cases(self) -> None:
        descriptor = read_json(NEGATIVE_DESCRIPTOR)
        self.assertEqual(UPSTREAM_CANONICAL.relative_to(ROOT).as_posix(), descriptor["base_fixture"])
        self.assertEqual(UPSTREAM_NEGATIVE.relative_to(ROOT).as_posix(), descriptor["upstream_negative_fixture"])
        base = read_json(UPSTREAM_CANONICAL)

        for case in descriptor["cases"]:
            with self.subTest(case=case["id"]):
                research = copy.deepcopy(base)
                mutation = case["mutation"]
                if mutation == "remove_verified_support_object":
                    research["evidence"] = [
                        item
                        for item in research["evidence"]
                        if item["evidence_id"] != "evidence_fixture_support"
                    ]
                elif mutation == "mark_used_source_stale":
                    research["sources"][0]["freshness"] = "stale"
                elif mutation == "reopen_verified_selected_claim_contradiction":
                    research["contradictions"][0]["resolution_status"] = "unresolved"
                    research["contradictions"][0]["evidence_status"] = "verified"
                    contradiction_id = research["contradictions"][0]["evidence_id"]
                    next(
                        item for item in research["evidence"] if item["evidence_id"] == contradiction_id
                    )["evidence_status"] = "verified"
                    next(
                        item
                        for item in research["evidence_links"]
                        if item["evidence_id"] == contradiction_id
                    )["evidence_status"] = "verified"
                elif mutation == "append_same_source_quote_over_aggregate_limit":
                    extra_quote = copy.deepcopy(research["quotable_facts"][0])
                    extra_quote["text"] = "A second short quote from the same source exceeds the combined per-source allowance."
                    extra_quote["word_count"] = 12
                    research["quotable_facts"].append(extra_quote)
                elif mutation == "append_sensitive_blocker_unknown":
                    research["unknowns"].append(
                        {
                            "code": "sensitive_content_unredacted",
                            "severity": "blocker",
                            "message": "Synthetic unredacted personal data marker.",
                            "claim_id": None,
                            "source_id": None,
                            "evidence_id": None,
                        }
                    )
                elif mutation == "point_evidence_to_missing_claim":
                    support = next(
                        item
                        for item in research["evidence"]
                        if item["evidence_id"] == "evidence_fixture_support"
                    )
                    support["claim_id"] = "claim_missing"
                elif mutation == "point_evidence_to_missing_source":
                    support = next(
                        item
                        for item in research["evidence"]
                        if item["evidence_id"] == "evidence_fixture_support"
                    )
                    support["source_id"] = "source_missing"
                elif mutation == "mark_verified_support_as_ai_synthesis":
                    support = next(
                        item
                        for item in research["evidence"]
                        if item["evidence_id"] == "evidence_fixture_support"
                    )
                    support["ai_synthesis"] = True
                elif mutation == "downgrade_support_to_candidate":
                    support = next(
                        item
                        for item in research["evidence"]
                        if item["evidence_id"] == "evidence_fixture_support"
                    )
                    support["evidence_status"] = "candidate"
                    support["review_status"] = "candidate"
                    next(
                        item
                        for item in research["evidence_links"]
                        if item["evidence_id"] == "evidence_fixture_support"
                    )["evidence_status"] = "candidate"
                elif mutation == "block_guarded_answer":
                    research["guarded_final_answer"].update(
                        {
                            "status": "blocked",
                            "ready": False,
                            "answer": None,
                            "claim_ids": [],
                            "evidence_ids": [],
                            "source_ids": [],
                            "blocking_gates": ["candidate_evidence_not_reviewed"],
                        }
                    )
                    research["completeness"]["ready_for_answer"] = False
                    research["completeness"]["blocking_gates"] = [
                        "candidate_evidence_not_reviewed"
                    ]
                else:
                    self.fail(f"unknown fixture mutation: {mutation}")

                fixture_path = self.temp_root / f"{case['id']}.json"
                fixture_path.write_text(
                    json.dumps(research, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8",
                )
                output_dir = self.temp_root / f"{case['id']}-output"
                with self.assertRaises(ADAPTER.AdapterError) as context:
                    ADAPTER.adapt_research_output(
                        fixture_path,
                        output_dir,
                        UPSTREAM_SCHEMA,
                        CONTENT_SCHEMA,
                        VIDEO_SCHEMA,
                    )
                self.assertEqual(case["expected_status"], context.exception.code)
                self.assertFalse(output_dir.exists())


if __name__ == "__main__":
    unittest.main()
