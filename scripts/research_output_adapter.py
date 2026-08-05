#!/usr/bin/env python3
"""Thin, fail-closed bridge from canonical research-output.v1 to content artifacts.

The input schema is owned by question-research-poc. This public release ships
an exact, provenance-recorded schema snapshot so the adapter can run offline.
Callers can still provide a newer canonical schema explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from article_visual_pipeline import render_article_html


ADAPTER_VERSION = "0.1.1"
STUDIO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTENT_SCHEMA = STUDIO_ROOT / "schemas/content-item.schema.json"
DEFAULT_VIDEO_SCHEMA = STUDIO_ROOT / "schemas/content-video-brief.v1.schema.json"
UPSTREAM_SCHEMA_CANDIDATES = (
    STUDIO_ROOT / "schemas/vendor/research-output.v1.schema.json",
)
SENSITIVE_UNKNOWN_TOKENS = (
    "sensitive",
    "privacy",
    "pii",
    "personal_data",
    "health_data",
    "unredacted",
    "敏感",
    "隐私",
    "未脱敏",
)


class AdapterError(RuntimeError):
    """A fail-closed, user-actionable adapter failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def stable_suffix(*parts: str, length: int = 16) -> str:
    material = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:length]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise AdapterError("invalid_json_root", f"{path} must contain a JSON object")
    return value


def discover_upstream_schema(explicit: Path | None = None) -> Path:
    if explicit is not None:
        if explicit.is_file():
            return explicit.resolve()
        raise AdapterError(
            "waiting_upstream_contract",
            f"canonical research-output.v1 schema does not exist: {explicit}",
        )
    for candidate in UPSTREAM_SCHEMA_CANDIDATES:
        if candidate.is_file():
            return candidate.resolve()
    searched = ", ".join(str(path) for path in UPSTREAM_SCHEMA_CANDIDATES)
    raise AdapterError(
        "waiting_upstream_contract",
        "canonical research-output.v1 schema is not available; searched: " + searched,
    )


def validate_instance(instance: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(instance),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    if not errors:
        return
    details = []
    for error in errors[:12]:
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        details.append(f"{location}: {error.message}")
    raise AdapterError("schema_validation_failed", f"{label}: " + "; ".join(details))


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _index(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        identifier = item.get(key)
        if not isinstance(identifier, str) or not identifier:
            raise AdapterError("invalid_reference_graph", f"{label} contains an empty {key}")
        if identifier in result:
            raise AdapterError("invalid_reference_graph", f"{label} duplicates {identifier}")
        result[identifier] = item
    return result


def _parse_timestamp(value: str) -> str:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise AdapterError("invalid_created_at", f"created_at is not an ISO timestamp: {value!r}") from error
    return value


def _ref(ref_type: str, ref_id: str) -> dict[str, str]:
    return {"ref_type": ref_type, "ref_id": ref_id}


def validate_research_semantics(research: dict[str, Any]) -> dict[str, Any]:
    """Validate cross-object and publication-safety rules not expressible in JSON Schema."""

    sources = _index(research["sources"], "source_id", "sources")
    claims = _index(research["claims"], "claim_id", "claims")
    evidence = _index(research["evidence"], "evidence_id", "evidence")
    answer = research["guarded_final_answer"]
    completeness = research["completeness"]
    review = research["review_status"]

    if answer["status"] != "verified" or answer["ready"] is not True or not answer["answer"]:
        raise AdapterError("guarded_answer_not_verified", "guarded_final_answer must be ready and verified")
    if not answer["claim_ids"]:
        raise AdapterError("guarded_answer_without_claims", "verified answer must reference at least one claim")
    if completeness["ready_for_answer"] is not True or completeness["blocking_gates"]:
        raise AdapterError("research_incomplete", "completeness has unresolved blocking gates")
    if review["overall"] != "verified":
        raise AdapterError("research_not_verified", "review_status.overall must be verified")
    if research["evidence_status"]["overall"] != "verified":
        raise AdapterError("evidence_not_verified", "evidence_status.overall must be verified")
    if research["provenance"]["offline_only"] is not True:
        raise AdapterError("non_offline_research", "research provenance must declare offline_only=true")
    if research["provenance"]["external_api_called"] is not False:
        raise AdapterError("external_api_provenance", "research provenance must declare external_api_called=false")
    if research["provenance"]["model_memory_used_as_evidence"] is not False:
        raise AdapterError("model_memory_as_evidence", "model memory cannot be used as evidence")

    canonical_links = {
        (
            item["evidence_id"],
            item["claim_id"],
            item["source_id"],
            item["relation"],
            item["evidence_status"],
        )
        for item in research["evidence_links"]
    }
    for item in research["evidence"]:
        if item["claim_id"] not in claims:
            raise AdapterError(
                "evidence_claim_ref_missing",
                f"{item['evidence_id']} references missing claim {item['claim_id']}",
            )
        if item["source_id"] not in sources:
            raise AdapterError(
                "evidence_source_ref_missing",
                f"{item['evidence_id']} references missing source {item['source_id']}",
            )
        link = (
            item["evidence_id"],
            item["claim_id"],
            item["source_id"],
            item["relation"],
            item["evidence_status"],
        )
        if link not in canonical_links:
            raise AdapterError(
                "evidence_link_mismatch",
                f"evidence_links does not preserve the canonical relation for {item['evidence_id']}",
            )
    for claim in research["claims"]:
        missing_source_ids = [source_id for source_id in claim["source_ids"] if source_id not in sources]
        if missing_source_ids:
            raise AdapterError(
                "claim_source_ref_missing",
                f"{claim['claim_id']} references missing sources: {', '.join(missing_source_ids)}",
            )
        for evidence_id in claim["evidence_ids"]:
            if evidence_id not in evidence:
                raise AdapterError(
                    "missing_evidence_reference",
                    f"{claim['claim_id']} references missing evidence {evidence_id}",
                )
            if evidence[evidence_id]["claim_id"] != claim["claim_id"]:
                raise AdapterError(
                    "invalid_reference_graph",
                    f"{claim['claim_id']} lists evidence owned by {evidence[evidence_id]['claim_id']}",
                )
    evidence_ids = set(evidence)
    for link in research["evidence_links"]:
        if link["evidence_id"] not in evidence_ids:
            raise AdapterError(
                "evidence_link_mismatch",
                f"evidence_links references missing evidence {link['evidence_id']}",
            )
    if len(canonical_links) != len(research["evidence_links"]):
        raise AdapterError("evidence_link_mismatch", "evidence_links contains duplicate canonical rows")
    if len(research["evidence_links"]) != len(research["evidence"]):
        raise AdapterError(
            "evidence_link_mismatch",
            "evidence_links must contain exactly one canonical row for every evidence object",
        )

    blocker_unknowns = [
        item for item in research["unknowns"] if item["severity"] == "blocker"
    ]
    if blocker_unknowns:
        codes = ", ".join(item["code"] for item in blocker_unknowns)
        sensitive = any(
            token in f"{item['code']} {item['message']}".casefold()
            for item in blocker_unknowns
            for token in SENSITIVE_UNKNOWN_TOKENS
        )
        code = "sensitive_content_blocked" if sensitive else "blocking_unknowns"
        raise AdapterError(code, f"blocking unknowns remain: {codes}")

    selected_claims: list[dict[str, Any]] = []
    used_evidence: dict[str, dict[str, Any]] = {}
    used_sources: dict[str, dict[str, Any]] = {}
    guarded_evidence_ids = set(answer["evidence_ids"])
    guarded_source_ids = set(answer["source_ids"])

    for claim_id in answer["claim_ids"]:
        claim = claims.get(claim_id)
        if claim is None:
            raise AdapterError("missing_claim_reference", f"guarded answer references missing claim {claim_id}")
        if claim["review_status"] not in {"reviewed", "verified"}:
            raise AdapterError("claim_not_reviewed", f"{claim_id} is not reviewed")
        if claim["evidence_status"] != "verified":
            raise AdapterError("claim_evidence_not_verified", f"{claim_id} evidence is not verified")

        supporting_ids = claim["supporting_evidence_ids"]
        if not supporting_ids:
            raise AdapterError("missing_supporting_evidence", f"{claim_id} has no supporting evidence")
        verified_support_count = 0
        for evidence_id in supporting_ids:
            item = evidence.get(evidence_id)
            if item is None:
                raise AdapterError(
                    "missing_evidence_reference",
                    f"{claim_id} references missing evidence {evidence_id}",
                )
            if item["claim_id"] != claim_id:
                raise AdapterError(
                    "invalid_reference_graph",
                    f"{evidence_id} points to {item['claim_id']} instead of {claim_id}",
                )
            if item["source_id"] not in sources:
                raise AdapterError(
                    "missing_source_reference",
                    f"{evidence_id} references missing source {item['source_id']}",
                )
            if item["source_id"] not in claim["source_ids"]:
                raise AdapterError(
                    "invalid_reference_graph",
                    f"{claim_id} does not list evidence source {item['source_id']}",
                )
            if item["ai_synthesis"] is True and item["evidence_status"] == "verified":
                raise AdapterError(
                    "ai_synthesis_not_fact",
                    f"{evidence_id} is AI synthesis and cannot be verified factual evidence",
                )
            if item["evidence_status"] == "candidate" or item["review_status"] == "candidate":
                raise AdapterError(
                    "candidate_evidence_not_reviewed",
                    f"{evidence_id} remains candidate or unreviewed",
                )
            if (
                item["relation"] == "supports"
                and item["evidence_status"] == "verified"
                and item["review_status"] == "reviewed"
                and item["ai_synthesis"] is False
            ):
                verified_support_count += 1
                if evidence_id not in guarded_evidence_ids:
                    raise AdapterError(
                        "guarded_answer_missing_evidence_ref",
                        f"guarded answer omits verified support {evidence_id}",
                    )
                if item["source_id"] not in guarded_source_ids:
                    raise AdapterError(
                        "guarded_answer_missing_source_ref",
                        f"guarded answer omits source {item['source_id']}",
                    )
                used_evidence[evidence_id] = item
                used_sources[item["source_id"]] = sources[item["source_id"]]
        if verified_support_count == 0:
            raise AdapterError(
                "missing_verified_support",
                f"{claim_id} has no human-reviewed, non-AI verified support",
            )
        selected_claims.append(claim)

    for evidence_id in answer["evidence_ids"]:
        item = evidence.get(evidence_id)
        if item is None:
            raise AdapterError("missing_evidence_reference", f"guarded answer references {evidence_id}")
        if (
            item["evidence_status"] != "verified"
            or item["review_status"] != "reviewed"
            or item["ai_synthesis"] is not False
        ):
            raise AdapterError(
                "unverified_guarded_evidence",
                f"guarded evidence {evidence_id} is not reviewed, verified, non-AI evidence",
            )

    unresolved = [
        item
        for item in research["contradictions"]
        if item["claim_id"] in answer["claim_ids"]
        and item["resolution_status"] in {"candidate", "unresolved"}
    ]
    if unresolved:
        refs = ", ".join(f"{item['claim_id']}:{item['evidence_id']}" for item in unresolved)
        code = (
            "unresolved_verified_contradiction"
            if any(item["evidence_status"] == "verified" for item in unresolved)
            else "unresolved_contradiction"
        )
        raise AdapterError(code, f"selected claims have unresolved contradictions: {refs}")

    for source_id, source in used_sources.items():
        if source["freshness"] in {"stale", "unknown"}:
            raise AdapterError("stale_or_unknown_source", f"{source_id} freshness is {source['freshness']}")
        if source["quality"] in {"poor", "unknown"}:
            raise AdapterError("low_quality_source", f"{source_id} quality is {source['quality']}")
        if source["ref_status"] == "missing":
            raise AdapterError("source_ref_missing", f"{source_id} has no resolvable reference")
        if source["hash"]["status"] == "mismatch":
            raise AdapterError("source_hash_mismatch", f"{source_id} source hash does not match")
        if source["hash"]["status"] == "missing" or not source["hash"]["value"]:
            raise AdapterError("source_hash_missing", f"{source_id} has no usable source hash")
        if source["review_status"] != "reviewed":
            raise AdapterError("source_not_reviewed", f"{source_id} has not been reviewed")

    quote_totals: dict[str, int] = {}
    for fact in research["quotable_facts"]:
        quote_totals[fact["source_id"]] = quote_totals.get(fact["source_id"], 0) + fact["word_count"]
    over_limit = {source_id: count for source_id, count in quote_totals.items() if count > 25}
    if over_limit:
        detail = ", ".join(f"{source_id}={count}" for source_id, count in sorted(over_limit.items()))
        raise AdapterError("copyright_quote_limit", f"quoted words exceed 25 per source: {detail}")

    return {
        "selected_claims": selected_claims,
        "used_evidence": list(used_evidence.values()),
        "used_sources": list(used_sources.values()),
        "warnings": _unique(
            list(completeness["warnings"])
            + list(answer["warnings"])
            + [item["message"] for item in research["unknowns"]]
        ),
    }


def _source_ref(source: dict[str, Any], research_id: str) -> str:
    return (
        source.get("archive_path")
        or source.get("url")
        or f"research-output://{research_id}/source/{source['source_id']}"
    )


def _content_source_type(source: dict[str, Any]) -> str:
    source_type = source["source_type"].casefold()
    if any(token in source_type for token in ("official", "government", "regulator")):
        return "official-web"
    if "code" in source_type:
        return "open-source-code"
    if any(token in source_type for token in ("local", "archive", "workspace")):
        return "workspace-file"
    if "user" in source_type:
        return "user-confirmed"
    return "candidate-evidence"


def _content_evidence_status(source: dict[str, Any], used_source_ids: set[str]) -> str:
    if (
        source["source_id"] in used_source_ids
        and source["review_status"] == "reviewed"
        and source["quality"] == "good"
        and source["freshness"] in {"current", "not_applicable"}
    ):
        return "verified"
    if source["review_status"] == "reviewed":
        return "partially-verified"
    return "unverified"


def build_status_snapshot(research: dict[str, Any]) -> dict[str, Any]:
    """Preserve upstream review/evidence states without upgrading their meaning."""

    return {
        "review_status": research["review_status"],
        "evidence_status": research["evidence_status"],
        "completeness": research["completeness"],
        "guarded_final_answer": {
            key: research["guarded_final_answer"][key]
            for key in (
                "status",
                "ready",
                "claim_ids",
                "evidence_ids",
                "source_ids",
                "blocking_gates",
                "warnings",
            )
        },
        "sources": [
            {
                "source_id": item["source_id"],
                "freshness": item["freshness"],
                "quality": item["quality"],
                "review_status": item["review_status"],
                "ref_status": item["ref_status"],
                "hash_status": item["hash"]["status"],
            }
            for item in research["sources"]
        ],
        "claims": [
            {
                "claim_id": item["claim_id"],
                "claim_status": item["claim_status"],
                "review_status": item["review_status"],
                "evidence_status": item["evidence_status"],
            }
            for item in research["claims"]
        ],
        "evidence": [
            {
                "evidence_id": item["evidence_id"],
                "claim_id": item["claim_id"],
                "source_id": item["source_id"],
                "relation": item["relation"],
                "evidence_status": item["evidence_status"],
                "review_status": item["review_status"],
                "ai_synthesis": item["ai_synthesis"],
            }
            for item in research["evidence"]
        ],
        "contradictions": research["contradictions"],
        "unknowns": research["unknowns"],
        "media_refs": [
            {
                "source_id": item["source_id"],
                "review_status": item["review_status"],
                "ref_status": item["ref_status"],
            }
            for item in research["media_refs"]
        ],
        "visual_leads": [
            {
                "source_id": item["source_id"],
                "ref": item["ref"],
                "status": item["status"],
            }
            for item in research["visual_leads"]
        ],
    }


def build_sections(research: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_by_claim: dict[str, list[dict[str, Any]]] = {}
    for item in policy["used_evidence"]:
        evidence_by_claim.setdefault(item["claim_id"], []).append(item)
    sections: list[dict[str, Any]] = []
    for index, claim in enumerate(policy["selected_claims"], start=1):
        refs = [_ref("claim", claim["claim_id"])]
        refs.extend(_ref("evidence", item["evidence_id"]) for item in evidence_by_claim[claim["claim_id"]])
        refs.extend(_ref("source", item["source_id"]) for item in evidence_by_claim[claim["claim_id"]])
        sections.append(
            {
                "section_id": f"section-{index:02d}",
                "title": f"证据结论 {index}",
                "body": claim["text"],
                "evidence_refs": refs,
            }
        )
    return sections


def _thesis(research: dict[str, Any]) -> str:
    answer = research["guarded_final_answer"]["answer"].strip()
    first_line = next((line.strip() for line in answer.splitlines() if line.strip()), "")
    first_line = re.sub(r"^[-*]\s*", "", first_line)
    first_line = re.sub(r"\s*\[claim:[^\]]+\]\s*$", "", first_line)
    return re.sub(r"^#+\s*", "", first_line) or research["question"]


def render_article_markdown(
    research: dict[str, Any],
    sections: list[dict[str, Any]],
    thesis: str,
) -> str:
    lines = [
        "<!-- draft_only: human review required; do not publish automatically -->",
        f"# {research['question'][:160]}",
        "",
        f"> 适用读者：{research['audience']}",
        "",
        "## 核心判断",
        "",
        thesis,
        "",
    ]
    for section in sections:
        reference_text = "；".join(
            f"{ref['ref_type']}:{ref['ref_id']}" for ref in section["evidence_refs"]
        )
        lines.extend(
            [
                f"## {section['title']}",
                "",
                section["body"],
                "",
                f"证据引用：{reference_text}",
                "",
            ]
        )
    if research["unknowns"]:
        lines.extend(["## 仍需保留的未知项", ""])
        for item in research["unknowns"]:
            lines.append(f"- [{item['code']}]：{item['message']}")
        lines.append("")
    lines.extend(
        [
            "## 使用边界",
            "",
            research["guarded_final_answer"]["guardrail"],
            "",
            "## 来源索引",
            "",
        ]
    )
    for source in research["sources"]:
        lines.append(
            f"- [{source['source_id']}] {source['title']} — {_source_ref(source, research['research_id'])}"
        )
    lines.extend(["", "_本文为 draft_only；事实、隐私、合规与版权仍需人工复核。_", ""])
    return "\n".join(lines)


def _visual_route(kind: str) -> str:
    value = kind.casefold()
    if "chart" in value or "data" in value:
        return "chart"
    if "icon" in value:
        return "icon"
    return "diagram"


def build_article_visual_slots(
    research: dict[str, Any],
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    section_by_source: dict[str, dict[str, Any]] = {}
    claim_by_id = {item["claim_id"]: item for item in research["claims"]}
    for section in sections:
        claim_ref = next(ref for ref in section["evidence_refs"] if ref["ref_type"] == "claim")
        claim = claim_by_id[claim_ref["ref_id"]]
        for source_id in claim["source_ids"]:
            section_by_source.setdefault(source_id, section)
    leads = research["visual_leads"][:12]
    slots: list[dict[str, Any]] = []
    for index, lead in enumerate(leads, start=1):
        section = section_by_source.get(lead["source_id"], sections[0])
        slots.append(
            {
                "slot_id": f"research-visual-{index:02d}",
                "anchor": {"kind": "heading", "value": section["title"], "occurrence": 1},
                "purpose": lead["ref"],
                "media_role": "explanatory",
                "route": _visual_route(lead["kind"]),
                "status": "planned",
                "layout": "full-width",
                "caption": "候选视觉线索；不是已存在素材，使用前需核验事实和版权。",
            }
        )
    if not slots:
        slots.append(
            {
                "slot_id": "research-visual-01",
                "anchor": {"kind": "heading", "value": sections[0]["title"], "occurrence": 1},
                "purpose": "把证据关系整理成解释性结构图；具体素材和版式待人工确认",
                "media_role": "explanatory",
                "route": "diagram",
                "status": "planned",
                "layout": "full-width",
                "caption": "结构图候选；不代表已经存在镜头或素材。",
            }
        )
    return slots


def build_content_item(
    research: dict[str, Any],
    policy: dict[str, Any],
    sections: list[dict[str, Any]],
    content_id: str,
    article_name: str,
    html_name: str,
    thesis: str,
    markdown: str,
) -> dict[str, Any]:
    created_at = _parse_timestamp(research["created_at"])
    used_source_ids = {item["source_id"] for item in policy["used_sources"]}
    uncertainties = _unique(
        [item["message"] for item in research["unknowns"]]
        + list(policy["warnings"])
        + [
            f"contradiction:{item['claim_id']}:{item['evidence_id']}:{item['resolution_status']}"
            for item in research["contradictions"]
        ]
    )
    platform_metadata = {
        "mode": "draft_only",
        "thesis": thesis,
        "audience": research["audience"],
        "sections": sections,
        "research_refs": {
            "research_id": research["research_id"],
            "claim_ids": [item["claim_id"] for item in research["claims"]],
            "evidence_ids": [item["evidence_id"] for item in research["evidence"]],
            "source_ids": [item["source_id"] for item in research["sources"]],
            "unknown_ids": [item["code"] for item in research["unknowns"]],
            "contradiction_refs": [
                f"{item['claim_id']}:{item['evidence_id']}" for item in research["contradictions"]
            ],
            "upstream_provenance": research["provenance"],
        },
        "research_status_snapshot": build_status_snapshot(research),
    }
    return _assemble_content_item(
        research,
        policy,
        sections,
        content_id,
        article_name,
        html_name,
        thesis,
        markdown,
        created_at,
        used_source_ids,
        uncertainties,
        platform_metadata,
    )


def build_video_brief(
    research: dict[str, Any],
    policy: dict[str, Any],
    content_id: str,
    input_sha256: str,
    input_schema_id: str,
    manifest_name: str,
    thesis: str,
) -> dict[str, Any]:
    evidence_by_claim: dict[str, list[dict[str, Any]]] = {}
    for item in policy["used_evidence"]:
        evidence_by_claim.setdefault(item["claim_id"], []).append(item)
    visual_by_source: dict[str, list[dict[str, Any]]] = {}
    for lead in research["visual_leads"]:
        visual_by_source.setdefault(lead["source_id"], []).append(lead)

    chapters: list[dict[str, Any]] = []
    scenes: list[dict[str, Any]] = []
    visual_counter = 0
    for index, claim in enumerate(policy["selected_claims"], start=1):
        claim_evidence = evidence_by_claim[claim["claim_id"]]
        evidence_refs = [_ref("claim", claim["claim_id"])]
        evidence_refs.extend(_ref("evidence", item["evidence_id"]) for item in claim_evidence)
        evidence_refs.extend(_ref("source", item["source_id"]) for item in claim_evidence)
        chapter_id = f"chapter-{index:02d}"
        chapters.append(
            {
                "chapter_id": chapter_id,
                "title": f"证据结论 {index}",
                "purpose": "呈现一条经过审核且有来源支持的核心判断",
                "target_duration_seconds": 45,
                "narration_draft": claim["text"],
                "evidence_refs": evidence_refs,
            }
        )
        leads: list[dict[str, Any]] = []
        for source_id in _unique([item["source_id"] for item in claim_evidence]):
            for lead in visual_by_source.get(source_id, []):
                visual_counter += 1
                leads.append(
                    {
                        "lead_id": f"visual-lead-{visual_counter:02d}",
                        "description": lead["ref"],
                        "status": "candidate",
                        "upstream_status": lead["status"],
                        "evidence_role": "explanatory",
                        "evidence_refs": [_ref("source", source_id)],
                        "rights_note": "仅为研究阶段视觉线索；实际素材使用前必须核验版权。",
                        "boundary_note": "不代表镜头或素材已经存在。",
                    }
                )
        if not leads:
            visual_counter += 1
            leads.append(
                {
                    "lead_id": f"visual-lead-{visual_counter:02d}",
                    "description": "候选：用证据卡、关键词或关系图解释本段；具体镜头和素材待人工确认",
                    "status": "candidate",
                    "evidence_role": "explanatory",
                    "evidence_refs": evidence_refs,
                    "rights_note": "未指定实际素材；后续需单独完成版权审核。",
                    "boundary_note": "这不是已存在镜头，也不是事实性画面承诺。",
                }
            )
        scenes.append(
            {
                "scene_id": f"scene-{index:02d}",
                "chapter_ref": chapter_id,
                "purpose": "用口语化旁白说明一条证据结论",
                "narration_draft": claim["text"],
                "evidence_refs": evidence_refs,
                "visual_leads": leads,
                "subtitle_candidates": [claim["text"]],
            }
        )

    attached_visual_refs = {
        lead["description"]
        for scene in scenes
        for lead in scene["visual_leads"]
    }
    for lead in research["visual_leads"]:
        if lead["ref"] in attached_visual_refs:
            continue
        visual_counter += 1
        scenes[0]["visual_leads"].append(
            {
                "lead_id": f"visual-lead-{visual_counter:02d}",
                "description": lead["ref"],
                "status": "candidate",
                "upstream_status": lead["status"],
                "evidence_role": "contextual",
                "evidence_refs": [_ref("source", lead["source_id"])],
                "rights_note": "上游只提供引用和审核状态；实际素材使用前必须重新核验版权。",
                "boundary_note": "候选上下文视觉，不作为本段事实证据，也不代表镜头已经存在。",
            }
        )

    unknowns = [
        {
            "unknown_id": f"{item['code']}-{index:02d}",
            "text": item["message"],
            "handling": "label-as-unknown" if item["severity"] == "warning" else "requires-review",
        }
        for index, item in enumerate(research["unknowns"], start=1)
    ]
    reasons = _unique(
        [
            "旁白、字幕、视觉和平台合规均需人工复核。",
            "所有 visual_leads 只是候选，不代表素材已存在或权利已核验。",
        ]
        + list(policy["warnings"])
    )
    return {
        "schema_version": "content-video-brief.v1",
        "brief_id": f"vbrief-{stable_suffix(content_id, ADAPTER_VERSION, length=20)}",
        "content_id": content_id,
        "mode": "draft_only",
        "title": research["question"][:160],
        "thesis": thesis,
        "audience": research["audience"],
        "target_duration_seconds": max(45, min(600, len(chapters) * 45)),
        "chapters": chapters,
        "scenes": scenes,
        "forbidden_expressions": _unique(
            [
                research["guarded_final_answer"]["guardrail"],
                "不得把 candidate、reviewed 或 AI synthesis 表述为 verified fact。",
                "不得把视觉候选写成已经拍到、已经取得或真实发生的镜头。",
            ]
        ),
        "unknowns": unknowns,
        "review_gate": {
            "status": "required",
            "reasons": reasons,
            "required_reviews": ["fact", "privacy", "compliance", "rights", "voice", "video-production"],
            "publication_boundary": "manual-only",
        },
        "provenance": {
            "research_id": research["research_id"],
            "input_schema_id": input_schema_id,
            "input_sha256": input_sha256,
            "adapter": "research-output-to-content",
            "adapter_version": ADAPTER_VERSION,
            "generated_at": _parse_timestamp(research["created_at"]),
            "manifest_ref": manifest_name,
            "offline_only": research["provenance"]["offline_only"],
            "external_api_called": research["provenance"]["external_api_called"],
            "model_memory_used_as_evidence": research["provenance"]["model_memory_used_as_evidence"],
            "upstream_input_files": research["provenance"]["input_files"],
        },
    }


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
    temporary.replace(path)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def adapt_research_output(
    research_path: Path,
    output_dir: Path,
    upstream_schema_path: Path,
    content_schema_path: Path = DEFAULT_CONTENT_SCHEMA,
    video_schema_path: Path = DEFAULT_VIDEO_SCHEMA,
) -> dict[str, Any]:
    research = load_json(research_path)
    upstream_schema = load_json(upstream_schema_path)
    content_schema = load_json(content_schema_path)
    video_schema = load_json(video_schema_path)
    validate_instance(research, upstream_schema, "research-output.v1")
    policy = validate_research_semantics(research)

    input_sha256 = sha256_value(research)
    content_id = f"research-{stable_suffix(research['research_id'], input_sha256, ADAPTER_VERSION, length=24)}"
    article_name = f"{content_id}.article.md"
    html_name = f"{content_id}.article.html"
    content_name = f"{content_id}.content-item.json"
    video_name = f"{content_id}.content-video-brief.json"
    manifest_name = f"{content_id}.manifest.json"
    thesis = _thesis(research)
    sections = build_sections(research, policy)
    markdown = render_article_markdown(research, sections, thesis)
    html = render_article_html(markdown, research["question"][:160])
    content_item = build_content_item(
        research,
        policy,
        sections,
        content_id,
        article_name,
        html_name,
        thesis,
        markdown,
    )
    video_brief = build_video_brief(
        research,
        policy,
        content_id,
        input_sha256,
        upstream_schema.get("$id", upstream_schema_path.name),
        manifest_name,
        thesis,
    )
    validate_instance(content_item, content_schema, "content-item")
    validate_instance(video_brief, video_schema, "content-video-brief.v1")

    texts = {
        article_name: markdown,
        html_name: html,
        content_name: _json_text(content_item),
        video_name: _json_text(video_brief),
    }
    manifest = {
        "schema_version": "research-content-adapter-manifest.v1",
        "adapter": "research-output-to-content",
        "adapter_version": ADAPTER_VERSION,
        "research_id": research["research_id"],
        "content_id": content_id,
        "mode": "draft_only",
        "generated_at": _parse_timestamp(research["created_at"]),
        "input": {
            "path": research_path.name,
            "schema_id": upstream_schema.get("$id", upstream_schema_path.name),
            "sha256": input_sha256,
        },
        "provenance": {
            "claim_ids": [item["claim_id"] for item in policy["selected_claims"]],
            "evidence_ids": [item["evidence_id"] for item in policy["used_evidence"]],
            "source_ids": [item["source_id"] for item in policy["used_sources"]],
            "unknown_ids": [item["code"] for item in research["unknowns"]],
            "contradiction_refs": [
                f"{item['claim_id']}:{item['evidence_id']}" for item in research["contradictions"]
            ],
            "upstream": research["provenance"],
            "status_snapshot": build_status_snapshot(research),
        },
        "quality": {
            "status": "human_review_required",
            "warnings": policy["warnings"],
            "publication_boundary": "manual-only",
        },
        "artifacts": [
            {"path": name, "sha256": _sha256_text(texts[name])}
            for name in sorted(texts)
        ],
    }
    texts[manifest_name] = _json_text(manifest)
    for name, value in texts.items():
        _write_text(output_dir / name, value)
    return {
        "status": "generated_draft_only",
        "content_id": content_id,
        "output_dir": str(output_dir.resolve()),
        "artifacts": [str((output_dir / name).resolve()) for name in sorted(texts)],
    }


def _assemble_content_item(
    research: dict[str, Any],
    policy: dict[str, Any],
    sections: list[dict[str, Any]],
    content_id: str,
    article_name: str,
    html_name: str,
    thesis: str,
    markdown: str,
    created_at: str,
    used_source_ids: set[str],
    uncertainties: list[str],
    platform_metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "0.2",
        "content_id": content_id,
        "title": research["question"][:160],
        "primary_format": "longform",
        "status": "review",
        "created_at": created_at,
        "updated_at": created_at,
        "tags": ["research-synthesis", "draft-only", "manual-only"],
        "provenance": {
            "sources": [
                {
                    "source_type": _content_source_type(source),
                    "ref": _source_ref(source, research["research_id"]),
                    "evidence_status": _content_evidence_status(source, used_source_ids),
                    "note": (
                        f"research source {source['source_id']}; publisher={source.get('publisher') or 'unknown'}; "
                        f"freshness={source['freshness']}; hash={source['hash']['status']}"
                    ),
                }
                for source in research["sources"]
            ],
            "verified_facts": [item["text"] for item in policy["selected_claims"]],
            "uncertainties": uncertainties,
            "forbidden_claims": _unique(
                [
                    research["guarded_final_answer"]["guardrail"],
                    "不得把 candidate、reviewed 或 AI synthesis 升级为 verified fact。",
                    "不得把候选视觉线索描述成已经拍摄或已经取得的素材。",
                ]
            ),
        },
        "gates": {
            "fact": {
                "status": "needs-review",
                "reason": "只纳入 guarded_final_answer 已验证且具备 verified support 的 claims；成稿仍需人工事实复核。",
                "reviewed_by": None,
                "reviewed_at": None,
            },
            "privacy": {
                "status": "needs-review",
                "reason": "research-output.v1 不替代发布前的隐私与脱敏复核。",
                "reviewed_by": None,
                "reviewed_at": None,
            },
            "compliance": {
                "status": "needs-review",
                "reason": "渠道、行业和表达合规需由发布者人工确认。",
                "reviewed_by": None,
                "reviewed_at": None,
            },
            "rights": {
                "status": "needs-review",
                "reason": "引用总量已通过适配器上限检查；视觉线索和最终素材版权仍需人工核验。",
                "reviewed_by": None,
                "reviewed_at": None,
            },
        },
        "intent": {
            "topic": research["question"],
            "speaker_role": "证据型综合稿编辑",
            "audience": research["audience"],
            "content_object": "证据型综合文章草稿",
            "desired_action": "帮助读者理解已核验证据、已知边界和仍待确认的问题",
            "risk_boundary": research["guarded_final_answer"]["guardrail"],
            "platform_intent": "draft_only；平台适配与发布均需人工完成",
            "main_angle": thesis,
        },
        "composition": {
            "canonical_body": markdown,
            "canonical_summary": thesis,
            "expression_mode": "evidence-led-synthesis",
            "voice_profile": "voice/source-preserved",
            "voice_profile_version": "1.0",
            "retention_mode": "full-text",
            "semantic_reversibility": {
                "status": "needs-review",
                "reverse_paraphrase": thesis,
                "meaning_drift": [],
            },
        },
        "channels": {
            "article": {
                "status": "review",
                "title": research["question"][:160],
                "body_ref": article_name,
                "summary": thesis,
                "adapter_version": ADAPTER_VERSION,
                "platform_metadata": platform_metadata,
            }
        },
        "operating_plan": {
            "production_unit": "real-problem-or-service-action",
            "origin_kind": "research-synthesis",
            "private_validation": {
                "channel": "private_conversation",
                "status": "blocked",
                "note": "完成事实、隐私、合规和声纹人工复核后才可进入发布判断。",
            },
        },
        "visual": {
            "visual_profile": "visual/evidence-led-article",
            "visual_profile_version": "0.1",
            "route": "none",
            "theme": "evidence-led-synthesis",
            "template": "article-visual-plan-candidate-only",
            "cognitive_anchors": [item["text"] for item in policy["selected_claims"][:4]],
            "assets": [],
            "render_recipe": {
                "renderer": "html-playwright",
                "renderer_version": "article_visual_pipeline.render_article_html",
                "width": 1200,
                "height": None,
                "output_format": "html",
                "output_refs": [html_name],
            },
            "article_visual_plan": {
                "article_ref": article_name,
                "workflow": "human-in-the-loop",
                "ai_generation_policy": "explicit-opt-in-only",
                "visual_mode": "diagram-led",
                "slots": build_article_visual_slots(research, sections),
            },
        },
        "qa": {
            "deterministic": [
                {
                    "name": "canonical-research-schema",
                    "status": "pass",
                    "details": "Input validates against question-research-poc research-output.v1.",
                },
                {
                    "name": "evidence-reference-graph",
                    "status": "pass",
                    "details": "Every included claim has reviewed, verified, non-AI supporting evidence and reviewed sources.",
                },
                {
                    "name": "draft-only-boundary",
                    "status": "pass",
                    "details": "No publishing action or real media asset is produced by this adapter.",
                },
            ],
            "promptfoo": {"status": "not-run", "suite": "research-output-adapter"},
            "human_review": {
                "status": "required",
                "reviewer": None,
                "reviewed_at": None,
                "note": "复核事实、隐私、合规、版权、本人声纹和渠道适配。",
            },
        },
        "publication": {"boundary": "manual-only", "receipts": []},
        "feedback": {
            "signal_status": "not-observed",
            "content_signal_status": "not-observed",
            "business_signal_status": "not-observed",
            "platform_signals": {},
            "business_signals": {},
            "learning_decision": "none",
            "next_test": "人工完成 review gates 后再决定是否进入渠道适配。",
        },
    }


def contract_preflight(
    upstream_schema_path: Path,
    content_schema_path: Path = DEFAULT_CONTENT_SCHEMA,
    video_schema_path: Path = DEFAULT_VIDEO_SCHEMA,
) -> dict[str, Any]:
    upstream_schema = load_json(upstream_schema_path)
    content_schema = load_json(content_schema_path)
    video_schema = load_json(video_schema_path)
    for schema in (upstream_schema, content_schema, video_schema):
        Draft202012Validator.check_schema(schema)
    schema_id = upstream_schema.get("$id", "")
    title = upstream_schema.get("title", "")
    marker = f"{schema_id} {title} {upstream_schema_path.name}".casefold()
    if "research-output" not in marker and "research output" not in marker:
        raise AdapterError(
            "wrong_upstream_contract",
            f"{upstream_schema_path} is not identifiable as research-output.v1",
        )
    return {
        "status": "contract_ready",
        "adapter_version": ADAPTER_VERSION,
        "upstream_schema": str(upstream_schema_path),
        "upstream_schema_id": schema_id,
        "upstream_schema_sha256": sha256_value(upstream_schema),
        "content_schema": str(content_schema_path.resolve()),
        "content_schema_sha256": sha256_value(content_schema),
        "video_schema": str(video_schema_path.resolve()),
        "video_schema_sha256": sha256_value(video_schema),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-output", type=Path)
    parser.add_argument("--research-schema", type=Path)
    parser.add_argument("--content-schema", type=Path, default=DEFAULT_CONTENT_SCHEMA)
    parser.add_argument("--video-schema", type=Path, default=DEFAULT_VIDEO_SCHEMA)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--check-contract-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        upstream_schema_path = discover_upstream_schema(args.research_schema)
        preflight = contract_preflight(
            upstream_schema_path,
            args.content_schema,
            args.video_schema,
        )
        if args.check_contract_only:
            print(json.dumps(preflight, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.research_output is None or args.output_dir is None:
            raise AdapterError(
                "missing_arguments",
                "--research-output and --output-dir are required unless --check-contract-only is used",
            )
        result = adapt_research_output(
            args.research_output,
            args.output_dir,
            upstream_schema_path,
            args.content_schema,
            args.video_schema,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except AdapterError as error:
        print(
            json.dumps(
                {"status": error.code, "message": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
