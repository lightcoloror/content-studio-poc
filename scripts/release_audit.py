#!/usr/bin/env python3
"""Fail-closed hygiene checks for the sanitized public release tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


SKIP_PARTS = {".git", ".release-tmp", "__pycache__", ".pytest_cache"}
FORBIDDEN_PATH_TOKENS = {
    "outputs", "draft-only", "manual-publications", "user-confirmation",
    "platform-receipt", "browser-profile", "customer-data", "crm",
}
MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".wav", ".mp3"}
ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:[\\/](?:Users|used" + "-by-codex|documents)[\\/]"),
    re.compile(r"(?i)(?:/home/|/Users/)[^\s\"']+"),
)
PRIVATE_CONTENT_MARKERS = ("保险", "医保", "养老金", "理赔", "保单", "客户", "质子重离子")
PRIVATE_IDENTIFIERS = ("灿哥", "明亚", "cange-", "codex://threads/", "used by syncthing")


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_PARTS for part in relative.parts):
            continue
        yield path, relative


def audit_tree(root: Path) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    fixture_manifest_path = root / "fixtures/fixture-manifest.json"
    if not fixture_manifest_path.is_file():
        errors.append("fixtures/fixture-manifest.json is required")
    else:
        manifest = json.loads(fixture_manifest_path.read_text(encoding="utf-8"))
        if manifest.get("synthetic_only") is not True:
            errors.append("fixture manifest must declare synthetic_only=true")
        for entry in manifest.get("fixtures", []):
            relative_path = str(entry.get("path", ""))
            if entry.get("synthetic_only") is not True:
                errors.append(f"fixture is not synthetic_only: {relative_path}")
            fixture = root / relative_path
            if not fixture.is_file():
                errors.append(f"manifest fixture is missing: {relative_path}")
            elif entry.get("sha256") != hashlib.sha256(fixture.read_bytes()).hexdigest():
                errors.append(f"fixture hash mismatch: {relative_path}")

    content_files = [
        relative.as_posix()
        for path, relative in iter_files(root / "content/items")
        if path.name != ".gitkeep"
    ] if (root / "content/items").exists() else []
    if content_files:
        errors.append("public release must not ship content/items: " + ", ".join(content_files))

    for path, relative in iter_files(root):
        rel = relative.as_posix()
        lowered_parts = {part.casefold() for part in relative.parts}
        matched = sorted(lowered_parts.intersection(FORBIDDEN_PATH_TOKENS))
        if matched:
            errors.append(f"forbidden path token {matched[0]}: {rel}")
        if path.suffix.casefold() in MEDIA_SUFFIXES:
            errors.append(f"media file is not allowed: {rel}")
        if path.stat().st_size > 3_000_000:
            errors.append(f"unexpected large file: {rel}")
        if path.suffix.casefold() in {".py", ".json", ".md", ".yml", ".yaml", ".html", ".toml", ".txt"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            if rel != "scripts/release_audit.py":
                if any(pattern.search(text) for pattern in ABSOLUTE_PATH_PATTERNS):
                    errors.append(f"personal absolute path found: {rel}")
                if any(marker.casefold() in text.casefold() for marker in PRIVATE_IDENTIFIERS):
                    errors.append(f"private identifier found: {rel}")
            if relative.parts[:2] == ("content", "items"):
                if any(marker in text for marker in PRIVATE_CONTENT_MARKERS):
                    errors.append(f"private-domain content marker found: {rel}")

    config = (root / "admin/config.yml").read_text(encoding="utf-8")
    if "example-owner/content-studio-poc" not in config:
        errors.append("admin config must keep the explicit example repository placeholder")
    if "owner_thread" in config or "used-by-codex" in config:
        errors.append("admin config contains internal routing or workstation metadata")
    index = (root / "admin/index.html").read_text(encoding="utf-8")
    if "@sveltia/cms@0.172.4" not in index:
        errors.append("Sveltia CMS is not pinned to 0.172.4")
    if "sha384-Sj4Mfbg9OjjwG2ZE/YeUYu7xbZRTXGFg/wa/nszj3KzItKuRLEmGqSpV5C9YI2Ge" not in index:
        errors.append("Sveltia CMS SRI is missing or changed")
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    errors = audit_tree(args.root)
    if errors:
        print("Release audit failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PASS: release tree contains only allowlisted code, contracts, docs, and synthetic fixtures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
