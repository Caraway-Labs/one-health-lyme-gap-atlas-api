# API #232 investigation and bounded correction

Related: API #228, WEB #394; deferred ingress behavior API #88 / API #234.
Investigation: October 12, 2026 (America/Denver), receipts October 13 UTC.
Production unchanged: ACTIVE b8fda191-82e6-40df-97c6-cedcd2cf23bd,
source 555295f502c257adeaeafbaf323b85122e8fac26.

## Confirmed root cause

The audit used year=2023 for cumulative as-published CDC status. This is not
an annual measure. Supported selectors are start_date=end_date=2025-12-31.
The CDC adapter rejected year through AtlasDataUnavailableError before any
release or producer read. FastAPI mapped it to structured503. DigitalOcean
App Platform ingress replaced it with HTML504, x-do-orig-status503.

Reproductions: pathogen01001 request cdc8c85c-6570-4720-89b2-06262fe11380:
HTTP504 in 0.491614s; correlated application log HTTP503, duration5ms.
Tick06001 request b68f688e-6fb1-4622-954b-6461f0f8f3ce: HTTP504 in
0.335239s; correlated application log HTTP503, duration6ms.
These are status replacement, not elapsed gateway timeouts or allowlist regression.

## Request path and unchanged controls

Ingress -> public per-IP rate/concurrency middleware -> FastAPI query validation
and bounds -> ObservationService -> CDC adapter temporal/identity preflight ->
current governed release read -> bounded requested page plus one lookahead ->
Data producer. Each county uses shared key-pair connection construction, secondary
roles NONE, statement timeout min(public_query_timeout_seconds,30), bounded
fetchmany(2), producer identity query and exact authority query. Data owns retained
record/revision admission and private authority. API validates pinned release,
bundle, source version, metadata revision, cumulative period and evidence before
projecting public fields. Errors remain sanitized; no publisher values are changed.

## Correction

Unsupported temporal selectors now raise existing PublicQueryError
UNSUPPORTED_FILTER, structured400, before data access. No annual-to-cumulative
coercion. Canonical dates, positive/NO_RECORDS evidence, identity guards and
Data failures retain existing behavior. OpenAPI/guide remove stale three-county
wording and document cumulative temporal restriction. DTO shapes are unchanged;
no generated Web model changes are needed for description-only schema edits.

## Rate limits, separate from ingress

Live app/service environment has no overrides for public rate, concurrency or
query-timeout settings. Defaults: public60/min/IP, public5 concurrent/IP,
overall120/min/IP; per-statement timeout15s. Public canonical routes share the
public bucket. Invalid requests consume budget. 429 RATE_LIMITED is generated
before data access when minute or concurrency capacity is exhausted, with
Retry-After1..60 and no-store. Existing concurrency tests and new invalid-CDC
budget regression verify this independently. Sequential positive samples had
zero429. 1,139 total GETs alone cannot prove a specific sliding-window/concurrency
violation. Audit raw timestamps, attempt counts, concurrency and Retry-After are
not attached to GitHub; exact attribution of historical429 remains unverified.
No limiter changes or intentional production saturation test were performed.

## Unresolved acceptance / release gates

Unknown-source county02122 with canonical dates still fails closed:
api.onehealthatlas.org HTTP504/origin503 in7.219s, request
0774d82e-21b3-4845-8594-0c315aaefe09; api.carawaylabs.com in5.922s,
d5fb66c2-afa7-4f0b-8a03-d7eeb800d58a; direct App Platform domain in5.765s,
7c148770-aefb-4123-9832-4fb681a7d0a7. App-spec reference documents a custom
error page but no status/body-preserving ingress switch:
https://docs.digitalocean.com/products/app-platform/reference/app-spec/
Provider-supported preservation must be verified, or a recorded contract/topology
decision is required. No speculative ingress rewrite or alternate success status.
Full #232 acceptance is NOT complete; keep issue open.

Supplemental independent subagent review APPROVE bounded correction/no actionable
findings; it does not substitute for independent OpenAI .agent coordinator review.
Repository docs/ci-and-production-deploy.md requires that separate coordinator
session and human merge. No coordinator session identified in accessible chats.
No merge/deploy claimed. Retain isolated worktree until successful release.

## Baseline production evidence (not post-fix UAT)

20 sequential requests across10 counties, both measures, page_size500, canonical
period; all200, latency5.941..8.456s. Release
 governed-2026-10-06-nlcd-demo-cohort; public metadata/provenance receipts retained
in api-232-production-samples.json. No raw response logs, cookies or credentials.

| County | Measure | HTTP | Seconds | Publisher | State | Normalized |
| --- | --- | ---: | ---: | --- | --- | --- |
| 01001 | scapularis_status | 200 | 6.714 | Established | OBSERVED | established |
| 01001 | burgdorferi_status | 200 | 8.251 | No records | NO_RECORDS | unknown |
| 06001 | scapularis_status | 200 | 7.94 | No records | NO_RECORDS | unknown |
| 06001 | burgdorferi_status | 200 | 5.974 | Present | OBSERVED | detected |
| 16001 | scapularis_status | 200 | 7.062 | No records | NO_RECORDS | unknown |
| 16001 | burgdorferi_status | 200 | 7.575 | No records | NO_RECORDS | unknown |
| 13081 | scapularis_status | 200 | 5.941 | Reported | OBSERVED | detected |
| 13081 | burgdorferi_status | 200 | 7.283 | No records | NO_RECORDS | unknown |
| 19135 | scapularis_status | 200 | 7.085 | Established | OBSERVED | established |
| 19135 | burgdorferi_status | 200 | 6.053 | Present | OBSERVED | detected |
| 12047 | scapularis_status | 200 | 8.456 | Established | OBSERVED | established |
| 12047 | burgdorferi_status | 200 | 6.037 | No records | NO_RECORDS | unknown |
| 04013 | scapularis_status | 200 | 7.248 | No records | NO_RECORDS | unknown |
| 04013 | burgdorferi_status | 200 | 7.451 | No records | NO_RECORDS | unknown |
| 08031 | scapularis_status | 200 | 7.854 | No records | NO_RECORDS | unknown |
| 08031 | burgdorferi_status | 200 | 7.365 | No records | NO_RECORDS | unknown |
| 25025 | scapularis_status | 200 | 7.065 | Established | OBSERVED | established |
| 25025 | burgdorferi_status | 200 | 7.438 | No records | NO_RECORDS | unknown |
| 53033 | scapularis_status | 200 | 7.147 | No records | NO_RECORDS | unknown |
| 53033 | burgdorferi_status | 200 | 6.852 | No records | NO_RECORDS | unknown |
