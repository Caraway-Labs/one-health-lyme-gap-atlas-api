# 0018: Vintage-aware county context observations

Status: Accepted (explicit owner decision in API #212 execution, 2026-10-17)
Date: 2026-10-17
Decision owner: Atlas owner

## Context

Data V155 admits five existing socioeconomic/rurality measures into the governed
county observation projection. RUCC is a 2023 categorical classification with no
observation dates. The existing dated Observation schema cannot truthfully
represent it. A new endpoint family or fabricated annual dates would violate
API #212's approved scope and scientific contract.

## Decision

Extend the existing /v1/observations operation with an explicit vintage_year
filter and a separate typed CountyContextObservation collection response
alternative. Preserve the original dated Observation and collection components,
operation IDs and existing-measure HTTP payloads. Keep the exact five-ID and
consumer-version allowlists, current-release binding and Data-owned semantics.

The owner explicitly approved: "Approve additive contract and Web #474 migration"
after independent review demonstrated that widening the generated operation
return type requires existing dated-only callers to narrow the result. This
acceptance is specific to the generated-client migration, not a claim that
unchanged schema component names imply unchanged operation return types.

## Migration and compatibility

Web #474 owns regeneration from first-party-openapi.json and response narrowing.
For dated-only helpers, validate the original CollectionEnvelopeObservation
schema or reject context rows using the required county_context discriminator
before accessing dates. For the five context measures, validate the generated
CollectionEnvelopeCountyContextObservation schema, preserve nullable RUCC dates,
use category_label and vintage_year, and retain all provenance/limitations.
Do not widen dated-only helper interfaces or fabricate periods to satisfy types.

Existing deployed clients requesting existing measures remain wire compatible
and can keep their pinned artifact until the Web migration is ready. New
code generation exposes the union deliberately. No existing operation or schema
is deprecated or removed, so no deprecation date applies; the API delivery and
Web migration have separate releases. The compatibility-floor test now asserts
that the original response remains an alternative; scratch generated-client
validation, including its known narrowing failures, remains separate evidence.

## Release gates and consequences

Independent review must re-review this decision and the exact candidate commit.
Green hosted quality and explicit human merge authority remain required.
Production acceptance requires actual API-service identity/read evidence and
anonymous HTTP verification for the five measures plus legacy climate/human
behavior. Neither human-PAT reads nor mocks establish deployed availability.
No Web source, ingestion, grant, ML, authentication or topology change is included.
Rollback uses the existing reviewed API revert workflow, preserving Data history.
