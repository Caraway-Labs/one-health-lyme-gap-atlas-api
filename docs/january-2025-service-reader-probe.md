# Privacy-safe service reader probe

The existing API startup diagnostic pattern already performs bounded read-only
driver probes and logs fixed categories/counts. This change uses that pattern
for Snowflake. It does not request an app specification, inspect deployment
environment values, enter a console, export credentials or expose an endpoint.
The service uses its existing configured connection; no role or grant changes.

For the real app factory only, a daemon issues at most three SELECT statements:
the effective primary identity, at most five release/measure metadata rows, and
at most one observation release row. Statement timeout is at most five seconds.
The existing connector's connection/retry policy remains in force. The probe is
independent of the climate activation flag and cannot block process readiness.
Fixture repositories and unconfigured app factories do not dispatch the probe.

The identity must match the already approved service principal, environment's
reader role, configured suffixed database and warehouse before either view is
read. Logs contain only fixed categories, booleans, capped counts and SHA256
fingerprints; raw principal/role/database/warehouse/release values and driver
messages are never copied. This permits confirmation of the known approved
identity without publishing a new identity or private endpoint. A mismatch
does not authorize a grant to an assumed role.

Readable empty views prove access without asserting published climate data.
Denied/not-found visibility remains `object_or_access_unavailable`, not absence.
Four canonical measures with a matching observation release establish only a
bounded same-release count/identity check, not full consumer decoding, frozen
membership, source authority or publication acceptance.

After independent review and normal deployment, read only the operational event
`atlas_environmental_reader_probe` through the existing permitted log surface.
If that log surface is unavailable or authorization is denied, stop and report;
do not retrieve a secret-bearing app response or use a console as a substitute.
Use the verified context to prepare any necessary exact two-view grant request,
then obtain specific authorization before executing persistent grants.
