from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import model_gateway_consumer as CONSUMER
import research_output_adapter as RESEARCH_ADAPTER

try:
    from model_provider_gateway import build_consent_plan, build_profile, build_route_plan
    from model_provider_gateway.consent import consent_revision
    from model_provider_gateway.execution import LiteLLMTransport
    from model_provider_gateway.mock_server import mock_server

    GATEWAY_AVAILABLE = True
except ImportError:
    GATEWAY_AVAILABLE = False


SOURCE = ROOT / "fixtures/research-output/verified.synthetic.json"
INPUT_SCHEMA = ROOT / "schemas/content-studio-gateway-input-manifest.v1.schema.json"
RESULT_SCHEMA = ROOT / "schemas/content-studio-gateway-result.v1.schema.json"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class StructuredTransport:
    mode = "loopback_stub"

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def execute(self, **_kwargs):
        return {
            "content": json.dumps(self.payload, ensure_ascii=False),
            "usage": {"status": "reported", "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "estimated_cost_usd": 0.0,
        }


@unittest.skipUnless(GATEWAY_AVAILABLE, "canonical model-provider-gateway is not available")
class ModelGatewayConsumerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = ROOT / "tests/.tmp-model-gateway-consumer"
        shutil.rmtree(self.temp, ignore_errors=True)
        self.temp.mkdir(parents=True)
        self.source = self.temp / "source.json"
        shutil.copy2(SOURCE, self.source)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def manifest(self, task: str, capability: str = "text", image: Path | None = None) -> Path:
        research = read_json(self.source)
        route_key = f"{task}:{capability}" if task == "visual_candidate" else task
        purpose = CONSUMER.TASK_ROUTES[route_key]["purpose"]
        gateway_path = image or self.source
        manifest = {
            "schema_version": "content-studio-gateway-input-manifest.v1",
            "manifest_id": f"manifest-{task}-{capability}-synthetic",
            "synthetic_only": True,
            "source_artifact": {
                "artifact_id": research["research_id"],
                "artifact_type": "research-output",
                "schema_id": "https://local.invalid/schemas/research-output.v1.schema.json",
                "relative_path": self.source.name,
                "sha256": sha256_file(self.source),
                "bytes": self.source.stat().st_size,
                "mime_type": "application/json",
                "synthetic_only": True,
            },
            "gateway_artifacts": [{
                "artifact_id": "synthetic-image" if image else research["research_id"],
                "relative_path": gateway_path.name,
                "sha256": sha256_file(gateway_path),
                "bytes": gateway_path.stat().st_size,
                "mime_type": "image/png" if image else "application/json",
                "data_type": "synthetic_visual_input" if image else "synthetic_research_output",
                "purpose": purpose,
                "consent_required": True,
            }],
            "context": CONSUMER.derive_context(research, "research-output"),
        }
        path = self.temp / f"{task}-{capability}.manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def authorization(self, manifest_path: Path, task: str, capability: str):
        manifest = read_json(manifest_path)
        preset = "openai-gpt-5-6-luna"
        profile = build_profile(
            preset,
            "env:SYNTHETIC_GATEWAY_KEY",
            profile_id=f"profile-content-studio-{capability}",
            capabilities=[capability],
            timeout_seconds=1,
            max_attempts=1,
            max_calls=1,
            max_cost_usd=1.0,
            last_verified="2026-08-05T00:00:00+00:00",
        )
        route = build_route_plan(
            f"route-content-studio-{capability}", capability, [profile], max_calls=1, max_cost_usd=1.0
        )
        artifact_paths = [(manifest_path.parent / item["relative_path"]).resolve() for item in manifest["gateway_artifacts"]]
        purpose = manifest["gateway_artifacts"][0]["purpose"]
        data_type = manifest["gateway_artifacts"][0]["data_type"]
        consent = build_consent_plan(
            route,
            artifact_paths,
            capability=capability,
            purpose=purpose,
            data_type=data_type,
            max_calls=1,
            max_cost_usd=1.0,
        )
        return profile, route, consent

    def run_task(self, task: str, capability: str, manifest: Path, transport, suffix: str = ""):
        profile, route, consent = self.authorization(manifest, task, capability)
        receipt = self.temp / f"receipt-{task}-{capability}{suffix}.json"
        ledger = self.temp / f"ledger-{task}-{capability}{suffix}.jsonl"
        result = CONSUMER.execute_content_task(
            manifest,
            receipt,
            task=task,
            capability=capability,
            profile=profile,
            route=route,
            consent=consent,
            ledger_path=ledger,
            transport=transport,
        )
        return result, receipt, ledger, profile, route, consent

    def test_schemas_and_checked_in_fixture_are_valid(self) -> None:
        for schema_path in (INPUT_SCHEMA, RESULT_SCHEMA):
            Draft202012Validator.check_schema(read_json(schema_path))
        fixture = read_json(ROOT / "fixtures/model-gateway/research-input-manifest.synthetic.json")
        Draft202012Validator(read_json(INPUT_SCHEMA)).validate(fixture)
        prepared = CONSUMER.prepare_input(
            ROOT / "fixtures/model-gateway/research-input-manifest.synthetic.json", "outline", "text"
        )
        self.assertTrue(prepared["manifest"]["synthetic_only"])

    def test_gateway_loopback_generates_candidate_and_second_run_is_byte_stable(self) -> None:
        manifest = self.manifest("outline")
        profile, route, consent = self.authorization(manifest, "outline", "text")
        receipt = self.temp / "stable.json"
        ledger = self.temp / "stable-ledger.jsonl"
        source_before = self.source.read_bytes()
        with mock_server() as base_url:
            transport = LiteLLMTransport.loopback_stub(base_url)
            first = CONSUMER.execute_content_task(
                manifest, receipt, task="outline", capability="text", profile=profile, route=route,
                consent=consent, ledger_path=ledger, transport=transport,
            )
        receipt_bytes = receipt.read_bytes()
        second = CONSUMER.execute_content_task(
            manifest, receipt, task="outline", capability="text", profile=profile, route=route,
            consent=consent, ledger_path=ledger, transport=object(),
        )
        self.assertEqual("candidate", first["status"])
        self.assertEqual("mock-ok", first["candidate"]["text"])
        self.assertEqual("not_evidence", first["candidate"]["evidence_role"])
        self.assertEqual(first, second)
        self.assertEqual(receipt_bytes, receipt.read_bytes())
        self.assertEqual(source_before, self.source.read_bytes())

    def test_all_text_task_routes_generate_draft_only_candidates(self) -> None:
        for task in ("rewrite", "summary", "visual_candidate"):
            with self.subTest(task=task):
                manifest = self.manifest(task, "text")
                result, *_ = self.run_task(task, "text", manifest, StructuredTransport({
                    "candidate_text": "Synthetic candidate",
                    "claim_refs": [],
                    "source_refs": [],
                    "new_factual_claims": [],
                }))
                self.assertEqual("candidate", result["status"])
                self.assertEqual("draft_only", result["mode"])
                expected = "visual_description" if task == "visual_candidate" else "text"
                self.assertEqual(expected, result["candidate"]["type"])

    def test_explicit_vision_consent_generates_visual_candidate_only(self) -> None:
        image = self.temp / "synthetic.png"
        image.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99\x3d\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        manifest = self.manifest("visual_candidate", "vision", image)
        profile, route, consent = self.authorization(manifest, "visual_candidate", "vision")
        with mock_server() as base_url:
            result = CONSUMER.execute_content_task(
                manifest,
                self.temp / "vision-receipt.json",
                task="visual_candidate",
                capability="vision",
                profile=profile,
                route=route,
                consent=consent,
                ledger_path=self.temp / "vision-ledger.jsonl",
                transport=LiteLLMTransport.loopback_stub(base_url),
            )
        self.assertEqual("candidate", result["status"])
        self.assertEqual("mock-vision-ok", result["candidate"]["text"])
        self.assertFalse(result["review"]["automatic_image_generation"])

    def test_gateway_http_failures_are_preserved_and_blocked(self) -> None:
        for status, expected in ((429, "rate_limited"), (500, "provider_server_error")):
            with self.subTest(status=status):
                manifest = self.manifest("outline")
                profile, route, consent = self.authorization(manifest, "outline", "text")
                with mock_server(status=status) as base_url:
                    result = CONSUMER.execute_content_task(
                        manifest,
                        self.temp / f"http-{status}.json",
                        task="outline",
                        capability="text",
                        profile=profile,
                        route=route,
                        consent=consent,
                        ledger_path=self.temp / f"http-{status}.jsonl",
                        transport=LiteLLMTransport.loopback_stub(base_url),
                    )
                self.assertEqual("blocked", result["status"])
                self.assertEqual(expected, result["error_class"])
                self.assertIsNone(result["candidate"])

    def test_timeout_is_blocked_without_retry_or_fallback(self) -> None:
        manifest = self.manifest("outline")
        profile, route, consent = self.authorization(manifest, "outline", "text")
        with mock_server(delay_seconds=1.3) as base_url:
            result = CONSUMER.execute_content_task(
                manifest, self.temp / "timeout.json", task="outline", capability="text",
                profile=profile, route=route, consent=consent,
                ledger_path=self.temp / "timeout.jsonl", transport=LiteLLMTransport.loopback_stub(base_url),
            )
        self.assertEqual("blocked", result["status"])
        self.assertEqual("timeout", result["error_class"])
        self.assertFalse(result["review"]["automatic_fallback_allowed"])

    def test_expired_consent_fails_closed(self) -> None:
        manifest = self.manifest("outline")
        profile, route, consent = self.authorization(manifest, "outline", "text")
        consent["expires_at"] = "2000-01-01T00:00:00+00:00"
        consent["consent_sha256"] = consent_revision(consent)
        result = CONSUMER.execute_content_task(
            manifest, self.temp / "expired.json", task="outline", capability="text",
            profile=profile, route=route, consent=consent,
            ledger_path=self.temp / "expired.jsonl", transport=StructuredTransport({}),
        )
        self.assertEqual("blocked", result["status"])
        self.assertEqual("consent_expired", result["error_class"])

    def test_profile_route_drift_and_capability_mismatch_fail_closed(self) -> None:
        manifest = self.manifest("outline")
        profile, route, consent = self.authorization(manifest, "outline", "text")
        drifted = copy.deepcopy(route)
        drifted["route_revision"] = "0" * 64
        result = CONSUMER.execute_content_task(
            manifest, self.temp / "route-drift.json", task="outline", capability="text",
            profile=profile, route=drifted, consent=consent,
            ledger_path=self.temp / "route-drift.jsonl", transport=StructuredTransport({}),
        )
        self.assertEqual("profile_or_route_drift", result["error_class"])

        vision_profile = build_profile(
            "openai-gpt-5-6-luna", "env:SYNTHETIC_GATEWAY_KEY",
            profile_id="profile-wrong-capability", capabilities=["vision"], max_calls=1,
            max_cost_usd=1.0, last_verified="2026-08-05T00:00:00+00:00",
        )
        vision_route = build_route_plan("route-wrong-capability", "vision", [vision_profile], max_calls=1, max_cost_usd=1.0)
        result = CONSUMER.execute_content_task(
            manifest, self.temp / "capability.json", task="outline", capability="vision",
            profile=vision_profile, route=vision_route, consent=consent,
            ledger_path=self.temp / "capability.jsonl", transport=StructuredTransport({}),
        )
        self.assertEqual("capability_mismatch", result["error_class"])

    def test_fallback_hash_drift_gateway_unavailable_and_new_fact_are_blocked(self) -> None:
        manifest = self.manifest("outline")
        profile, route, consent = self.authorization(manifest, "outline", "text")

        fallback_route = build_route_plan(
            "route-fallback", "text", [profile], fallback_enabled=True, max_calls=1, max_cost_usd=1.0
        )
        fallback_consent = build_consent_plan(
            fallback_route, [self.source], capability="text",
            purpose=CONSUMER.TASK_ROUTES["outline"]["purpose"], data_type="synthetic_research_output",
            max_calls=1, max_cost_usd=1.0,
        )
        blocked = CONSUMER.execute_content_task(
            manifest, self.temp / "fallback.json", task="outline", capability="text",
            profile=profile, route=fallback_route, consent=fallback_consent,
            ledger_path=self.temp / "fallback.jsonl", transport=StructuredTransport({}),
        )
        self.assertEqual("fallback_unauthorized", blocked["error_class"])

        drift_manifest = read_json(manifest)
        drift_manifest["source_artifact"]["sha256"] = "0" * 64
        drift_path = self.temp / "hash-drift.manifest.json"
        drift_path.write_text(json.dumps(drift_manifest), encoding="utf-8")
        blocked = CONSUMER.execute_content_task(
            drift_path, self.temp / "hash-drift.json", task="outline", capability="text",
            profile=profile, route=route, consent=consent,
            ledger_path=self.temp / "hash-drift.jsonl", transport=StructuredTransport({}),
        )
        self.assertEqual("artifact_contract_invalid", blocked["error_class"])

        with mock.patch.object(CONSUMER, "_gateway_api", side_effect=CONSUMER.ConsumerError("gateway_unavailable", "unavailable")):
            blocked = CONSUMER.execute_content_task(
                manifest, self.temp / "unavailable.json", task="outline", capability="text",
                profile=profile, route=route, consent=consent,
                ledger_path=self.temp / "unavailable.jsonl", transport=StructuredTransport({}),
            )
        self.assertEqual("gateway_unavailable", blocked["error_class"])

        fact_transport = StructuredTransport({
            "candidate_text": "Synthetic unsupported fact 99999",
            "claim_refs": [],
            "source_refs": [],
            "new_factual_claims": ["Synthetic unsupported fact 99999"],
        })
        blocked, receipt, *_ = self.run_task("outline", "text", manifest, fact_transport, "-new-fact")
        self.assertEqual("unsupported_fact_risk", blocked["error_class"])
        self.assertIsNone(blocked["execution_result"])
        self.assertNotIn("99999", receipt.read_text(encoding="utf-8"))
        self.assertNotIn("SYNTHETIC_GATEWAY_KEY", receipt.read_text(encoding="utf-8"))
        self.assertNotIn(str(self.temp), receipt.read_text(encoding="utf-8"))


    def test_versioned_content_item_is_also_accepted_without_state_mutation(self) -> None:
        generated = self.temp / "generated"
        RESEARCH_ADAPTER.adapt_research_output(
            SOURCE,
            generated,
            ROOT / "schemas/vendor/research-output.v1.schema.json",
            ROOT / "schemas/content-item.schema.json",
            ROOT / "schemas/content-video-brief.v1.schema.json",
        )
        content_path = next(generated.glob("*.content-item.json"))
        content = read_json(content_path)
        content_before = content_path.read_bytes()
        purpose = CONSUMER.TASK_ROUTES["summary"]["purpose"]
        relative = content_path.relative_to(self.temp).as_posix()
        manifest = {
            "schema_version": "content-studio-gateway-input-manifest.v1",
            "manifest_id": "manifest-content-item-summary-synthetic",
            "synthetic_only": True,
            "source_artifact": {
                "artifact_id": content["content_id"],
                "artifact_type": "content-item",
                "schema_id": read_json(ROOT / "schemas/content-item.schema.json")["$id"],
                "relative_path": relative,
                "sha256": sha256_file(content_path),
                "bytes": content_path.stat().st_size,
                "mime_type": "application/json",
                "synthetic_only": True,
            },
            "gateway_artifacts": [{
                "artifact_id": content["content_id"],
                "relative_path": relative,
                "sha256": sha256_file(content_path),
                "bytes": content_path.stat().st_size,
                "mime_type": "application/json",
                "data_type": "synthetic_content_item",
                "purpose": purpose,
                "consent_required": True,
            }],
            "context": CONSUMER.derive_context(content, "content-item"),
        }
        manifest_path = self.temp / "content-item.manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result, *_ = self.run_task("summary", "text", manifest_path, StructuredTransport({
            "candidate_text": "Synthetic candidate",
            "claim_refs": [],
            "source_refs": [],
            "new_factual_claims": [],
        }), "-content-item")
        self.assertEqual("candidate", result["status"])
        self.assertEqual(content_before, content_path.read_bytes())
        self.assertEqual(content["content_id"], result["input_ref"]["artifact_id"])

    def test_non_synthetic_manifest_is_rejected_with_a_valid_blocked_receipt(self) -> None:
        manifest = self.manifest("outline")
        value = read_json(manifest)
        value["synthetic_only"] = False
        value["source_artifact"]["synthetic_only"] = False
        manifest.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        profile, route, consent = self.authorization(manifest, "outline", "text")
        result = CONSUMER.execute_content_task(
            manifest, self.temp / "non-synthetic.json", task="outline", capability="text",
            profile=profile, route=route, consent=consent,
            ledger_path=self.temp / "non-synthetic.jsonl", transport=StructuredTransport({}),
        )
        Draft202012Validator(read_json(RESULT_SCHEMA)).validate(result)
        self.assertEqual("blocked", result["status"])
        self.assertFalse(result["input_ref"]["synthetic_only"])
        self.assertEqual("artifact_contract_invalid", result["error_class"])

if __name__ == "__main__":
    unittest.main()
