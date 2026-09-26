# Feedback operations

Operator notes for `POST /v1/feedback` abuse controls and account-rights
handling. This is not an alerting product; investigate from existing API logs.

## Single-process assumption

The in-memory feedback rate limiter and the submission idempotency stripe pool
are both process-local. They are correct only while the API runs as one process
on one instance. `tests/test_deployment_config.py` fails closed if
`.do/app.yaml` would run more than one instance, or if the Dockerfile /
worker environment would start more than one uvicorn worker. Do not remove that
gate until a distributed idempotency and rate-limit design replaces the
process-local controls.

## Operator thresholds

Investigate from existing structured `feedback_submission` logs when either
threshold is crossed:

- 10 `persistence_failed` outcomes in 10 minutes
- 30 `throttled` outcomes in 10 minutes

Safe log fields are `outcome`, `category`, `route_id`, and `request_id` only.
Logs must never include message text, contact email, bearer tokens, raw IP, or
request bodies.

## Accidental sensitive data

If a submission contains sensitive personal information that should not remain
in the retained message body, an `OWNER` operator identifies the row by
`feedback_id` on the triage view and calls
`GOVERNANCE.SP_REDACT_USER_FEEDBACK(feedback_id, reason)` (data story #130).
That path replaces the message text and removes contact and account linkage for
that one row. Account privacy deletion uses
`SP_REDACT_FEEDBACK_FOR_ACCOUNT` instead: it removes contact and account
linkage for the account and keeps the submitted message.
