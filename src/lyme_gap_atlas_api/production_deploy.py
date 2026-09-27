"""Promote the Atlas API only when this run's commit is still ``origin/main``.

DigitalOcean App Platform's create-deployment call rebuilds the branch configured
on the app (``main``). It does not accept a commit SHA. Calling it from a
superseded GitHub run would build whatever ``main`` is at clone time, including
a commit this run did not validate.

The script therefore refuses the API call unless ``CANDIDATE_SHA`` is exactly
``origin/main``, then requires the ``api`` service ``source_commit_hash`` to
match that same SHA before reporting success. A superseded candidate exits 0.
A built SHA that differs from the candidate fails the job and cancels the
in-progress DigitalOcean deployment.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

APP_NAME = "api"
SERVICE_NAME = "api"
TERMINAL_FAILURE_PHASES = frozenset({"ERROR", "CANCELED", "SUPERSEDED"})
POLL_ATTEMPTS = 60
POLL_SECONDS = 10
_API_ROOT = "https://api.digitalocean.com/v2/apps"


class DeploymentClient(Protocol):
    def create_deployment(self) -> Mapping[str, Any]: ...

    def get_deployment(self, deployment_id: str) -> Mapping[str, Any]: ...

    def cancel_deployment(self, deployment_id: str) -> None: ...


@dataclass(frozen=True)
class DeployIdentity:
    app: str
    branch: str
    candidate_sha: str
    run_id: str
    run_url: str
    timestamp: str


def identity_from_env(candidate_sha: str, *, timestamp: str | None = None) -> DeployIdentity:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_url = ""
    if repository and run_id:
        run_url = f"{server}/{repository}/actions/runs/{run_id}"
    return DeployIdentity(
        app=os.environ.get("APP_NAME", APP_NAME) or APP_NAME,
        branch=os.environ.get("GITHUB_REF_NAME", "main") or "main",
        candidate_sha=candidate_sha,
        run_id=run_id,
        run_url=run_url,
        timestamp=timestamp or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def format_identity(identity: DeployIdentity, result: str, **extra: str) -> str:
    lines = [
        f"app={identity.app}",
        f"branch={identity.branch}",
        f"candidate_sha={identity.candidate_sha}",
        f"run_id={identity.run_id}",
        f"run_url={identity.run_url}",
        f"timestamp={identity.timestamp}",
        f"result={result}",
    ]
    lines.extend(f"{key}={value}" for key, value in extra.items())
    return "\n".join(lines)


def candidate_is_current_main(candidate_sha: str, current_main_sha: str) -> bool:
    candidate = candidate_sha.strip().lower()
    current = current_main_sha.strip().lower()
    return bool(candidate) and candidate == current


def skip_message(candidate_sha: str, current_main_sha: str) -> str:
    return (
        "Deployment skipped.\n"
        f"Candidate SHA: {candidate_sha}\n"
        f"Current main:  {current_main_sha}\n"
        "Reason: candidate has been superseded by a newer main commit."
    )


def _deployment_body(deployment: Mapping[str, Any]) -> Mapping[str, Any]:
    body = deployment.get("deployment", deployment)
    if isinstance(body, Mapping):
        return body
    return {}


def deployment_id(deployment: Mapping[str, Any]) -> str:
    value = _deployment_body(deployment).get("id")
    if isinstance(value, str):
        return value.strip()
    return ""


def deployment_phase(deployment: Mapping[str, Any]) -> str:
    value = _deployment_body(deployment).get("phase")
    if isinstance(value, str):
        return value.strip()
    return ""


def service_commit_hash(deployment: Mapping[str, Any], service_name: str) -> str | None:
    services = _deployment_body(deployment).get("services")
    if not isinstance(services, list):
        return None
    for service in services:
        if not isinstance(service, Mapping) or service.get("name") != service_name:
            continue
        commit = service.get("source_commit_hash")
        if isinstance(commit, str) and commit.strip():
            return commit.strip()
    return None


def commit_matches(candidate_sha: str, built_sha: str) -> bool:
    candidate = candidate_sha.strip().lower()
    built = built_sha.strip().lower()
    return bool(candidate) and candidate == built


def fetch_main_sha(remote: str = "origin") -> str:
    subprocess.run(["git", "fetch", "--no-tags", remote, "main"], check=True)
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", f"{remote}/main"],
        check=True,
        capture_output=True,
        text=True,
    )
    sha = completed.stdout.strip()
    if not sha:
        raise RuntimeError(f"{remote}/main did not resolve to a commit")
    return sha


def _annotation(kind: str, message: str) -> str:
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::{kind}::{escaped}"


def _write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")


class DigitalOceanApps:
    """Minimal App Platform client. The bearer token is never logged."""

    def __init__(self, app_id: str, token: str, *, timeout_seconds: float = 30) -> None:
        self._base = f"{_API_ROOT}/{app_id}"
        self._token = token
        self._timeout_seconds = timeout_seconds

    def create_deployment(self) -> Mapping[str, Any]:
        return self._request("POST", f"{self._base}/deployments", {"force_build": True})

    def get_deployment(self, deployment_id: str) -> Mapping[str, Any]:
        return self._request("GET", f"{self._base}/deployments/{deployment_id}")

    def cancel_deployment(self, deployment_id: str) -> None:
        self._request("POST", f"{self._base}/deployments/{deployment_id}/cancel", {})

    def _request(
        self,
        method: str,
        url: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"DigitalOcean {method} failed with HTTP {exc.code}: {detail}"
            ) from exc
        if not raw.strip():
            return {}
        parsed = json.loads(raw.decode())
        if not isinstance(parsed, dict):
            raise RuntimeError("DigitalOcean response was not a JSON object")
        return parsed


def promote(
    *,
    candidate_sha: str,
    current_main_sha: str,
    client: DeploymentClient,
    identity: DeployIdentity,
    service_name: str = SERVICE_NAME,
    sleep: Callable[[float], None] = time.sleep,
    poll_attempts: int = POLL_ATTEMPTS,
    poll_seconds: float = POLL_SECONDS,
    log: Callable[[str], None] = print,
) -> int:
    """Return 0 for a deploy or a clean skip, and 1 when promotion fails."""

    log(format_identity(identity, "pending", current_main_sha=current_main_sha))
    if not candidate_is_current_main(candidate_sha, current_main_sha):
        message = skip_message(candidate_sha, current_main_sha)
        log(message)
        log(_annotation("notice", message))
        summary = format_identity(identity, "skipped", current_main_sha=current_main_sha)
        log(summary)
        _write_summary(summary)
        return 0

    created = client.create_deployment()
    created_id = deployment_id(created)
    if not created_id:
        summary = format_identity(identity, "failure", reason="missing_deployment_id")
        log(summary)
        _write_summary(summary)
        return 1
    log(f"Triggered DigitalOcean deployment {created_id}")

    for _attempt in range(poll_attempts):
        deployment = client.get_deployment(created_id)
        phase = deployment_phase(deployment)
        built = service_commit_hash(deployment, service_name)
        log(f"DigitalOcean deployment phase: {phase or 'UNKNOWN'}")
        if built is not None and not commit_matches(candidate_sha, built):
            return _fail_mismatched_build(
                client,
                identity,
                created_id,
                candidate_sha,
                built,
                phase,
                log,
            )
        if phase == "ACTIVE":
            if built is None or not commit_matches(candidate_sha, built):
                summary = format_identity(
                    identity,
                    "failure",
                    reason="active_without_matching_sha",
                    deployment_id=created_id,
                )
                log(summary)
                log(_annotation("error", summary))
                _write_summary(summary)
                return 1
            summary = format_identity(
                identity,
                "success",
                built_sha=built,
                deployment_id=created_id,
            )
            log(summary)
            _write_summary(summary)
            return 0
        if phase in TERMINAL_FAILURE_PHASES:
            summary = format_identity(
                identity,
                "failure",
                phase=phase,
                deployment_id=created_id,
            )
            log(f"DigitalOcean deployment {created_id} ended in {phase}.")
            log(summary)
            log(_annotation("error", summary))
            _write_summary(summary)
            return 1
        sleep(poll_seconds)

    summary = format_identity(identity, "failure", reason="timeout", deployment_id=created_id)
    log(f"Timed out waiting for DigitalOcean deployment {created_id}.")
    log(summary)
    log(_annotation("error", summary))
    _write_summary(summary)
    return 1


def _fail_mismatched_build(
    client: DeploymentClient,
    identity: DeployIdentity,
    created_id: str,
    candidate_sha: str,
    built_sha: str,
    phase: str,
    log: Callable[[str], None],
) -> int:
    message = (
        "DigitalOcean built a different commit than this run validated.\n"
        f"Candidate SHA: {candidate_sha}\n"
        f"Built SHA:     {built_sha}\n"
        "Reason: create-deployment builds the configured branch tip, "
        "which no longer matches this run."
    )
    log(message)
    if phase != "ACTIVE":
        try:
            client.cancel_deployment(created_id)
        except RuntimeError as exc:
            log(f"Failed to cancel DigitalOcean deployment {created_id}: {exc}")
        else:
            log(f"Cancelled DigitalOcean deployment {created_id}")
    summary = format_identity(
        identity,
        "failure",
        reason="built_sha_mismatch",
        built_sha=built_sha,
        deployment_id=created_id,
        phase=phase or "UNKNOWN",
    )
    log(summary)
    log(_annotation("error", summary))
    _write_summary(summary)
    return 1


def main() -> int:
    candidate = os.environ.get("CANDIDATE_SHA", "").strip()
    app_id = os.environ.get("DIGITALOCEAN_APP_ID", "").strip()
    token = os.environ.get("DIGITALOCEAN_ACCESS_TOKEN", "").strip()
    if not candidate or not app_id or not token:
        print(
            "CANDIDATE_SHA, DIGITALOCEAN_APP_ID, and DIGITALOCEAN_ACCESS_TOKEN are required.",
            file=sys.stderr,
        )
        return 1
    identity = identity_from_env(candidate)
    try:
        current = fetch_main_sha()
    except (subprocess.CalledProcessError, RuntimeError):
        summary = format_identity(identity, "failure", reason="main_fetch_failed")
        print(summary, file=sys.stderr)
        print(_annotation("error", summary), file=sys.stderr)
        _write_summary(summary)
        return 1
    client = DigitalOceanApps(app_id, token)
    return promote(
        candidate_sha=candidate,
        current_main_sha=current,
        client=client,
        identity=identity,
    )


if __name__ == "__main__":
    raise SystemExit(main())
