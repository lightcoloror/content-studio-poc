#!/usr/bin/env python3
"""Run the standalone contract, fixture, adapter, hygiene, and unit checks."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
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


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-tests", action="store_true")
    args = parser.parse_args()

    adapter = load_module("research_output_adapter", SCRIPTS / "research_output_adapter.py")
    release_audit = load_module("release_audit", SCRIPTS / "release_audit.py")
    research_schema = ROOT / "schemas/vendor/research-output.v1.schema.json"
    content_schema = ROOT / "schemas/content-item.schema.json"
    video_schema = ROOT / "schemas/content-video-brief.v1.schema.json"
    for schema_path in (research_schema, content_schema, video_schema):
        schema = load_json(schema_path)
        Draft202012Validator.check_schema(schema)

    fixture = load_json(ROOT / "fixtures/research-output/verified.synthetic.json")
    Draft202012Validator(load_json(research_schema)).validate(fixture)
    preflight = adapter.contract_preflight(research_schema, content_schema, video_schema)
    if preflight["status"] != "contract_ready":
        print("contract preflight did not become ready", file=sys.stderr)
        return 1
    errors = release_audit.audit_tree(ROOT)
    if errors:
        for error in errors:
            print(f"release_audit: {error}", file=sys.stderr)
        return 1
    print("PASS: three Schemas, synthetic input, adapter preflight, and release hygiene.")
    if args.no_tests:
        return 0
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-v"],
        cwd=ROOT,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())