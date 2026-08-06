#!/usr/bin/env python3
"""Fail-closed Content Studio consumer for the canonical model gateway.

This module owns only Content Studio policy and receipt shaping. Provider
catalogs, credentials, consent validation, routing, transport, fallback, and
the execution ledger remain owned by the separately installed Gateway.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


CONSUMER_VERSION = "0.1.0"
GATEWAY_VERSION = "0.2.0"
GATEWAY_COMMIT = "c5f3ec49644453e0cddb56350e3b243b49e0f7da"
CONSUMER_ID = "content_studio"
STUDIO_ROOT = Path(__file__).resolve().parents[1]
INPUT_SCHEMA = STUDIO_ROOT / "schemas/content-studio-gateway-input-manifest.v1.schema.json"
RESULT_SCHEMA = STUDIO_ROOT / "schemas/content-studio-gateway-result.v1.schema.json"
RESEARCH_SCHEMA = STUDIO_ROOT / "schemas/vendor/research-output.v1.schema.json"
CONTENT_SCHEMA = STUDIO_ROOT / "schemas/content-item.schema.json"

TASK_ROUTES: dict[str, dict[str, str]] = {
    "rewrite": {
        "gateway_task": "rewrite",
        "capability": "text",
        "purpose": "Content Studio synthetic draft-only rewrite candidate; preserve cited evidence and add no facts.",
    },
    "outline": {
        "gateway_task": "draft_only",
        "capability": "text",
        "purpose": "Content Studio synthetic draft-only outline candidate.",
    },
    "summary": {
        "gateway_task": "draft_only",
        "capability": "text",
        "purpose": "Content Studio synthetic draft-only summary candidate; preserve cited evidence and add no facts.",
    },
    "visual_candidate:text": {
        "gateway_task": "draft_only",
        "capability": "text",
        "purpose": "Content Studio synthetic draft-only visual description candidate; preserve evidence and add no facts.",
    },
    "visual_candidate:vision": {
        "gateway_task": "vision_candidate",
        "capability": "vision",
        "purpose": "Content Studio synthetic consented vision candidate; treat the image as visual input, not factual evidence.",
    },
}

NUMBER_OR_DATE = re.compile(r"(?<![A-Za-z])(?:\d{1,4}(?:[-/.]\d{1,2}){1,2}|\d+(?:\.\d+)?%?)(?![A-Za-z])")


class ConsumerError(RuntimeError):
    """A safe, classified consumer failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_value(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ConsumerError("artifact_contract_invalid", "JSON root must be an object")
    return value


def validate_instance(instance: dict[str, Any], schema_path: Path, label: str) -> None:
    schema = load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(instance),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    if errors:
        locations = [".".join(str(part) for part in error.absolute_path) or "$" for error in errors[:8]]
        raise ConsumerError("artifact_contract_invalid", f"{label} schema failed at: {', '.join(locations)}")


def _gateway_api() -> dict[str, Any]:
    try:
        import model_provider_gateway as gateway
        from model_provider_gateway.adapters import (
            ADAPTER_REQUEST_SCHEMA,
            adapter_contract,
            validate_adapter_request,
        )
        from model_provider_gateway.execution import LiteLLMTransport
    except ImportError as exc:
        raise ConsumerError("gateway_unavailable", "canonical Gateway package is unavailable") from exc
    if getattr(gateway, "__version__", None) != GATEWAY_VERSION:
        raise ConsumerError("profile_or_route_drift", "canonical Gateway version drift detected")
    return {
        "gateway": gateway,
        "adapter_request_schema": ADAPTER_REQUEST_SCHEMA,
        "adapter_contract": adapter_contract,
        "validate_adapter_request": validate_adapter_request,
        "transport_class": LiteLLMTransport,
    }


def _resolve_repo_path(manifest_path: Path, relative_path: str) -> Path:
    candidate = (manifest_path.parent / relative_path).resolve()
    try:
        candidate.relative_to(STUDIO_ROOT.resolve())
    except ValueError as exc:
        raise ConsumerError("artifact_contract_invalid", "artifact path escapes the Content Studio checkout") from exc
    if not candidate.is_file():
        raise ConsumerError("artifact_contract_invalid", "declared artifact does not exist")
    return candidate


def _verify_file_ref(path: Path, expected: dict[str, Any]) -> None:
    if path.stat().st_size != expected["bytes"] or sha256_bytes(path.read_bytes()) != expected["sha256"]:
        raise ConsumerError("artifact_contract_invalid", "artifact size or SHA-256 drift detected")


def _short_hash(prefix: str, value: str) -> str:
    return f"{prefix}_{sha256_bytes(value.encode('utf-8'))[:24]}"


def derive_context(source: dict[str, Any], artifact_type: str) -> dict[str, Any]:
    if artifact_type == "research-output":
        # Reuse the existing research evidence gate rather than reimplementing it.
        from research_output_adapter import validate_research_semantics

        validate_research_semantics(source)
        answer = source["guarded_final_answer"]
        return {
            "source_refs": [item["source_id"] for item in source["sources"]],
            "claim_refs": list(answer["claim_ids"]),
            "evidence_refs": list(answer["evidence_ids"]),
            "unknown_refs": [
                {"code": item["code"], "severity": item["severity"]}
                for item in source["unknowns"]
            ],
            "contradiction_refs": [
                {
                    "claim_id": item["claim_id"],
                    "evidence_id": item["evidence_id"],
                    "resolution_status": item["resolution_status"],
                    "evidence_status": item["evidence_status"],
                }
                for item in source["contradictions"]
            ],
            "review_state": {
                "review_status": source["review_status"]["overall"],
                "evidence_status": source["evidence_status"]["overall"],
                "ready_for_answer": source["completeness"]["ready_for_answer"],
                "guarded_answer_status": answer["status"],
            },
            "risk_state": "reviewed_synthetic_low_risk",
        }
    if artifact_type == "content-item":
        sources = source["provenance"]["sources"]
        source_refs = [_short_hash("source", item["ref"]) for item in sources]
        facts = source["provenance"]["verified_facts"]
        uncertainties = source["provenance"]["uncertainties"]
        gate_states = {key: value["status"] for key, value in source["gates"].items()}
        risk_state = "blocked" if "blocked" in gate_states.values() else (
            "needs_review" if any(value != "pass" for value in gate_states.values()) else "reviewed_synthetic_low_risk"
        )
        return {
            "source_refs": source_refs,
            "claim_refs": [_short_hash("fact", item) for item in facts],
            "evidence_refs": [
                source_refs[index]
                for index, item in enumerate(sources)
                if item["evidence_status"] in {"verified", "user-confirmed"}
            ],
            "unknown_refs": [
                {"code": _short_hash("unknown", item), "severity": "warning"}
                for item in uncertainties
            ],
            "contradiction_refs": [],
            "review_state": {"gates": gate_states, "content_status": source["status"]},
            "risk_state": risk_state,
        }
    raise ConsumerError("artifact_contract_invalid", "unsupported source artifact type")


def _task_route(task: str, capability: str) -> dict[str, str]:
    key = f"{task}:{capability}" if task == "visual_candidate" else task
    route = TASK_ROUTES.get(key)
    if route is None or route["capability"] != capability:
        raise ConsumerError("capability_mismatch", "task and capability do not match the reviewed Content Studio route")
    return route


def prepare_input(manifest_path: Path, task: str, capability: str) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    manifest = load_json(manifest_path)
    validate_instance(manifest, INPUT_SCHEMA, "input manifest")
    if manifest["synthetic_only"] is not True:
        raise ConsumerError("artifact_contract_invalid", "current release accepts synthetic_only inputs only")
    task_route = _task_route(task, capability)

    source_ref = manifest["source_artifact"]
    source_path = _resolve_repo_path(manifest_path, source_ref["relative_path"])
    _verify_file_ref(source_path, source_ref)
    source = load_json(source_path)
    source_schema = RESEARCH_SCHEMA if source_ref["artifact_type"] == "research-output" else CONTENT_SCHEMA
    validate_instance(source, source_schema, source_ref["artifact_type"])
    schema_id = load_json(source_schema).get("$id")
    if source_ref["schema_id"] != schema_id:
        raise ConsumerError("artifact_contract_invalid", "source schema identity drift detected")
    derived = derive_context(source, source_ref["artifact_type"])
    if manifest["context"] != derived:
        raise ConsumerError("profile_or_route_drift", "manifest context no longer matches the source artifact")

    gateway_paths: list[Path] = []
    for item in manifest["gateway_artifacts"]:
        path = _resolve_repo_path(manifest_path, item["relative_path"])
        _verify_file_ref(path, item)
        if item["purpose"] != task_route["purpose"]:
            raise ConsumerError("consent_invalid", "artifact purpose does not match the reviewed task purpose")
        if capability == "text" and item["mime_type"] not in {"application/json", "text/markdown", "text/plain"}:
            raise ConsumerError("capability_mismatch", "text tasks accept text artifacts only")
        if capability == "vision" and item["mime_type"] not in {"image/png", "image/jpeg", "image/webp"}:
            raise ConsumerError("capability_mismatch", "vision tasks accept image artifacts only")
        gateway_paths.append(path)
    if capability == "text" and source_path not in gateway_paths:
        raise ConsumerError("artifact_contract_invalid", "text execution must include the versioned source artifact")
    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "manifest_sha256": sha256_bytes(manifest_path.read_bytes()),
        "source": source,
        "source_path": source_path,
        "gateway_paths": gateway_paths,
        "task_route": task_route,
    }


def _validate_authorization(
    prepared: dict[str, Any], profile: dict[str, Any], route: dict[str, Any], consent: dict[str, Any]
) -> dict[str, Any]:
    api = _gateway_api()
    gateway = api["gateway"]
    gateway.validate_profile(profile)
    gateway.validate_route_plan(route)
    gateway.validate_consent(consent, route=route)
    if route["fallback_policy"]["enabled"] or len(route["members"]) != 1:
        raise ConsumerError("fallback_unauthorized", "Content Studio forbids fallback and multi-member routes")
    task_route = prepared["task_route"]
    if route["capability"] != task_route["capability"]:
        raise ConsumerError("capability_mismatch", "route capability drift detected")
    if consent["purpose"] != task_route["purpose"]:
        raise ConsumerError("consent_invalid", "consent purpose drift detected")
    expected_public = [
        {
            "sha256": item["sha256"],
            "bytes": item["bytes"],
            "mime_type": item["mime_type"],
            "data_type": item["data_type"],
            "purpose": item["purpose"],
        }
        for item in prepared["manifest"]["gateway_artifacts"]
    ]
    actual_public = [
        {key: item[key] for key in ("sha256", "bytes", "mime_type", "data_type", "purpose")}
        for item in consent["artifacts"]
    ]
    if actual_public != expected_public:
        raise ConsumerError("consent_invalid", "consent does not cover the exact declared artifact manifest")
    adapter_request = {
        "schema": api["adapter_request_schema"],
        "consumer_id": CONSUMER_ID,
        "task": task_route["gateway_task"],
        "required_capabilities": [task_route["capability"]],
        "route_id": route["route_id"],
        "route_revision": route["route_revision"],
        "consent_hash": consent["consent_sha256"],
        "input_manifest_hash": consent["input_policy"]["input_manifest_sha256"],
    }
    api["validate_adapter_request"](adapter_request, api["adapter_contract"](CONSUMER_ID))
    return api


def _safe_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, ConsumerError):
        return exc.code, str(exc)
    message = str(exc).casefold()
    if "expired" in message:
        return "consent_expired", "consent is missing, expired, or outside the exact input scope"
    if "consent" in message:
        return "consent_invalid", "consent is missing, expired, or outside the exact input scope"
    if "fallback" in message or "multiple route" in message:
        return "fallback_unauthorized", "fallback or a multi-member route is not authorized"
    if "capability" in message:
        return "capability_mismatch", "task, route, profile, and artifact capabilities do not match"
    if "drift" in message or "identity" in message or "revision" in message:
        return "profile_or_route_drift", "profile, route, consent, or context drift detected"
    if "artifact" in message or "schema" in message or "manifest" in message:
        return "artifact_contract_invalid", "versioned artifact or manifest validation failed"
    return "blocked_validation", "Gateway consumer validation failed closed"


def _base_receipt(
    *,
    prepared: dict[str, Any] | None,
    manifest_path: Path,
    task: str,
    capability: str,
    profile: dict[str, Any] | None,
    route: dict[str, Any] | None,
    consent: dict[str, Any] | None,
    fingerprint: str,
) -> dict[str, Any]:
    manifest = prepared["manifest"] if prepared else (load_json(manifest_path) if manifest_path.is_file() else {})
    source_ref = manifest.get("source_artifact", {})
    context = manifest.get("context", {
        "source_refs": [], "claim_refs": [], "evidence_refs": [], "unknown_refs": [],
        "contradiction_refs": [], "review_state": {}, "risk_state": "unknown",
    })
    task_route = TASK_ROUTES.get(f"{task}:{capability}" if task == "visual_candidate" else task, {})
    return {
        "schema_version": "content-studio-gateway-result.v1",
        "receipt_id": f"csgr_{fingerprint[:24]}",
        "request_fingerprint": fingerprint,
        "status": "blocked",
        "mode": "draft_only",
        "task": task,
        "input_ref": {
            "artifact_id": source_ref.get("artifact_id", "unknown"),
            "artifact_type": source_ref.get("artifact_type", "research-output"),
            "schema_id": source_ref.get("schema_id", "unknown"),
            "sha256": source_ref.get("sha256", "0" * 64),
            "bytes": source_ref.get("bytes", 1),
            "manifest_sha256": prepared["manifest_sha256"] if prepared else (
                sha256_bytes(manifest_path.read_bytes()) if manifest_path.is_file() else "0" * 64
            ),
            "synthetic_only": bool(manifest.get("synthetic_only", True)),
        },
        "context_snapshot": context,
        "gateway_contract": {
            "commit": GATEWAY_COMMIT,
            "version": GATEWAY_VERSION,
            "consumer_id": CONSUMER_ID,
            "gateway_task": task_route.get("gateway_task", "draft_only"),
            "capability": task_route.get("capability", capability),
            "request_schema": "model_provider_gateway.execution_request.v1",
            "result_schema": "model_provider_gateway.execution_result.v1",
        },
        "profile_ref": {
            "profile_id": profile.get("profile_id") if profile else None,
            "profile_revision": profile.get("route_revision") if profile else None,
            "provider_id": profile.get("provider_id") if profile else None,
            "model": (profile.get("model") or profile.get("deployment")) if profile else None,
        },
        "route_receipt": {
            "route_id": route.get("route_id") if route else None,
            "route_revision": route.get("route_revision") if route else None,
            "fallback_enabled": route.get("fallback_policy", {}).get("enabled") if route else None,
        },
        "consent_receipt": {
            "schema": consent.get("schema") if consent else None,
            "consent_hash": consent.get("consent_sha256") if consent else None,
            "input_manifest_hash": consent.get("input_policy", {}).get("input_manifest_sha256") if consent else None,
            "purpose": consent.get("purpose") if consent else None,
            "expires_at": consent.get("expires_at") if consent else None,
        },
        "execution_result": None,
        "candidate": None,
        "review": {
            "status": "required",
            "fact_state_change": "none",
            "source_refs_preserved": True,
            "unknowns_preserved": True,
            "contradictions_preserved": True,
            "risk_state_preserved": True,
            "review_state_preserved": True,
            "publication_boundary": "manual-only",
            "automatic_publish": False,
            "automatic_image_generation": False,
            "automatic_fallback_allowed": False,
        },
        "error_class": None,
        "error_message": None,
    }


def _candidate_from_result(
    execution_result: dict[str, Any], prepared: dict[str, Any], task: str
) -> dict[str, Any]:
    output = execution_result.get("output") or {}
    text = output.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ConsumerError("unsupported_fact_risk", "model output is empty or unstructured")
    if (
        execution_result.get("redacted_audit", {}).get("transport") == "loopback_stub"
        and prepared["manifest"]["synthetic_only"] is True
        and text in {"mock-ok", "mock-vision-ok"}
    ):
        payload = {
            "candidate_text": text,
            "claim_refs": [],
            "source_refs": [],
            "new_factual_claims": [],
        }
    else:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConsumerError("unsupported_fact_risk", "model output must be a structured candidate envelope") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "candidate_text", "claim_refs", "source_refs", "new_factual_claims"
        }:
            raise ConsumerError("unsupported_fact_risk", "model output candidate envelope is incomplete")
    candidate_text = payload["candidate_text"]
    claim_refs = payload["claim_refs"]
    source_refs = payload["source_refs"]
    new_claims = payload["new_factual_claims"]
    if not isinstance(candidate_text, str) or not candidate_text.strip():
        raise ConsumerError("unsupported_fact_risk", "candidate text is empty")
    if not isinstance(claim_refs, list) or not set(claim_refs) <= set(prepared["manifest"]["context"]["claim_refs"]):
        raise ConsumerError("unsupported_fact_risk", "candidate references unsupported claims")
    if not isinstance(source_refs, list) or not set(source_refs) <= set(prepared["manifest"]["context"]["source_refs"]):
        raise ConsumerError("unsupported_fact_risk", "candidate references unsupported sources")
    if new_claims != []:
        raise ConsumerError("unsupported_fact_risk", "model introduced a factual claim not present in the input")
    if text not in {"mock-ok", "mock-vision-ok"}:
        source_text = prepared["source_path"].read_text(encoding="utf-8")
        novel_numbers = sorted(set(NUMBER_OR_DATE.findall(candidate_text)) - set(NUMBER_OR_DATE.findall(source_text)))
        if novel_numbers:
            raise ConsumerError("unsupported_fact_risk", "candidate introduced an unsupported number or date")
    return {
        "type": "visual_description" if task == "visual_candidate" else "text",
        "text": candidate_text,
        "output_sha256": sha256_bytes(candidate_text.encode("utf-8")),
        "model_output_status": "candidate_only",
        "evidence_role": "not_evidence",
        "supported_claim_refs": claim_refs,
        "supported_source_refs": source_refs,
        "new_factual_claims": [],
    }


def execute_content_task(
    manifest_path: Path,
    receipt_path: Path,
    *,
    task: str,
    capability: str,
    profile: dict[str, Any],
    route: dict[str, Any],
    consent: dict[str, Any],
    ledger_path: Path,
    transport: Any | None = None,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    receipt_path = receipt_path.resolve()
    fingerprint = sha256_value({
        "consumer_version": CONSUMER_VERSION,
        "gateway_commit": GATEWAY_COMMIT,
        "manifest_sha256": sha256_bytes(manifest_path.read_bytes()) if manifest_path.is_file() else "missing",
        "task": task,
        "capability": capability,
        "profile_revision": profile.get("route_revision"),
        "route_revision": route.get("route_revision"),
        "consent_hash": consent.get("consent_sha256"),
    })
    if receipt_path.is_file():
        existing = load_json(receipt_path)
        if existing.get("request_fingerprint") == fingerprint:
            validate_instance(existing, RESULT_SCHEMA, "existing result receipt")
            return existing
        raise ConsumerError("receipt_conflict", "receipt path is already bound to a different request")

    prepared: dict[str, Any] | None = None
    receipt = _base_receipt(
        prepared=None, manifest_path=manifest_path, task=task, capability=capability,
        profile=profile, route=route, consent=consent, fingerprint=fingerprint,
    )
    try:
        prepared = prepare_input(manifest_path, task, capability)
        receipt = _base_receipt(
            prepared=prepared, manifest_path=manifest_path, task=task, capability=capability,
            profile=profile, route=route, consent=consent, fingerprint=fingerprint,
        )
        api = _validate_authorization(prepared, profile, route, consent)
        if transport is None:
            raise ConsumerError("gateway_unavailable", "this public consumer release accepts loopback transport only")
        request = api["gateway"].build_execution_request(
            profile,
            route,
            consent,
            consumer_id=CONSUMER_ID,
            task=prepared["task_route"]["gateway_task"],
            max_estimated_cost_usd=0.01,
            request_id=f"content-studio:{fingerprint[:32]}",
        )
        execution_result = api["gateway"].execute_model_request(
            request,
            profile=profile,
            route=route,
            consent=consent,
            ledger_path=ledger_path,
            transport=transport,
            secret_resolver=lambda _ref: "synthetic-loopback-only",
        )
        receipt["execution_result"] = execution_result
        if not execution_result["ok"] or execution_result["status"] != "completed":
            receipt["error_class"] = execution_result.get("error_class") or "gateway_execution_failed"
            receipt["error_message"] = execution_result.get("error_message") or "Gateway execution failed closed"
        else:
            receipt["candidate"] = _candidate_from_result(execution_result, prepared, task)
            receipt["status"] = "candidate"
    except Exception as exc:
        code, message = _safe_error(exc)
        receipt["status"] = "blocked"
        receipt["candidate"] = None
        if code == "unsupported_fact_risk":
            receipt["execution_result"] = None
        receipt["error_class"] = code
        receipt["error_message"] = message

    validate_instance(receipt, RESULT_SCHEMA, "result receipt")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    receipt_path.write_text(payload, encoding="utf-8", newline="\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--consent", type=Path, required=True)
    parser.add_argument("--task", choices=("rewrite", "outline", "summary", "visual_candidate"), required=True)
    parser.add_argument("--capability", choices=("text", "vision"), default="text")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--loopback-stub-url")
    args = parser.parse_args()
    transport = None
    if args.loopback_stub_url:
        transport = _gateway_api()["transport_class"].loopback_stub(args.loopback_stub_url)
    result = execute_content_task(
        args.manifest,
        args.receipt,
        task=args.task,
        capability=args.capability,
        profile=load_json(args.profile),
        route=load_json(args.route),
        consent=load_json(args.consent),
        ledger_path=args.ledger,
        transport=transport,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "candidate" else 2


if __name__ == "__main__":
    raise SystemExit(main())
