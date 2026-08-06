# Change Rationale

Acting tool/model: Codex (GPT-5.6 Sol)
Timestamp: 2026-08-05 16:00:00 +08:00

## Intent

Publish the reusable Content Studio contracts and offline adapter without exposing the private content library or its Git history.

## Decision

Create a new clean Git root from an explicit allowlist. Include only admin configuration, Schemas, adapter and validation scripts, tests, documentation, and three synthetic fixtures. Keep all generated content and media outside the repository.

## Reason

The source worktree contains private domain content, operational outputs, downloaded-media references, internal routing metadata, and machine-specific paths. A branch based on that history would remain recoverable and is therefore not an adequate sanitization boundary.

## Evidence

- Source baseline: `0bec8c02f5a5d9830fc4df4738cbe76d6eaa9110`.
- The research adapter is tested against the canonical synthetic input hash.
- Sveltia is pinned to version `0.172.4`, commit `d4b633b85d883e00ae84b9b0582211d2f2966489`, with recorded SHA-256 and SRI.
- Release tests and archive scans are recorded in the release receipt.

## Effective scope

Only this clean public repository. The original Content Studio worktree and history are untouched.

## Rollback

Before publication, delete this clean release directory. After publication, make the new repository private or archive/delete it. No rollback action is required in the private source worktree.

## Reuse decision

No shared runtime module was extracted with Self Media because the two public cores solve different tasks: Content Studio adapts verified research into content artifacts; Self Media validates routing and operating contracts. Only release-hygiene commands overlap, which is not enough to justify a third package.

## 2026-08-05 22:09:08 +08:00 — sanitized public increment

- Acting tool/model: Codex (GPT-5.6 Sol)
- Intent: synchronize the reusable article-visual verification coverage and consumer documentation from the private development tree.
- Decision: add two synthetic-only visual test modules and two generic guides; declare the already-imported Markdown parser and its transitive dependency. Do not copy the domain-specific chart renderer, content objects, output artifacts, media, or private configuration.
- Reason: the visual scripts were already public but their runtime dependency and safety behavior were under-tested. The excluded renderer hardcodes domain, workstation, and internal task metadata and is not a safe minimal patch.
- Evidence: source-file hashes in RELEASE_PROVENANCE.json, local package metadata for MIT licenses, unit tests with an injected fake fetcher, release audit, Gitleaks, and clean-clone validation.
- Effective scope: public article visual validation, optional candidate-review documentation, and research-output consumer guidance only.
- Rollback: revert this single increment commit. The private development worktree is unchanged and remains the source of excluded local-only assets.

## 2026-08-06 09:10:42 +08:00 — unified Gateway consumer Wave 3

- Acting tool/model: Codex (GPT-5.6 Sol)
- Intent: let Content Studio request draft-only writing and visual-description candidates through the canonical multi-provider Gateway without duplicating the Gateway control plane.
- Decision: add two Content Studio-owned JSON Schemas, one thin Python consumer, one synthetic exact-artifact manifest, loopback-only positive and negative regression tests, and a consumer guide. Map `outline` and `summary` to Gateway `draft_only/text`; map `rewrite` directly; map text visual descriptions to `draft_only/text`; permit `vision_candidate/vision` only with exact image consent.
- Reason: Content Studio needs a stable model-consumer boundary, while Provider catalog, secrets, consent, routing, retry, fallback, transport, and execution ledger remain single-owner concerns in the Gateway. Model output must never change the research or content evidence state.
- Evidence: the new provenance hash gate also detected and corrected a pre-existing stale `content-item.schema.json` hash; Gateway `0.2.0` at `c5f3ec49644453e0cddb56350e3b243b49e0f7da`; Content Studio public base `67aa9575850c3edc79f9dbf513d4f6e1629b4491`; canonical Gateway loopback tests for text and vision; fail-closed tests for 429, 5xx, timeout, consent expiry, route drift, capability mismatch, fallback, artifact drift, Gateway unavailability, and unsupported new facts.
- Effective scope: the clean public Content Studio checkout only. Outputs remain `draft_only`, `candidate_only`, `not_evidence`, and `manual-only`. Current checked-in fixture and validation are `synthetic_only`; no real Provider is called.
- Rollback: revert the Wave 3 commit. The separately installed Gateway and the private Content Studio development worktree are unchanged.
