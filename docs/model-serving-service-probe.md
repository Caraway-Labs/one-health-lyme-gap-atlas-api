# Model-serving service access check

The existing API startup diagnostic pattern now includes one bounded PROD-only
model-view check using its configured Snowflake service connection. No additional
key, passphrase, credential export, environment inspection, public endpoint or
Snowflake grant is needed. Read only `atlas_model_serving_reader_probe` through
the existing operational log surface after normal reviewed deployment.

At most seven SELECTs run in a daemon once per app lifespan, each limited to
five seconds. The primary API identity, configured reader/database/warehouse and
absence of owner/migrator/publisher roles must match before presentation reads.
Both frozen ML138 models must expose 3,144 distinct counties, the original
October 15 input release/bundle and v1 batch identity under the current governed
presentation release. Four base-table LIMIT 0 reads must remain inaccessible (SQLSTATE 42S02 means either missing or unauthorized; this probe does not distinguish them); no DML or
publication procedure is attempted. Failures retain closed categories; logs
contain counts, booleans and identity/release fingerprints, never credentials,
raw model rows or driver messages. Fixtures, import and OpenAPI export are inert.

This establishes actual service visibility for Data #702 / API #253. It is not
the full release-completeness canary, probability/scientific acceptance, Tier 1
compatibility admission or delivery of the future public surveillance-situations
endpoint. Data #678 retains the deferred Tier 1 lineage defect. No existing
release, model batch, source provenance or public contract changes.
