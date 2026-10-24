# Snowflake readiness and monitoring (API #310)

`GET /health/ready` reads process-local state only. One successful bounded
startup connectivity check (`connect` plus `SELECT 1`) latches the Snowflake
startup gate for this process. Before success it returns the existing 503
problem response. The feedback process-topology and enabled-chat wiring gates
still apply. Neo4j is checked only at chat time; `/health/live` remains unchanged.

After startup succeeds, readiness returns 200 even if Snowflake later fails:

```json
{"status":"ready","dependencies":{"snowflake":"degraded"}}
```

The dependency field is informational, sampled by the monitor; it is not a
per-request availability guarantee. Data routes retain their existing exception
handler and 503 problem detail: "The governed Atlas data service is temporarily
unavailable." Existing data cache behavior is unchanged.

One daemon worker owns a long-lived connection, checks every
`SNOWFLAKE_MONITOR_INTERVAL_SECONDS` (default 60, measured after each completed
check), and performs at most one reconnect when an existing session fails.
`SNOWFLAKE_STARTUP_CHECK_TIMEOUT_SECONDS` defaults to 10. Subsequent checks use
a three-second monotonic budget across query/reconnect attempts. The monitor
uses the approved shared credential construction with monitor-only login,
network/socket and statement timeouts; product connection policy is unchanged.
Network/socket waits cap at three seconds and retries have no backoff delay.
Statement timeout is rounded up to the connector's integer seconds. These are
cooperative SDK bounds, not forcible cancellation of a native/DNS call. A late
result fails its deadline and never makes startup ready. Checks never overlap,
and no replacement workers accumulate behind a stalled call.

Shutdown signals the worker and joins with a bounded wait. The owning worker's
`finally` closes its connection with close retries disabled. If an SDK call
outlives that wait, cleanup runs when it returns; the application does not race
a connection close against an active query. Normal shutdown closes before
lifespan exits. No session heartbeat thread or SDK telemetry is enabled for
this connection. Healthy operation uses one login per process/session lifetime,
with about 60 lightweight checks/hour. Session expiration or outages can require
additional logins; this is not a global product-route connection pool.

`atlas.dependency.snowflake.check` spans originate in the background worker,
with existing nested connect/execute/fetch/close instrumentation. Logs contain
only fixed state transitions: initial failure or degradation emits
`snowflake_dependency_degraded`; recovery emits `snowflake_dependency_recovered`.
Repeated failures and initial success are silent.

## Release evidence

Controlled tests cover credential failure, one-way startup latch, no readiness
I/O/spans, session reuse, reconnect budget, transition-only logs, interval waits,
and shutdown cleanup. Public OpenAPI remains unchanged; the first-party schema
adds the typed readiness dependency body with unchanged operation ID. Existing
Web consumers of health status must tolerate the additive `dependencies` field;
no public generated-client change is needed.

DigitalOcean probe settings and instance count are unchanged. After the reviewed
release, observe natural traffic for one hour starting at least ten minutes
after deployment. Verify readiness p95 below 50 ms, no Snowflake descendants of
`GET /health/ready`, about 60 background checks/hour, and no more than two
health/monitor logins/hour in healthy steady state. Check transition logs when
naturally present. Do not inject faults into production. These runtime targets
are pending deployment observation, not established by local tests.
