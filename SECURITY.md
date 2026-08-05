# Security

Do not put secrets, browser profiles, access tokens, private content, customer records, or platform exports in this repository.

Before opening a pull request, run:

```powershell
python scripts/release_audit.py .
python scripts/validate_release.py
```

Report a suspected vulnerability privately through GitHub's security advisory interface. Do not include real sensitive data in a public issue.