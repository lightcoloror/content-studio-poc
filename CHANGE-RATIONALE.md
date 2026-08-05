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