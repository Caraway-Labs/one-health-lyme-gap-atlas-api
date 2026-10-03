# January climate reader access preparation

Read-only inspection on 2026-10-03 confirmed that the existing API service user
`OH_LYME_API_SVC` has role membership in `OH_LYME_PROD_READ` and
`OH_LYME_API_READER`. The checked-in App Platform manifest requests
`OH_LYME_PROD_READ` and the suffixed PROD presentation database. These are
configuration and grant evidence, not proof of the effective deployed session.
No service credential was accessed, copied, changed or configured.

An authenticated `ATLAS_PROD_OWNER` session confirmed existing database and
PRESENTATION schema USAGE for `OH_LYME_PROD_READ`, and existing annual view SELECT
grants. Its visible role-grant result contained no climate view SELECT grant.
A SHOW GRANTS on the PROD climate observations view returned "does not exist or
not authorized". PROD climate object existence therefore remains UNKNOWN under
this inspection; this is not evidence that a missing object must be created.

After reviewed PROD publication, actual runtime identity proof and confirmation
that these grants remain absent, the minimal proposed persistent access action is:

```sql
GRANT SELECT ON VIEW ONE_HEALTH_LYME_GAP_ATLAS_PROD.PRESENTATION.CURRENT_CLIMATE_COUNTY_DAY_OBSERVATIONS_V TO ROLE OH_LYME_PROD_READ;
GRANT SELECT ON VIEW ONE_HEALTH_LYME_GAP_ATLAS_PROD.PRESENTATION.CURRENT_CLIMATE_MEASURE_METADATA_V TO ROLE OH_LYME_PROD_READ;
```

This is a proposal requiring specific authorization before execution. It grants
no RAW, GOVERNANCE, table, future-object or owner access. Existing usage grants
are not recreated. A DEV deployment requires its own verified service identity
and target role rather than reusing the PROD proposal.

`scripts/verify_environmental_reader.py` must run inside the existing authorized
API execution environment with its actual configuration, after feature activation
and publication. It reports effective user/role/database/warehouse and bounded
same-release metadata/decoded-observation counts. A human PAT inspection cannot
substitute for that result. No approved remote service-command surface is exposed
in this workspace, so this service-side execution remains an engineering handoff;
it does not require Matthew to repeat January source or metadata acceptance.

A bounded owner-visible INFORMATION_SCHEMA query-history check (last 24 hours,
at most 1,000 visible rows, API user only, no query text) returned no matching
rows. Its visibility is limited; this does not establish that the service has
not queried Snowflake or authenticate its runtime identity.
