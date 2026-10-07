# CI and production deployment

Quality validates one commit. A push to `main` that passes `quality` deploys
that commit when it is still the tip of `main`. Manual dispatch redeploys the
same way. Pull requests do not deploy. Merge to `main` is an explicit human
action after the gates in [Pull request governance](#pull-request-governance).

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

A push to `main` runs `deploy` after `quality` succeeds, when
`vars.DIGITALOCEAN_APP_ID` is set. `workflow_dispatch` on `main` with
`deploy_production` set is the redeploy path for that same commit. Pull
requests run `quality` only.

`.do/app.yaml` keeps `deploy_on_push: false`. DigitalOcean does not start its
own deployment when `main` moves. This workflow is what calls the App Platform
API. The `production` environment allows only the `main` branch and has no
required reviewers, so a green push to `main` deploys without a separate
approval.

`deploy` needs `quality`. The job `if` does not use `always()` or `failure()`,
so GitHub still requires `quality` to succeed before the job starts.

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

After an `ACTIVE` promotion, the workflow runs the bounded anonymous canonical
API smoke in `scripts/probe_public_api.py` once per shape. It validates
discovery, observations, provenance, typed errors, and a provisional 20-second
request ceiling. A smoke failure fails the deploy job even though the App
Platform deployment may already be active; inspect the active source SHA and
follow the rollback path below. A superseded deploy skip can still run the
smoke against the current public service, but is not evidence that the skipped
candidate deployed.

## Rollback

Push a commit that is the current tip of `main`. To return to older code,
revert on `main`. After `quality` succeeds, that revert deploys if it is still
the tip. `workflow_dispatch` with `deploy_production` redeploys the current tip
without another commit. Dispatching an older commit while a newer tip exists
is skipped, so a stale run cannot roll production backward.

If a newer deploy job cancels this GitHub job before the DigitalOcean request,
the next green push, or a redeploy dispatch, on the current `main` tip starts
again. A DigitalOcean deployment that already started keeps running on App
Platform; confirm its `source_commit_hash` before starting another one.

## Failure

`ERROR`, `CANCELED`, and `SUPERSEDED` phases fail this job. A timeout fails
this job. The previous production deployment remains the active one until a
later promotion reaches `ACTIVE`. Push a fix to `main`, or redeploy the current
tip with `workflow_dispatch`, after the cause is fixed. A superseded skip does
not change production. A green `quality` check on a pull request does not
deploy.

## Pull request governance

One human authors this repository. Codex Cloud pull requests are opened by
that GitHub identity, and GitHub will not let the author approve their own
pull request. `main` therefore keeps the pull-request requirement and sets
the approving-review count to 0. Independent OpenAI `.agent` review is the
procedural peer-review gate. The verdict is advisory. It is distinct from a
GitHub-native approving review by a separate identity. The human merges.
Nothing in this workflow auto-merges, and the reviewer does not deploy.

### Recorded `main` protection

Repository admin `CarawayLabs` audited classic branch protection and changed
only the approving-review count on 2026-10-07. The before/after values below
are the API record in
[issue #205](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/205#issuecomment-6029741829).
Repository rulesets were empty. Organization rulesets were not readable
without `admin:org` (the organization rulesets API returned 404), so this
record does not claim they are absent. This document does not change
protection, secrets, or `.github/workflows/quality-deploy.yml`.

| Setting | Before | After |
| --- | --- | --- |
| `required_approving_review_count` | 1 | 0 |
| required status checks | `quality`, `strict: true` | unchanged |
| `enforce_admins` | true | true |
| `required_conversation_resolution` | true | true |
| `allow_force_pushes` | false | false |
| `allow_deletions` | false | false |
| `require_code_owner_reviews` | false | false |
| `dismiss_stale_reviews` | false | false |
| `require_last_push_approval` | false | false |
| `required_signatures` | false | false |
| repository rulesets | none | none |

Pull requests still need an up-to-date branch, a successful current
`quality` check, and resolved conversations. Pull-request runs still execute
`quality` only. A push to `main` that passes `quality` still deploys through
the existing job, with the same production guards and concurrency.

### `.agent` peer-review procedure

The reviewer must be the OpenAI `.agent` coordinator, in a session
independent of the Codex Cloud session that authored the pull request. The
coordinator reviews Web Cursor pull requests and API Codex Cloud pull
requests with repository-specific rubrics.

On open and on every update:

1. Review the actual diff at the latest commit SHA.
2. Check the linked issue acceptance criteria, architecture, and contracts.
3. Check security, data provenance, and PHI boundaries.
4. Check tests, the current `quality` result, and deployment side effects.
   A pull request must not deploy. Merge to `main` uses the existing
   production path.
5. Publish a GitHub pull-request review or comment with advisory verdict
   `APPROVE`, `REQUEST CHANGES`, or `BLOCKED`, the evidence for that
   verdict, and actionable findings. Say that the verdict is procedural
   and is not a GitHub-recognized approving review from an independent
   identity.

`REQUEST CHANGES` returns the findings to the originating Codex Cloud
session. That session pushes updates, `quality` reruns, and `.agent`
re-reviews the new commit SHA. `BLOCKED` stops for an owner decision before
merge. `APPROVE`, plus passing current `quality`, resolved conversations,
and a branch that is up to date with `main`, is the point at which the
coordinator notifies the human. The human merges explicitly. The reviewer
does not merge and does not deploy.

Until a later status-check story, GitHub does not enforce the `.agent`
verdict. Passing `quality` alone is not that peer review.

### Dry-run checklist

Use this on the next API pull request. Completing the checklist does not
authorize merging that pull request.

Coordinator (OpenAI `.agent`, independent of the authoring session):

- Confirm this session is the `.agent` coordinator, not the Codex Cloud
  session that opened the pull request.
- Review the diff at the latest commit SHA.
- Check linked acceptance criteria, architecture, contracts, security, data
  provenance, PHI boundaries, tests, current `quality`, and deployment side
  effects.
- Publish the advisory verdict `APPROVE`, `REQUEST CHANGES`, or `BLOCKED`
  with evidence and actionable findings, and state that it is not a
  GitHub-native approval from a separate identity.
- On `REQUEST CHANGES`, send the findings to the originating Codex Cloud
  session, then re-review the latest SHA after the update and a new
  `quality` run.
- On `BLOCKED`, wait for an owner decision.
- On `APPROVE` with green current `quality`, resolved conversations, and an
  up-to-date branch, notify the human. Do not merge and do not deploy.

Human:

- Confirm current `quality` is green, conversations are resolved, and the
  branch is up to date with `main`.
- Confirm the `.agent` advisory verdict on that same SHA is `APPROVE`, or
  record an explicit owner decision when the verdict was `BLOCKED`.
- Merge explicitly. Leave auto-merge off.
- Let the existing `main` path run `quality` and then `deploy`.

### Future follow-up

After this manual loop is proven, an optional GitHub-enforced `.agent`
peer-review status check can require a head-SHA pass or fail, a verified
`.agent` execution identity, fail-closed behavior when the check is missing
or stale, and a repair loop that cannot be self-attested by the authoring
agent. That check would stay separate from `quality` and from the human
merge. It needs its own design and authorization.

Phase 1 deliberately excludes OpenAI API billing and credentials,
third-party GitHub Apps, webhooks, runners, and new Actions jobs.
