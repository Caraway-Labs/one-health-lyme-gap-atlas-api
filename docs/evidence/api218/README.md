# API #218: Summit Compass North verification

Scope: replace legacy documentation branding using the owner's approved assets.
Owning contract: `docs/documentation-surfaces.md`; API policy and workspace ADR
0002 boundaries are retained. No architecture decision changes or new dependencies.
Security middleware, CSP, CORS, authentication, public projection, operation IDs,
payloads, provenance, and upstream renderer controls are unchanged. Existing Atlas
image alternative text is retained. Rollback is a reviewed revert and deployment.

## Asset provenance

Copied without modification from the owner-approved
`atlas_summit_compass_north_corrected_review` Windows handoff (2026-10-10).
Only the two artifacts needed by these documentation surfaces were imported.

| File | Dimensions | Bytes | SHA-256 |
| --- | --- | --- | --- |
| favicon.ico | 16/32/48 | 7210 | 9a3f9ae0df3e926a33c2e8dbbabcf1c1b4770515ed75a7c673ad8304d7c98d19 |
| favicon-256x256.png | 256 x 256 | 65877 | b803ae4b899ff9a318bba31a049ee3ecd1d65fd0b80530da5b9361aa457eac23 |

The ICO matches Web #528 / PR #531's committed `public/favicon.ico`:
Git blob `d0497d13f18aeedeaaa6bda66a32d5cd3b6390a2`, checked at Web
`origin/main` commit `cb4ac32ff3a27f35f26024be70f0964bf90a03b7`.
The 256-pixel PNG is from the same approved local set; Web does not commit that size.
The legacy SVG and its served route are removed.

## Local verification

Unmodified application at `http://127.0.0.1:8218` returned 200 for `/docs`,
`/redoc`, `/openapi.json`, and both versioned asset URLs. ICO returns
`image/x-icon`; PNG returns `image/png`. Both return
`Cache-Control: public, max-age=86400`; repeated/no-cache requests return the same
approved bytes. Versioned paths avoid reuse of the legacy SVG cache key.
Documentation HTML and schema retain their existing cache behavior.

`uv build` passed. ZIP inspection of the built wheel confirmed both assets match
the source bytes and no legacy favicon SVG is packaged. Docker copies `src` into
both builder and runtime stages; hosted CI remains the production container gate.
Local `docker build -t atlas-api218:quality .` could not connect to the unavailable
Docker Desktop Linux engine.

Both regenerated OpenAPI artifacts are identical to base `344ea28` outside
`info`; only the public description image and `x-logo` URLs changed. No generated
Web client refresh is required for this presentation-only metadata change.

## Browser screenshots

Before images capture the live production legacy branding on 2026-10-10 Denver:

- [Swagger before](swagger-before.jpg)
- [ReDoc before](redoc-before.jpg)

After images use a credential-free local fixture preview at port 8219. Because
the unchanged canonical metadata convention uses absolute production asset URLs,
the preview rewrites only those branding URL hosts to localhost and the Swagger
server URL to the local fixture server. This preview adjustment is not shipped.
The standard renderers load the actual approved assets (natural size 256 pixels).
Swagger Try It Out executed `GET /v1/indicators`, returning 200 with synthetic
`alpha`/`beta` fixture records; no warehouse/provider call was made.

- [Swagger after](swagger-after.jpg)
- [ReDoc after](redoc-after.jpg)
- [Swagger Try It Out](swagger-try-it-out.jpg)
- [Swagger at 390 pixels](swagger-after-narrow.jpg)
- [ReDoc at 390 pixels](redoc-after-narrow.jpg)

Production acceptance remains pending independent OpenAI `.agent` coordinator
review, explicit human merge authorization, and confirmed deployment. After
deployment, verify both production renderers, icon bytes/MIME/cache behavior,
regular and fresh/incognito browser tabs, and issue closure before removing only
this task's branch and worktree. No merge or deployment was initiated here.
