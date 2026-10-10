# Web #354 canonical-origin CORS readiness

The owner authorized the additive production API origin
`https://onehealthatlas.org`. The deployment spec appends only this origin to
`CORS_ORIGINS`; the four existing origins remain allowed. HTTP, lookalike hosts,
and other origins remain rejected. API routes, payloads, credentials, and the
API hostname are unchanged. This implements the existing strict-origin security
contract and workspace ADR 0002 without changing the authentication model.

The current live API app is `fb312ce3-e762-45b0-bf34-12d3df91eee6`.
The verified deployed source and this branch's base are both
`fcef60dc809957a9c5720bd25fdd2b0cbfa2063a`.
Its current runtime CORS setting still excludes the new origin. No live API
configuration has been changed: a configuration update can trigger deployment,
and this request explicitly requires owner approval before deployment or merge.
This PR is independent of Web #499, Squarespace DNS, and Supabase configuration.

Do not merge before owner approval: the API's current main-push workflow deploys
after quality and latest-main checks. Before approved promotion, reread live
`CORS_ORIGINS` and preserve any origins added since this snapshot. Use the
existing production workflow and exact reviewed source; never apply a template
with secret placeholders directly over the live app specification.

Acceptance after approved deployment: GET and OPTIONS to `/health/live` with
Origin `https://onehealthatlas.org` return the matching
`Access-Control-Allow-Origin`. Repeat for every existing origin and confirm
lookalike/HTTP origins have no allow-origin header. Verify actual Atlas browser
API calls after the Web cutover. Local tests use a fake repository and do not
read Snowflake or prove live production access.

Rollback removes only the newly appended origin after approval, preserving all
existing origins. Web #355 API hostname migration remains out of scope.
