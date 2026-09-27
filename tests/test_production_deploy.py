"""Latest-main promotion decisions and the quality/deploy workflow contract."""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from lyme_gap_atlas_api.production_deploy import (
    DeployIdentity,
    DigitalOceanApps,
    candidate_is_current_main,
    commit_matches,
    fetch_main_sha,
    main,
    promote,
    service_commit_hash,
    skip_message,
)

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "quality-deploy.yml"


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _commit_remote(remote: Path, message: str) -> str:
    remote.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(remote)], check=True)
    _git(remote, "config", "user.email", "deploy@example.com")
    _git(remote, "config", "user.name", "Deploy Test")
    (remote / "README").write_text(f"{message}\n", encoding="utf-8")
    _git(remote, "add", "README")
    _git(remote, "commit", "-m", message)
    return _git(remote, "rev-parse", "HEAD")
CANDIDATE = "a" * 40
NEWER = "b" * 40
IDENTITY = DeployIdentity(
    app="api",
    branch="main",
    candidate_sha=CANDIDATE,
    run_id="4242",
    run_url="https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/actions/runs/4242",
    timestamp="2026-09-27T00:00:00Z",
)


class FakeApps:
    def __init__(self, polls: list[dict[str, Any]]) -> None:
        self.polls = polls
        self.created_calls = 0
        self.cancelled: list[str] = []
        self.fail_cancel = False

    def create_deployment(self) -> Mapping[str, Any]:
        self.created_calls += 1
        return {"deployment": {"id": "dep-1", "phase": "PENDING"}}

    def get_deployment(self, deployment_id: str) -> Mapping[str, Any]:
        assert deployment_id == "dep-1"
        if not self.polls:
            raise AssertionError("unexpected extra poll")
        return self.polls.pop(0)

    def cancel_deployment(self, deployment_id: str) -> None:
        if self.fail_cancel:
            raise RuntimeError("cancel refused")
        self.cancelled.append(deployment_id)


def _active(commit: str) -> dict[str, Any]:
    return {
        "deployment": {
            "id": "dep-1",
            "phase": "ACTIVE",
            "services": [{"name": "api", "source_commit_hash": commit}],
        }
    }


def _building(commit: str | None, phase: str = "BUILDING") -> dict[str, Any]:
    service: dict[str, Any] = {"name": "api"}
    if commit is not None:
        service["source_commit_hash"] = commit
    return {"deployment": {"id": "dep-1", "phase": phase, "services": [service]}}


def _run(client: FakeApps, current: str, candidate: str = CANDIDATE) -> tuple[int, list[str]]:
    logs: list[str] = []
    code = promote(
        candidate_sha=candidate,
        current_main_sha=current,
        client=client,
        identity=IDENTITY,
        sleep=lambda _seconds: None,
        poll_attempts=3,
        log=logs.append,
    )
    return code, logs


def test_superseded_candidate_skips_without_calling_digitalocean() -> None:
    client = FakeApps([])
    code, logs = _run(client, NEWER)
    text = "\n".join(logs)

    assert code == 0
    assert client.created_calls == 0
    assert "Deployment skipped." in text
    assert f"Candidate SHA: {CANDIDATE}" in text
    assert f"Current main:  {NEWER}" in text
    assert "Reason: candidate has been superseded by a newer main commit." in text
    assert "result=skipped" in text
    assert "app=api" in text
    assert "run_id=4242" in text
    assert IDENTITY.run_url in text


def test_matching_tip_deploys_only_after_built_sha_matches() -> None:
    client = FakeApps([_building(None), _building(CANDIDATE), _active(CANDIDATE)])
    code, logs = _run(client, CANDIDATE)
    text = "\n".join(logs)

    assert code == 0
    assert client.created_calls == 1
    assert client.cancelled == []
    assert "result=success" in text
    assert f"built_sha={CANDIDATE}" in text
    assert "deployment_id=dep-1" in text
    assert "Triggered DigitalOcean deployment dep-1" in text


def test_older_commit_cannot_deploy_after_newer_main_exists() -> None:
    """Scenario A: an older validated commit finishes after main has moved."""

    client = FakeApps([_active(CANDIDATE)])
    code, _logs = _run(client, NEWER, candidate="c" * 40)
    assert code == 0
    assert client.created_calls == 0


def test_branch_moved_after_the_api_call_cancels_the_in_progress_deployment() -> None:
    """Scenario B: DigitalOcean cloned a different tip than this run validated."""

    client = FakeApps([_building(NEWER, phase="BUILDING")])
    code, logs = _run(client, CANDIDATE)
    text = "\n".join(logs)

    assert code == 1
    assert client.cancelled == ["dep-1"]
    assert "result=failure" in text
    assert "reason=built_sha_mismatch" in text
    assert f"Built SHA:     {NEWER}" in text
    assert "Cancelled DigitalOcean deployment dep-1" in text


def test_active_deployment_of_a_different_sha_is_not_reported_as_success() -> None:
    client = FakeApps([_active(NEWER)])
    code, logs = _run(client, CANDIDATE)

    assert code == 1
    assert client.cancelled == []
    assert "result=failure" in "\n".join(logs)
    assert "built_sha_mismatch" in "\n".join(logs)


def test_cancel_failure_still_fails_the_promotion() -> None:
    client = FakeApps([_building(NEWER)])
    client.fail_cancel = True
    code, logs = _run(client, CANDIDATE)

    assert code == 1
    assert "Failed to cancel DigitalOcean deployment dep-1" in "\n".join(logs)


def test_terminal_digitalocean_phase_fails() -> None:
    client = FakeApps([_building(None, phase="ERROR")])
    code, logs = _run(client, CANDIDATE)

    assert code == 1
    assert "ended in ERROR" in "\n".join(logs)
    assert "result=failure" in "\n".join(logs)
    assert "phase=ERROR" in "\n".join(logs)


def test_timeout_fails_when_the_deployment_never_becomes_active() -> None:
    client = FakeApps([_building(None), _building(None), _building(None)])
    code, logs = _run(client, CANDIDATE)

    assert code == 1
    assert "Timed out waiting for DigitalOcean deployment dep-1." in "\n".join(logs)
    assert "reason=timeout" in "\n".join(logs)


def test_active_without_source_commit_hash_fails_closed() -> None:
    client = FakeApps([_building(None, phase="ACTIVE")])
    code, logs = _run(client, CANDIDATE)

    assert code == 1
    assert "reason=active_without_matching_sha" in "\n".join(logs)


def test_missing_deployment_id_fails_before_polling() -> None:
    class NoId(FakeApps):
        def create_deployment(self) -> Mapping[str, Any]:
            self.created_calls += 1
            return {"deployment": {}}

    client = NoId([])
    code, logs = _run(client, CANDIDATE)

    assert code == 1
    assert "reason=missing_deployment_id" in "\n".join(logs)


def test_sha_comparison_is_case_insensitive_and_rejects_blanks() -> None:
    assert candidate_is_current_main(CANDIDATE.upper(), CANDIDATE)
    assert not candidate_is_current_main("", "")
    assert not candidate_is_current_main(CANDIDATE, NEWER)
    assert commit_matches(CANDIDATE.upper(), CANDIDATE)
    other_service = {"services": [{"name": "other", "source_commit_hash": CANDIDATE}]}
    assert service_commit_hash(other_service, "api") is None
    assert "superseded" in skip_message(CANDIDATE, NEWER)


def test_skip_and_success_write_the_step_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    client = FakeApps([])
    assert _run(client, NEWER)[0] == 0
    assert "result=skipped" in summary.read_text(encoding="utf-8")

    summary.write_text("", encoding="utf-8")
    client = FakeApps([_active(CANDIDATE)])
    assert _run(client, CANDIDATE)[0] == 0
    assert "result=success" in summary.read_text(encoding="utf-8")


def test_fetch_main_sha_reads_origin_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote = tmp_path / "remote"
    work = tmp_path / "work"
    expected = _commit_remote(remote, "tip")
    subprocess.run(["git", "clone", str(remote), str(work)], check=True)
    monkeypatch.chdir(work)

    assert fetch_main_sha() == expected


def test_main_skips_a_superseded_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    remote = tmp_path / "remote"
    work = tmp_path / "work"
    tip = _commit_remote(remote, "newer")
    subprocess.run(["git", "clone", str(remote), str(work)], check=True)
    monkeypatch.chdir(work)
    monkeypatch.setenv("CANDIDATE_SHA", "d" * 40)
    monkeypatch.setenv("DIGITALOCEAN_APP_ID", "app-1")
    monkeypatch.setenv("DIGITALOCEAN_ACCESS_TOKEN", "super-secret-token")
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Caraway-Labs/one-health-lyme-gap-atlas-api")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setenv("APP_NAME", "api")

    assert main() == 0
    captured = capsys.readouterr()
    assert "Deployment skipped." in captured.out
    assert f"Current main:  {tip}" in captured.out
    assert "super-secret-token" not in captured.out
    assert "super-secret-token" not in captured.err


def test_main_requires_promotion_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CANDIDATE_SHA", raising=False)
    monkeypatch.delenv("DIGITALOCEAN_APP_ID", raising=False)
    monkeypatch.delenv("DIGITALOCEAN_ACCESS_TOKEN", raising=False)
    assert main() == 1


def test_main_reports_a_failed_main_fetch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CANDIDATE_SHA", CANDIDATE)
    monkeypatch.setenv("DIGITALOCEAN_APP_ID", "app-1")
    monkeypatch.setenv("DIGITALOCEAN_ACCESS_TOKEN", "super-secret-token")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    assert main() == 1
    text = summary.read_text(encoding="utf-8")
    assert "reason=main_fetch_failed" in text
    assert "super-secret-token" not in text


def test_digitalocean_client_pins_force_build_and_hides_the_token() -> None:
    seen: list[tuple[str, str, bytes, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._record()
            body = json.dumps(
                {"deployment": {"id": "dep-9", "phase": "ACTIVE", "services": []}}
            ).encode()
            self._json(body)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length)
            seen.append((self.command, self.path, payload, self.headers.get("Authorization")))
            if self.path.endswith("/boom"):
                self.send_response(422)
                self.end_headers()
                self.wfile.write(b"nope")
                return
            self._json(b'{"deployment":{"id":"dep-9","phase":"PENDING"}}')

        def _record(self) -> None:
            seen.append((self.command, self.path, b"", self.headers.get("Authorization")))

        def _json(self, body: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    token = "super-secret-token"
    client = DigitalOceanApps("app-1", token, timeout_seconds=5)
    client._base = f"http://{host}:{port}/v2/apps/app-1"  # noqa: SLF001
    try:
        created = client.create_deployment()
        fetched = client.get_deployment("dep-9")
        client.cancel_deployment("dep-9")
        with pytest.raises(RuntimeError, match="HTTP 422") as exc_info:
            client._request("POST", f"{client._base}/boom", {})  # noqa: SLF001
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert created["deployment"]["id"] == "dep-9"
    assert fetched["deployment"]["phase"] == "ACTIVE"
    methods = [item[0] for item in seen]
    assert methods == ["POST", "GET", "POST", "POST"]
    assert json.loads(seen[0][2]) == {"force_build": True}
    assert all(item[3] == f"Bearer {token}" for item in seen)
    assert token not in str(exc_info.value)


def test_digitalocean_client_rejects_a_non_object_body() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"[1]")

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = DigitalOceanApps("app-1", "token", timeout_seconds=5)
    client._base = f"http://{host}:{port}"  # noqa: SLF001
    try:
        with pytest.raises(RuntimeError, match="JSON object"):
            client.get_deployment("dep-1")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_workflow_keeps_quality_parallel_and_serializes_only_deploy() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    quality, deploy = text.split("\n  deploy:\n", maxsplit=1)

    assert "concurrency:" not in quality
    assert "CI_MAX_PARALLEL=4" in quality
    assert "needs: quality" in deploy
    assert "atlas-api-production" in deploy
    assert "cancel-in-progress: true" in deploy
    assert "cancel-in-progress: false" not in text
    assert "group: api-production" not in text
    assert "LATEST_MAIN_WINS=true" in deploy
    assert "API_PRODUCTION_CONCURRENCY_GROUP" in deploy
    assert "production_deploy" in deploy
    assert "CANDIDATE_SHA: ${{ github.sha }}" in deploy
    assert "github.event_name == 'push'" in deploy
    assert "github.event_name == 'workflow_dispatch'" in deploy
    assert "inputs.deploy_production" in deploy
    assert "github.ref == 'refs/heads/main'" in deploy
    assert "vars.DIGITALOCEAN_APP_ID != ''" in deploy
    assert "always()" not in deploy
    assert "failure()" not in deploy
    assert "environment:" in deploy
    assert "name: production" in deploy

    for gate in (
        "uv sync --extra dev --locked",
        "uv run ruff check .",
        "uv run mypy",
        "uv run pytest -q",
        "codecov/codecov-action@v5",
        "uv run python scripts/export_openapi.py",
        "git diff --exit-code -- openapi.json",
        "docker build --tag lyme-atlas-api:quality .",
        "tests/test_typst_renderer.py",
        "ghcr.io/gitleaks/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f",
    ):
        assert gate in quality

    assert "deploy_on_push" not in text
