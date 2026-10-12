# API #228 CDC coverage correction

The API county allowlist prevented published counties from reaching the existing
Data producer. Eligibility now comes from that unchanged pinned producer, which
checks the published release and exact retained steward packet binding before
mapping and consumer projection. The API retains its release/bundle, two measure,
source/version/reviewed metadata and cumulative-through-2025-12-31 checks.
NO_RECORDS, UNKNOWN, publisher wording and Data normalized_state remain distinct.
No public schema, endpoint, credentials, grants, migrations or classification change.

Requests retain 500-county and 1–500 page bounds. Only the selected page plus one
lookahead invokes the producer, sequentially; cancellation checks stop later reads.
Every producer invocation keeps the existing statement/session timeout. There is
no full-release scan, concurrency or speculative query for nonrequested counties.
Mixed admitted/unadmitted pages fail closed without partial output.

## Read-only PROD evidence

Named PAT ATLAS_PROD_READ was verified live as MATTHEWCARAWAY /
OH_LYME_PROD_READ / ONE_HEALTH_LYME_GAP_ATLAS_PROD / COMPUTE_WH, secondary
roles NONE. The installed Data artifact is pinned at
8ce5d47d518161986d20954f3ddceb8f66504c92. Its unchanged named-reader projector
uses a 30-second statement limit. This operator evidence is not API service-key
or postdeployment HTTP evidence.

All originals passed: tick 13081 Reported / detected; pathogen 19135 Present /
detected and 12047 No records / unknown. Tick 01001 and 01003 and pathogen 06001
and 06013 all failed at `exact retained steward/release decision required`.
Complete published lineage does not authorize their consumer projection.

A bounded aggregate over the intended-reader secure view found 3,144 published
lineage observations per measure but only one tick and two pathogen observations
with an observation_bindings entry in the retained review conditions. Combined
with the successful exact projector reads, current admitted counts are 1 and 2,
not 3,109 each. No additional counties are currently admitted by that packet.

Genuine production HTTP on the existing ACTIVE SHA returned 200 for all three
originals with the values/states above. Tick 01001 and pathogen 06001 returned
gateway 504 with x-do-orig-status=503. The gateway behavior remains out of scope.
These HTTP reads exercise the configured production service; they do not verify
deployment of this correction or additional admitted availability.

## Smallest Data-owned requirement

Retain an explicitly authorized steward decision using the existing governed
review workflow. Its cdc_consumer_admission condition must bind this exact release,
bundle, source slice and immutable revision-2 metadata identity, and enumerate
each admitted published observation in observation_bindings with its actual
record_id and semantic_revision_id. Validate those bindings through the unchanged
Data projector and intended-reader role. Do not infer bindings from source approval,
alter the projector, manufacture revisions, include unknown-coverage placeholders,
or republish retired FIPS. The existing source attestations remain separate gates.
The decision timestamp must satisfy the producer's exact reviewed metadata binding;
Data must reconcile that through its existing immutable review/metadata procedure,
not silently stamp a later decision with an earlier time. API code cannot grant
this missing record-level authority.

Deployment requires current quality, independent .agent coordinator review and
explicit human merge per docs/ci-and-production-deploy.md. The current production
deployment inspected is ACTIVE at 30fbcfac8f28d3ebcb5ad3fabad916accbe8d4f5;
the #228 correction has not deployed. Local doctl console fails at terminal-size
initialization; no service secrets were extracted.
