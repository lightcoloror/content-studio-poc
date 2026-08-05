"""Deterministic checks for article illustration plans."""

from __future__ import annotations

from typing import Any


SELECTED_STATUSES = {"selected", "inserted"}
SEARCH_ROUTES = {"stock-photo", "icon"}
PHOTO_ROUTES = {"personal-photo", "work-detail-photo", "stock-photo"}


def validate_article_visual_plan(item: dict[str, Any]) -> list[tuple[str, str]]:
    visual = item.get("visual", {})
    plan = visual.get("article_visual_plan")
    if not plan:
        return []

    errors: list[tuple[str, str]] = []
    assets = visual.get("assets", [])
    assets_by_ref = {asset.get("ref"): asset for asset in assets if asset.get("ref")}
    seen_slot_ids: set[str] = set()
    selected_routes: list[str] = []

    article_ref = plan.get("article_ref", "")
    if not article_ref.lower().endswith((".md", ".markdown")):
        errors.append(("visual.article_visual_plan.article_ref", "must reference a Markdown article"))

    for index, slot in enumerate(plan.get("slots", [])):
        location = f"visual.article_visual_plan.slots[{index}]"
        slot_id = slot.get("slot_id", "")
        if slot_id in seen_slot_ids:
            errors.append((f"{location}.slot_id", f"duplicates article visual slot {slot_id!r}"))
        seen_slot_ids.add(slot_id)

        route = slot.get("route")
        role = slot.get("media_role")
        status = slot.get("status")
        search = slot.get("search")
        selected_ref = slot.get("selected_asset_ref", "")

        if status in SELECTED_STATUSES:
            selected_routes.append(route)

        if route in SEARCH_ROUTES and status not in {"blocked", "skipped"} and not search:
            errors.append((f"{location}.search", f"{route} slots require a structured search intent"))

        if role == "evidence" and route in {"stock-photo", "generated-image"}:
            errors.append((f"{location}.route", "stock or generated images cannot serve as factual evidence"))

        if route == "generated-image":
            gate = visual.get("ai_image_generation", {})
            if gate.get("explicit_user_request") is not True:
                errors.append((f"{location}.route", "generated-image requires explicit user opt-in"))

        if status in SELECTED_STATUSES and not selected_ref:
            errors.append((f"{location}.selected_asset_ref", f"status {status!r} requires a selected asset"))
            continue

        if selected_ref:
            asset = assets_by_ref.get(selected_ref)
            if not asset:
                errors.append((f"{location}.selected_asset_ref", "must match visual.assets[].ref"))
                continue
            if asset.get("rights_status") != "verified":
                errors.append((f"{location}.selected_asset_ref", "selected article assets require verified rights"))
            if not slot.get("alt_text") and not asset.get("alt_text"):
                errors.append((f"{location}.alt_text", "selected article assets require meaningful alt text"))

    if plan.get("visual_mode") == "editorial-photo-mix":
        photo_count = sum(route in PHOTO_ROUTES for route in selected_routes)
        icon_count = selected_routes.count("icon")
        if photo_count == 0:
            errors.append(("visual.article_visual_plan.visual_mode", "editorial-photo-mix requires a selected photo"))
        if icon_count > 1 or icon_count > photo_count:
            errors.append(("visual.article_visual_plan.visual_mode", "editorial-photo-mix keeps icons secondary: at most one icon and never more icons than photos"))

    return errors
