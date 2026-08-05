# Article Visual Pipeline

The article visual pipeline is a human-in-the-loop adapter for planning, reviewing, and inserting visuals into a Markdown article.

## Boundaries

- Candidate search is optional and runs only when the user explicitly invokes `search-openverse`.
- Unit tests use an injected fake fetcher and make no network request.
- Search results remain `needs-review`; metadata returned by a search API is not proof of reuse rights.
- An asset can be inserted only when its registered `rights_status` is `verified` and meaningful alt text exists.
- Stock or generated imagery cannot serve as factual evidence.
- Generated-image routes require an explicit opt-in recorded in the content object.
- The adapter writes local review artifacts only. It does not publish or authenticate to a platform.

## Offline checks

```powershell
python -m unittest tests.test_article_visual_pipeline tests.test_article_visual_validation -v
python scripts/validate_release.py
```

## Optional candidate search

```powershell
python scripts/article_visual_pipeline.py search-openverse --item path/to/content-item.json --output .release-tmp/candidates.json
```

Before selecting a result, open its original landing page and verify the license, attribution, identifiable-person, trademark, and contextual-use constraints. Keep the selected asset outside this code repository unless redistribution rights are independently established.
