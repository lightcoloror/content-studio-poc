# Research Output Consumer Guide

`research_output_adapter.py` consumes a validated `research-output.v1` document and derives draft-only content artifacts. The vendored input Schema is authoritative for transport; adapter success alone never upgrades research evidence.

## Verified-input gate

Conversion proceeds only when all of the following are true:

- JSON Schema validation passes;
- `completeness.ready_for_answer` is true;
- `completeness.blocking_gates` is empty;
- `guarded_final_answer.status` is `verified`;
- `guarded_final_answer.ready` is true;
- the guarded answer is present.

Candidate, reviewed, verified, unknown, contradiction, and AI-synthesis states are preserved. A blocked input produces no publish-ready article.

## Outputs

The adapter writes a deterministic local bundle containing:

- a validated content item;
- draft-only Markdown;
- optional responsive HTML;
- `content-video-brief.v1` with scene-level evidence references and unknowns;
- a manifest containing artifact hashes and provenance.

Visual leads are candidates, not claims that footage or media exists. All outputs remain manual-only and outside the repository's tracked content directories.

## Commands

```powershell
python scripts/research_output_adapter.py --check-contract-only
python scripts/research_output_adapter.py --research-output fixtures/research-output/verified.synthetic.json --output-dir .release-tmp/example
python scripts/validate_release.py
```
