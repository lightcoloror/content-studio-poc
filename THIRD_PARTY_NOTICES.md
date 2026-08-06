# Third-Party Notices

## Sveltia CMS

- Project: https://github.com/sveltia/sveltia-cms
- Version: `0.172.4`
- Release tag object: `3a32b2449855d0c3a37e4942ac69f88c6161032c`
- Commit: `d4b633b85d883e00ae84b9b0582211d2f2966489`
- License: MIT
- Use: exact-version CDN script in `admin/index.html`; not vendored.
- CDN artifact SHA-256: `328b38111dc10c212bb502da345a302218f3b83ea7fbd0705e9e30050f16ac8d`
- SRI: `sha384-Sj4Mfbg9OjjwG2ZE/YeUYu7xbZRTXGFg/wa/nszj3KzItKuRLEmGqSpV5C9YI2Ge`

Copyright (c) Sveltia CMS contributors. Permission is granted under the MIT License; see the upstream repository for its complete license text.

## jsonschema

- Project: https://github.com/python-jsonschema/jsonschema
- Audited commit: `81f7a761cbaca107d3d261c1288155655b98ba08`
- Runtime version pinned by this release: `4.25.1`
- License: MIT
- Use: Python package dependency; no source is vendored.

Copyright (c) Julian Berman. Permission is granted under the MIT License; see the upstream repository for its complete license text.


## markdown-it-py

- Project: https://github.com/executablebooks/markdown-it-py
- Runtime version pinned by this release: `4.2.0`
- Official tag commit: `36c5f547144df2d01970a5792d68c71a3380b227`
- License: MIT
- Use: CommonMark parsing and safe Markdown-to-HTML structure in the article visual adapter; no source is vendored.

The installed distribution also carries the upstream markdown-it acknowledgement under MIT.

## mdurl

- Project: https://github.com/executablebooks/mdurl
- Runtime version resolved with this release: `0.1.2`
- Official tag object / commit: `fb26485560d0589d4ed56255a4fcf87d09752dbf` / `596bf1c8752de45fa576a52c315d6d8cc5bb1a4e`
- License: MIT
- Use: transitive URL parsing dependency of `markdown-it-py`; no source is vendored.

## Upstream research-output contract snapshot

`schemas/vendor/research-output.v1.schema.json` is an exact first-party contract snapshot from `question-research-poc` commit `51ca30ac24bf84dd838b30569aee4e3bc6c3f59e`, SHA-256 `3c2e0e9a05034b1ffa3693735da128c7d3e114ddb155f11668120b974fee1009`. It is included under this repository's Apache-2.0 license with provenance preserved. The snapshot remains byte-identical to the canonical schema at question-research-poc commit `2d83fc7bb0114417b045726de69f7a3f6b46242b`.

## Optional Model Provider Gateway integration

- Project: `model-provider-gateway`
- Canonical commit: `c5f3ec49644453e0cddb56350e3b243b49e0f7da`
- Version: `0.2.0`
- License: AGPL-3.0-only
- Use: separately installed execution service/library consumed through its versioned public Python contract; no Gateway source, Provider catalog, credential implementation, or LiteLLM source is vendored here.

The Apache-2.0 license of this repository does not relicense the separately installed Gateway. Operators who enable that optional integration must comply with the Gateway and its dependency licenses independently.

## Not bundled

Lucide, Pexels media, personal photographs, model weights, hosted services, Provider credentials, LiteLLM source, and external platform SDKs are not bundled by this release.
