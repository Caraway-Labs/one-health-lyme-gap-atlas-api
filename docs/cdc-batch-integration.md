# API #248 Stage 2: bounded CDC batch integration

The product owner approved one batch only when the entire processed page fits
within Data's 50-county bound. This implements the existing API/Data boundary;
Data ADR 0045 and `cdc-consumer-batch-v1.md` own admission and batch semantics.
The immutable Data implementation pin is
`2476bfec3b89b513adf9ce2348fe94be44abf915`, merged in Data PR #700 via
`70035167a3924415731c497f7557964c34cd5830`.

## Selection and consistency

The existing sorted selection, token offset and `page_size + 1` lookahead select
the actual processed county slice. A nonempty slice of at most 50 counties uses
exactly one `produce_cdc_batch_v1` call. Larger slices retain sequential
singleton projection. Empty slices do not call either producer. Total selected
counties alone do not determine routing: 100 selected/page size 25 processes 26
and uses batch; 51 selected/page size 50 processes 51 and uses singleton. A
continuation can therefore change the path when fewer counties remain.

Batch results must match contract version, measure, governed release, requested
county sequence and cardinality. Every projected observation goes through the
existing API mapper. Any invalid or unavailable result, including lookahead,
fails the complete request. An attempted batch never falls back after failure.
The batch producer owns scientific admission; the API does not copy its SQL or
introduce a second scientific authority engine.

One batch authority statement has Data's statement-level committed snapshot.
There are no multiple-batch public pages. Large-page singleton requests retain
their preexisting cross-statement consistency limitations: matching immutable
release/source identities does not prove a page-wide snapshot of live admission
authority. This change makes no such claim and introduces no transaction or
snapshot mechanism. Current release still resolves once per request and must
match the approved October 15 binding.

The Stage 1 owner still acquires, configures and tears down one exclusive
request-local Snowflake connection. Both paths borrow that same connection,
execute sequentially, preserve reader checks and use bounded cursor fetches.
Timeouts, errors and supported cancellation unwind the same ownership context.
No public endpoint, schema, operation ID, limit, rate limit or non-CDC serving
path changes.

## Reproducible synthetic measurements

Run `uv run python scripts/benchmark_cdc_batches.py --samples 2`. The script
loads Stage 1 service/adapter modules from baseline
`6d7908bcfbfb4d90cddcfaefe03d1836f08490ba` and compares the candidate integrated
service/adapter for both measures and 1, 2, 5, 10, 50 and 51 processed counties.
Controlled producer doubles use existing API fixture envelopes, exercise the
real borrowed callback and compare complete public observation DTOs. Synthetic
county identifiers and unique fixture observation IDs are benchmark scaffolding,
not newly admitted scientific examples. These doubles do not measure or prove
Data admission; separate scientific regression tests provide that evidence.

Injected costs are 100 ms/connect, 10 ms/execute, 2 ms/fetch and 10 ms/teardown.
Reported wall clock includes local scheduling and Python overhead, and excludes
HTTP/network transport. Mapping and exclusive producer processing come from
existing request timing; JSON serialization is measured separately. Public
payload bytes include the complete response envelope. Peak memory uses Python
tracemalloc across service execution and serialization, not process RSS or SDK
native memory. Batch authority-envelope bytes are unavailable for the controlled
producer double, which returns a synthetic marker rather than the real private
authority payload. Public payload sizes are measured independently; this does
not reproduce Data's measured 48.66% pair-payload reduction. The script asserts
DTO equality, one closed connection per call,
authority counts and explicit SQL counts. CSV samples remain gitignored at
`.agent-tools/api248-stage2/benchmark.csv`.

| Processed counties | Connections before / after | Authority SQL before / after | Explicit SQL before / after |
|---:|---:|---:|---:|
|1|1 / 1|1 / 1|5 / 5|
|2|1 / 1|2 / 1|7 / 5|
|5|1 / 1|5 / 1|13 / 5|
|10|1 / 1|10 / 1|23 / 5|
|50|1 / 1|50 / 1|103 / 5|
|51|1 / 1|51 / 51|105 / 105|

Counts include two existing session commands, release lookup and Data's reader
identity/authority statements. SDK implicit statements remain outside this
explicit counter. Reduced SQL counts are structural evidence, not proof of lower
production latency. Actual Data batch compilation was previously measured at
4.2–6.1 seconds; fixture delays do not model it or its fetch payload.

Observed local medians below pool two samples per measure (four per cell).
All 48 calls passed DTO equality, cleanup and count assertions. Measurements
include tracemalloc overhead and competing local quality processes. The
51-county path is identical before/after, so its elapsed variation illustrates
the environment's noise rather than a fallback performance improvement.

| Counties | Simulated total ms before / after | Mapping ms before / after | Producer processing ms before / after | Serialization ms before / after | Public bytes | Median traced peak bytes before / after |
|---:|---:|---:|---:|---:|---:|---:|
|1|429.83 / 395.28|198.77 / 171.99|6.88 / 3.02|0.19 / 0.20|3,003|33,922 / 34,237|
|2|790.45 / 525.93|516.85 / 325.16|12.65 / 7.52|0.26 / 0.40|5,898|43,316 / 46,525|
|5|1,055.55 / 884.14|667.28 / 660.24|28.80 / 10.91|0.80 / 1.55|14,583|71,209 / 92,642|
|10|3,173.57 / 2,291.87|2,480.50 / 1,933.86|72.48 / 80.49|0.91 / 0.74|29,058|133,922 / 169,977|
|50|15,768.52 / 8,312.36|11,875.78 / 7,674.65|496.43 / 214.13|8.21 / 13.13|144,858|641,959 / 787,865|
|51|12,857.41 / 11,044.23|9,769.33 / 8,584.24|328.70 / 298.08|8.54 / 4.29|147,753|654,215 / 654,267|

Batch holds bounded projected envelopes until mapping completes, so its traced
peak can exceed sequential singleton memory despite fewer SQL statements.
These measurements are synthetic API mapping/SDK experiments, not production
observations or measurements of real Data scientific processing/authority bytes.

## Production acceptance after explicit approval

No production benchmark is authorized by this implementation. After human merge
and deployment approval, verify the exact reviewed source SHA and ACTIVE
deployment, startup reader identity and unchanged public contract. Use the same
approved cohorts from the Stage 1 runbook: 01001; 01001/13081; and
01001/01003/01005/01007/01009, both measures, county geography,
start/end `2025-12-31`, page size 10. Start with one/two before five counties.

Use at most 19 serial requests, a 45-second client deadline, no redirects or
retries and at least 0.35 seconds after each completion. Two singleton and two
pair samples per measure consume eight requests; one five-county sample each
adds two; a two-page size-one sequence each adds four; one excluded-county
request each adds two; human/climate/context compatibility controls add three.
Stop on any 429, repeated 5xx or operational distress, and honor Retry-After.
Do not expand into larger selections without a separately approved comparison.

Compare historical October 17 HTTP medians (seconds): tick 4.743/4.650/10.884
and pathogen 5.416/5.590/6.947 at one/two/five counties. Capture safe request IDs,
HTTP and application durations, selected serving path, processed count,
connection/explicit/authority counts and execution/fetch timing. Record
compilation separately only where observable; report unavailable components
honestly. Never add overlapping producer/processing/I/O timing categories.
Check exact publisher values, NO_RECORDS unknown, metadata revision 3,
release/source/provenance/limitations, pagination and private-field absence.
The known excluded-county ingress HTML failure is not structured 503 success.

## Rollback

Before merge, close or revise this PR without touching production. After a
separately authorized deployment, use the normal reviewed rollback to the Stage
1 implementation at `6d7908bcfbfb4d90cddcfaefe03d1836f08490ba`, including prior
Data pin `2dc362ea6720d3198f9d0a3c8d5ba64dccd60a6b` and its committed lockfile.
That restores singleton execution through the Stage 1 request-owned session.
Do not manually modify Snowflake, admission, grants or dependency artifacts.
Keep API #248 open for production acceptance and residual performance findings.
