# CI and production deployment

Quality runs and production promotion are separate. Quality validates one
commit. Promotion may ship that commit only after an explicit dispatch, and
only when it is still the tip of `main`.

## What runs in parallel

`quality` is the required check. It stays one job: uv sync, ruff, mypy,
pytest with coverage, Codecov upload, OpenAPI export, Docker build, Typst
regression, and gitleaks. Pull requests, pushes to `main`, and manual
dispatches each start their own run. This workflow has no workflow-level
concurrency group, so those runs are not queued behind each other or behind
a production deploy.

`CI_MAX_PARALLEL=4` is the operational intent for overlapping quality runs.
GitHub Actions has no native four-slot semaphore. A concurrency group would
serialize those runs to one, so this repository does not use one for CI.
There is no matrix, so `strategy.max-parallel` does not apply. The account's
hosted-runner limit is what bounds simultaneous quality jobs.

## What is serialized

Only the `deploy` job takes a production lock.

```yaml
concurrency:
  group: ${{ vars.API_PRODUCTION_CONCURRENCY_GROUP || 'atlas-api-production' }}
  cancel-in-progress: true
```

`API_PRODUCTION_CONCURRENCY_GROUP` defaults to `atlas-api-production`. Set
that repository variable only to rename the lock. An in-flight deploy still
using the previous name (`api-production`) does not share the new group.

`cancel-in-progress` cancels the deploy job that currently holds the group
when another deploy job for this API arrives. It does not cancel `quality`.
The web app uses its own group and is not coordinated here.

`LATEST_MAIN_WINS=true`. That policy is always on. Nothing in the workflow
disables it.

## When production changes

DigitalOcean does not deploy this app on push (`.do/app.yaml` sets
`deploy_on_push: false`). Merging to `main` runs quality and stops there.

Promotion is `workflow_dispatch` on `main` with `deploy_production` set, and
only when `vars.DIGITALOCEAN_APP_ID` is set. The `deploy` job `needs: quality`,
so a failed or skipped quality check does not promote that commit. The job
uses the `production` environment.

The App Platform create-deployment API (`force_build: true`) rebuilds the
branch configured on the app. It cannot pin a SHA. The deploy job checks out
this run's commit and, immediately before that API call, fetches `origin/main`.
If the candidate is not that tip, the job logs a skip and exits successfully
without calling DigitalOcean:

```text
Deployment skipped.
Candidate SHA: <this run>
Current main:  <origin/main>
Reason: candidate has been superseded by a newer main commit.
```

If the candidate is the tip, the job triggers the deployment and polls until
the `api` service reports `source_commit_hash`. Success requires that hash to
equal this run's SHA and phase `ACTIVE`. A different hash fails the job. While
the deployment is not yet `ACTIVE`, the job also cancels it. The job does not
check out a newer `main` and deploy that instead.

Logs and the job summary record `app=api`, branch, candidate SHA, run id, run
URL, UTC timestamp, and `result=success`, `result=skipped`, or
`result=failure`. The DigitalOcean deployment id is included when one was
created. The access token is not logged.

## Rollback

Ship a commit that is the current tip of `main`. To return to older code,
revert on `main`, wait for `quality` on that commit, then dispatch
`deploy_production` again. Dispatching an older commit while a newer tip
exists is skipped, so a stale run cannot roll production backward.

If a newer deploy job cancels this GitHub job before the DigitalOcean request,
re-dispatch promotion on the current `main` tip after its `quality` check is
green. A DigitalOcean deployment that already started keeps running on App
Platform; confirm its `source_commit_hash` before dispatching again.

## Failure

`ERROR`, `CANCELED`, and `SUPERSEDED` phases fail this job. A timeout fails
this job. The previous production deployment remains the active one until a
later promotion reaches `ACTIVE`. Re-dispatch on the current tip after the
cause is fixed. Do not treat a green `quality` check, or a skip, as a
production change.
