# Contributing

Contributions should preserve the repository's offline, deterministic, and fail-closed boundaries.

- Use synthetic fixtures only.
- Keep generated output out of Git.
- Do not add publishing, account login, engagement automation, or model calls to the core adapter.
- Preserve evidence status and provenance.
- Run `python scripts/validate_release.py` and `git diff --check`.
- Update `THIRD_PARTY_NOTICES.md` and `RELEASE_PROVENANCE.json` when dependencies or vendored contracts change.