# Model Provider Gateway consumer

Acting tool/model: Codex (GPT-5.6 Sol)
Timestamp: 2026-08-06 09:10:42 +08:00

Content Studio is a direct, fail-closed consumer of the separately installed
Model Provider Gateway `0.2.0` at commit
`c5f3ec49644453e0cddb56350e3b243b49e0f7da`. No Provider catalog, secret,
consent, fallback, retry, or transport implementation is copied into this
repository.

## Supported tasks

| Content Studio task | Gateway task | Capability | Output |
| --- | --- | --- | --- |
| `rewrite` | `rewrite` | `text` | text candidate |
| `outline` | `draft_only` | `text` | text candidate |
| `summary` | `draft_only` | `text` | text candidate |
| `visual_candidate` | `draft_only` | `text` | visual-description candidate |
| `visual_candidate` | `vision_candidate` | `vision` | visual-description candidate |

All results remain `draft_only`, `candidate_only`, `not_evidence`, and
`manual-only`. A visual result is a review candidate; it is never promoted to
evidence and never triggers image generation.

## Input contract

`content-studio-gateway-input-manifest.v1` binds a versioned `research-output`
or `content-item` to exact SHA-256, byte count, local relative path, purpose,
and consent scope. The current public release accepts `synthetic_only=true`
fixtures only. The manifest also freezes source, claim, evidence, unknown,
contradiction, risk, and review references. The adapter recomputes this context
from the source object and rejects drift.

For text tasks, the versioned source JSON must itself be in the Gateway consent
manifest. For vision, the source JSON stays local and the exact image artifact
requires a separate vision consent. Paths must stay within the checkout.

## Output contract

`content-studio-gateway-result.v1` stores only public identity and redacted
execution fields: source and manifest hashes, Gateway contract version, profile
and route revisions, consent receipt, canonical result envelope, candidate,
and review boundary. It never stores a credential, raw local path, private
header, or implicit fallback.

Provider-like output must be a JSON object with exactly:

```json
{
  "candidate_text": "...",
  "claim_refs": [],
  "source_refs": [],
  "new_factual_claims": []
}
```

Claim and source references must be subsets of the input manifest;
`new_factual_claims` must be empty. Novel numbers or dates are blocked. The
literal `mock-ok` and `mock-vision-ok` responses are accepted only from the
Gateway's loopback transport and only for synthetic fixtures.

## Fail-closed behavior

The adapter returns a blocked receipt for Gateway unavailability, missing or
expired consent, consent-purpose or artifact drift, profile/route drift,
capability mismatch, fallback or multi-member routes, 429, 5xx, timeout,
artifact hash drift, and unsupported factual additions. It performs no retry
and no fallback.

## Offline verification

Install the Gateway separately at the pinned commit, then expose its `src`
directory only for the verification process. No Provider key is needed.

```powershell
$env:PYTHONPATH = 'D:\path\to\model-provider-gateway\src'
python -m unittest discover -s tests -p 'test_model_gateway_consumer.py' -v
python scripts/validate_release.py
```

The tests use the Gateway's loopback-only HTTP stub, synthetic profile and
consent objects, and temporary synthetic images. They do not call a real
Provider or retain media.

## CLI

The CLI consumes already-built canonical profile, route, and consent JSON. It
does not create or repair consent. A loopback example requires an ephemeral
Gateway stub URL:

```powershell
python scripts/model_gateway_consumer.py `
  --manifest fixtures/model-gateway/research-input-manifest.synthetic.json `
  --profile .release-tmp/profile.json `
  --route .release-tmp/route.json `
  --consent .release-tmp/consent.json `
  --task outline --capability text `
  --receipt .release-tmp/outline-result.json `
  --ledger .release-tmp/gateway-ledger.jsonl `
  --loopback-stub-url http://127.0.0.1:PORT
```

This public release accepts loopback transport only. Enabling a real Provider
requires a separately reviewed consumer increment; there is no hidden or
implicit Provider mode in this CLI.
