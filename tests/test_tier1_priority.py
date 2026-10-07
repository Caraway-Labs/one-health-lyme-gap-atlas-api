"""Persisted Tier 1 contract and presentation-only read boundary."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError
from lyme_gap_atlas_api.tier1_priority import SnowflakeTier1PriorityRepository


def row(tier: str = "HIGH", sufficiency: str = "SUFFICIENT") -> tuple[Any, ...]:
    return (
        "09110",
        tier,
        98.5,
        2.07,
        sufficiency,
        "tier1-statistical-reference-v1",
        "tier1-review-priority-744b2933bae43718",
        "tier1-review-percentile-v1",
        datetime(2026, 10, 6, 5, 34, 52, tzinfo=UTC),
        "release-1",
        "a" * 40,
        json.dumps([{"code": "PATHOGEN_PRESENT", "text": "Publisher reports pathogen Present."}]),
        "docs/contracts/tier1-persisted-output-v1.md",
    )


class FixtureRepository:
    def __init__(self, result: tuple[Any, ...] | None) -> None:
        self.result = result

    def county(self, fips: str) -> tuple[Any, ...] | None:
        return self.result if fips == "09110" else None


@pytest.mark.parametrize("tier", ["HIGH", "MEDIUM", "LOW"])
def test_persisted_tiers_and_lineage(tier: str) -> None:
    api = TestClient(
        create_app(settings=ApiSettings(), tier1_priority_repository=FixtureRepository(row(tier)))
    )
    response = api.get("/v1/counties/09110/tier1-surveillance-priority")
    assert response.status_code == 200
    value = response.json()
    assert value["priority_tier"] == tier
    assert value["priority_percentile"] == 98.5
    assert value["model_version"] == "tier1-statistical-reference-v1"
    assert value["release_id"] == "release-1"
    assert value["source_commit"] == "a" * 40
    assert value["prediction_batch_version"] == "tier1-review-priority-744b2933bae43718"
    assert value["generated_at_utc"] == "2026-10-06T05:34:52Z"
    assert value["reasons"] == [
        {"code": "PATHOGEN_PRESENT", "text": "Publisher reports pathogen Present."}
    ]
    assert value["limitation_ref"] == "docs/contracts/tier1-persisted-output-v1.md"
    assert response.headers["Cache-Control"] == "public, max-age=60"


def test_insufficient_is_separate_from_low_and_absent_has_no_fallback() -> None:
    api = TestClient(
        create_app(
            settings=ApiSettings(),
            tier1_priority_repository=FixtureRepository(row("LOW", "INSUFFICIENT")),
        )
    )
    assert (
        api.get("/v1/counties/09110/tier1-surveillance-priority").json()["evidence_sufficiency"]
        == "INSUFFICIENT"
    )
    assert api.get("/v1/counties/99999/tier1-surveillance-priority").status_code == 404
    absent = TestClient(
        create_app(settings=ApiSettings(), tier1_priority_repository=FixtureRepository(None))
    )
    assert absent.get("/v1/counties/09110/tier1-surveillance-priority").status_code == 404


def test_not_estimable_preserves_null_tier() -> None:
    item = row("LOW", "NOT_ESTIMABLE")
    item = item[:1] + (None, None, None) + item[4:]
    api = TestClient(
        create_app(settings=ApiSettings(), tier1_priority_repository=FixtureRepository(item))
    )
    result = api.get("/v1/counties/09110/tier1-surveillance-priority")
    assert result.status_code == 200
    assert result.json()["priority_tier"] is None
    assert result.json()["priority_percentile"] is None
    assert result.json()["raw_model_score"] is None
    assert result.json()["evidence_sufficiency"] == "NOT_ESTIMABLE"


@pytest.mark.parametrize(
    ("sufficiency", "tier", "percentile", "score"),
    [
        ("NOT_ESTIMABLE", "HIGH", None, None),
        ("NOT_ESTIMABLE", None, 50.0, None),
        ("NOT_ESTIMABLE", None, None, 1.0),
        ("SUFFICIENT", None, 50.0, 1.0),
        ("SUFFICIENT", "HIGH", None, 1.0),
        ("SUFFICIENT", "HIGH", 50.0, None),
        ("INSUFFICIENT", None, 50.0, 1.0),
        ("INSUFFICIENT", "LOW", None, 1.0),
        ("INSUFFICIENT", "LOW", 50.0, None),
        ("SUFFICIENT", "HIGH", -0.1, 1.0),
        ("SUFFICIENT", "HIGH", 100.1, 1.0),
        ("SUFFICIENT", "HIGH", float("nan"), 1.0),
        ("SUFFICIENT", "HIGH", 50.0, float("inf")),
    ],
)
def test_invalid_score_state_fails_closed(
    sufficiency: str,
    tier: str | None,
    percentile: float | None,
    score: float | None,
) -> None:
    item = list(row())
    item[1:5] = [tier, percentile, score, sufficiency]
    api = TestClient(
        create_app(settings=ApiSettings(), tier1_priority_repository=FixtureRepository(tuple(item)))
    )
    assert api.get("/v1/counties/09110/tier1-surveillance-priority").status_code == 503


@pytest.mark.parametrize(
    "bad",
    [
        row("UNKNOWN"),
        row("LOW")[:11] + ('[{"code":"x"}]', "ref"),
        row()[:11] + ("{}", "ref"),
        row()[:1] + (None,) + row()[2:],
    ],
)
def test_malformed_persisted_values_fail_closed(bad: tuple[Any, ...]) -> None:
    api = TestClient(
        create_app(settings=ApiSettings(), tier1_priority_repository=FixtureRepository(bad))
    )
    assert api.get("/v1/counties/09110/tier1-surveillance-priority").status_code == 503


def test_repository_uses_only_presentation_view(monkeypatch: pytest.MonkeyPatch) -> None:
    statements: list[str] = []

    class Cursor:
        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def execute(self, statement: str, params: tuple[str], **_: object) -> None:
            statements.append(statement)
            assert params == ("09110",)

        def fetchall(self) -> list[tuple[Any, ...]]:
            return [row()]

    class Connection:
        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def cursor(self) -> Cursor:
            return Cursor()

    monkeypatch.setattr("lyme_gap_atlas_api.tier1_priority.connect", lambda _: Connection())
    repository = SnowflakeTier1PriorityRepository(
        ApiSettings(snowflake_database="ONE_HEALTH_LYME_GAP_ATLAS_DEV")
    )
    assert repository.county("09110") == row()
    assert '"PRESENTATION".CURRENT_TIER1_COUNTY_REVIEW_V' in statements[0]
    assert "FEATURE_STORE" not in statements[0]


def test_openapi_semantics() -> None:
    app = create_app(settings=ApiSettings())
    for schema in (app.openapi(), app.first_party_openapi()):
        operation = schema["paths"]["/v1/counties/{fips}/tier1-surveillance-priority"]["get"]
        assert "not Lyme disease risk" in operation["description"]
        assert "LOW is a scored tier" in operation["description"]
        assert "404" in operation["responses"] and "503" in operation["responses"]
        fields = schema["components"]["schemas"]["Tier1CountyPriority"]["properties"]
        assert "surveillance review priority" in fields["priority_tier"]["description"]
        assert "Within-batch relative position" in fields["priority_percentile"]["description"]
        assert "not comparable across batches" in fields["priority_percentile"]["description"]
        assert "Model-native anomaly score" in fields["raw_model_score"]["description"]
        assert "not calibrated model confidence" in fields["evidence_sufficiency"]["description"]
        assert "INSUFFICIENT both require" in fields["evidence_sufficiency"]["description"]
        for field in (
            "model_version",
            "prediction_batch_version",
            "tier_policy_version",
            "generated_at_utc",
        ):
            assert fields[field]["description"]


def test_unavailable_is_not_absent() -> None:
    class Unavailable:
        def county(self, fips: str) -> tuple[Any, ...] | None:
            raise AtlasDataUnavailableError("Unavailable")

    api = TestClient(create_app(settings=ApiSettings(), tier1_priority_repository=Unavailable()))
    assert api.get("/v1/counties/09110/tier1-surveillance-priority").status_code == 503


def test_empty_current_view_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class Cursor:
        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def execute(self, *_: object, **__: object) -> None:
            pass

        def fetchall(self) -> list[tuple[Any, ...]]:
            return []

        def fetchone(self) -> None:
            return None

    class Connection:
        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def cursor(self) -> Cursor:
            return Cursor()

    monkeypatch.setattr("lyme_gap_atlas_api.tier1_priority.connect", lambda _: Connection())
    repo = SnowflakeTier1PriorityRepository(ApiSettings())
    with pytest.raises(AtlasDataUnavailableError, match="No active"):
        repo.county("09110")
