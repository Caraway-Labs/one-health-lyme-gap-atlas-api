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
The new domain is CONFIGURING: `DomainCNAMENotFound`. Its certificate is not
issued yet. The old readiness and indicators endpoints both returned 200.

## Owner DNS action

Squarespace manages the authoritative DNS. In Domains → onehealthatlas.org →
DNS → DNS Settings → Custom Records, add:

| Field | Value |
| --- | --- |
| Host | `api` |
| Type | `CNAME` |
| Data / target | `one-health-lyme-gap-atlas-api-lzpqa.ondigitalocean.app` |
| TTL | Default |

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
middleware requires a hostname change. The published OpenAPI server and
favicon URLs still point to the working CarawayLabs hostname; update those
metadata URLs with the public documentation after branded HTTPS passes.

The production Web build-time `NEXT_PUBLIC_API_BASE_URL`, tracked Web manifest,
and API public guide currently use `https://api.carawaylabs.com`. Keep those
working URLs until branded HTTPS passes. Then submit a focused Web base-URL
and public documentation cutover for review and owner-approved deployment.
Verify representative public and Ask Atlas behavior after that cutover; an
authenticated Ask Atlas check requires an authorized session. Do not claim
that check from public health probes.

No code PR is merged or deployed as part of this preparation. Removing only
the new alias is the configuration rollback; retaining the old hostname keeps
existing clients working throughout.
