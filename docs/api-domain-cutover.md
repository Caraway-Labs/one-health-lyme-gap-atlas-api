# Atlas API hostname cutover (Web #355)

The existing DigitalOcean application is `fb312ce3-e762-45b0-bf34-12d3df91eee6`.
`api.carawaylabs.com` remains PRIMARY and operational. Add
`api.onehealthatlas.org` as an ALIAS with minimum TLS 1.2; retain all other
application settings. Hosting, endpoint behavior, authentication, and data
contracts do not change.

On 2026-10-10, the alias was added using authenticated `doctl apps update`
without `--update-sources`. Comparing the complete live spec before and after,
with only the new alias removed, confirmed all unrelated settings unchanged.
Immediately after the update, active deployment remained
`818e9320-58ec-43ff-b33c-b6be16d956a3`. DigitalOcean subsequently reconciled
the configuration into ACTIVE deployment `25f6331d-918a-4b0d-bd91-6cb17934d766`,
retaining source `a18103306da4d53cf420dcfed5dcab60408d5066`, with no deployment
in progress. No new source or code PR was deployed.
At preparation time the new domain was CONFIGURING: `DomainCNAMENotFound`.
After the owner saved the CNAME (TTL 4 hours), all authoritative servers,
Google DNS and Cloudflare returned the expected target. Managed TLS completed;
both hosts returned 200 for readiness, indicators, measures, OpenAPI, Swagger
and ReDoc with certificate validation enabled. PR #216 subsequently deployed
as `b58250d6-babb-4787-aaf9-dd3c3ef1224d`, ACTIVE at merge SHA
`0365c0742cbef20db2a841322bf13c5b5d026825`; the complete app spec, including
stored secret entries, compared equal to the previous deployment.

## Owner DNS action

Squarespace manages the authoritative DNS. In Domains → onehealthatlas.org →
DNS → DNS Settings → Custom Records, add:

| Field | Value |
| --- | --- |
| Host | `api` |
| Type | `CNAME` |
| Data / target | `one-health-lyme-gap-atlas-api-lzpqa.ondigitalocean.app` |
| TTL | 4 hours (saved by owner) |

Do not change nameservers, apex/www records, or existing CarawayLabs records.
If an `api` record already exists, review it before replacement. No DNS change
was made by the agent. DigitalOcean should validate the CNAME and provision
managed HTTPS automatically. The alias is already registered: no additional
DigitalOcean UI action is normally required. If provisioning stalls, inspect
App Platform → this API app → Settings → Domains → api.onehealthatlas.org;
follow only the exact verification record displayed there, or contact support.

## Verification and subsequent approval

With ordinary TLS validation enabled, verify `/health/ready`, `/v1/indicators`,
`/v1/measures`, `/openapi.json`, `/docs`, and `/redoc` on both API hostnames.
Check a browser-origin request and OPTIONS preflight from
`https://onehealthatlas.org`. Production CORS already permits this web origin;
the API hostname is not a browser origin to add to CORS. No trusted-host
middleware requires a hostname change. OpenAPI advertises the branded server
and documentation asset URLs. The production CORS manifest also permits
`https://api.carawaylabs.com`: Swagger running on the retained documentation
hostname calls the advertised branded server across origins. This explicit
documentation-origin permission preserves the old Try It Out flow without
wildcards or a change to authentication.

The separate Web cutover aligns build-time `NEXT_PUBLIC_API_BASE_URL`, the
tracked Web manifest and CI builds with `https://api.onehealthatlas.org`.
Approve and deploy this API documentation/CORS PR first, then the Web PR.
The API deployment workflow rebuilds existing live source and does not apply
`.do/app.yaml`; apply only the added legacy docs CORS origin to the existing
live spec with owner-approved `doctl apps update` (no `--update-sources`).
Preserve all live settings/secrets by starting from the live spec, changing
only CORS_ORIGINS, and comparing all unrelated fields before and after.
Do not replace the live API spec with the repository's incomplete template.
This live CORS step is pending approval; no production setting was changed.

After approved deployment, execute GET `/v1/indicators` using Swagger Try It
Out on both `/docs` hosts, and check ReDoc renders and loads its logo/schema.
Use ordinary TLS and verify no browser CORS errors. Then verify the Web's
actual deployed build-time setting and browser network requests use the new
API; compare the live Web spec to ensure unrelated settings/auth remain intact.
Verify representative public and Ask Atlas behavior after that cutover; an
authenticated Ask Atlas check requires an authorized session. Do not claim
that check from public health probes.

No code PR is merged or deployed as part of this preparation. Removing only
the new alias is the configuration rollback; retaining the old hostname keeps
existing clients working throughout.
