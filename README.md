# Content Studio PoC

An offline-first, file-based content contract workbench.

This public release contains only reusable code, JSON Schemas, an optional Sveltia CMS editor shell, deterministic tests, and three synthetic fixtures. It does **not** contain any private content library, operational output, customer data, platform receipts, downloaded media, or publishing automation.

## What is included

- `content-item.schema.json`: the shared content object contract.
- `content-video-brief.v1.schema.json`: a draft-only handoff contract for video planning.
- `research-output.v1.schema.json`: a provenance-recorded snapshot of the upstream research contract.
- `research_output_adapter.py`: a fail-closed offline adapter that produces:
  - a validated content item;
  - draft-only Markdown and responsive HTML;
  - a draft-only video brief;
  - a deterministic manifest with hashes.
- `article_visual_pipeline.py`: a human-reviewed visual candidate and insertion adapter; candidate search is optional and never runs during offline validation.
- `article_visual_validation.py`: deterministic rights, evidence-role, alt-text, and generated-image opt-in gates.
- A minimal Sveltia CMS shell pinned to a fixed release and SRI hash.
- Synthetic positive and negative fixtures.

## Safety boundary

The adapter never publishes content, calls an external model, fabricates footage, or upgrades candidate evidence to fact. Generated outputs are `draft_only` and `manual-only`. Missing evidence, stale sources, unresolved contradictions, AI synthesis presented as fact, quote-limit violations, and unredacted sensitive markers fail closed.

## Quick start

```powershell
python -m pip install -r requirements.txt
python scripts/validate_release.py
python scripts/research_output_adapter.py --check-contract-only
python scripts/research_output_adapter.py --research-output fixtures/research-output/verified.synthetic.json --output-dir .release-tmp/example
```

To use the optional editor, change `example-owner/content-studio-poc` in `admin/config.yml` to your own repository. The editor is not an authorization or publication system.

## Release hygiene

```powershell
python scripts/release_audit.py .
git diff --check
python -m unittest discover -s tests -v
```

See `docs/article-visual-pipeline.md`, `docs/research-output-consumer-guide.md`, `CONTENT_NOTICE.md`, `THIRD_PARTY_NOTICES.md`, and `RELEASE_PROVENANCE.json` before redistributing.
