"""Persisted values, missingness, release isolation and bounded SQL contract."""

import copy
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError
from lyme_gap_atlas_api.surveillance_situations import (
    MODELS,
    SnowflakeSituationRepository,
    assemble,
)

RELEASE = ("fixture-release", "a" * 64)


def rows(index: int = 1) -> list[tuple[Any, ...]]:
    result = []
    for model in MODELS.values():
        data = {
            "county_fips": "02013",
            "model_id": model,
            "model_version": "v1",
            "class_id": f"{model}.v1.class-{index}",
            "class_name": "Unknown tick and pathogen status",
            "class_description": "Fixture.",
            "probabilities": [
                {"class_id": f"{model}.v1.class-{i}", "probability": float(i == index)}
                for i in range(4)
            ],
            "limitations": ["not disease risk"],
            "batch_id": "fixture-batch",
            "batch_version": "v1",
            "county_sha256": "b" * 64,
            "dictionary_sha256": "c" * 64,
            "input_release_id": RELEASE[0],
            "input_bundle_sha256": RELEASE[1],
            "release_id": RELEASE[0],
            "bundle_sha256": RELEASE[1],
            "fit_git_sha": "d" * 40,
            "feature_contract": {"config": "fixture"},
            "model_provenance": {"method": "fixture"},
            "source_provenance": {"vintage": "2023"},
            "handoff_approval": {"approved": True},
            "data_publication_authorization": {"approved": True},
            "data_authorization_id": "fixture",
            "staging_attempt": "fixture",
            "activated_at": "2026-10-19T00:00:00Z",
        }
        a = {
            key: data[key]
            for key in (
                "county_fips",
                "model_id",
                "release_id",
                "bundle_sha256",
                "batch_id",
                "batch_version",
                "input_release_id",
                "class_id",
            )
        }
        a.update(availability="AVAILABLE", unavailable_reason=None)
        result.append((a, data))
    return result


@pytest.mark.parametrize("index", range(4))
def test_both_variants_and_all_primary_classes(index: int) -> None:
    source = rows(index)
    value = assemble("02013", RELEASE, source)
    for variant in (value.four_state, value.tick_pathogen):
        assert variant.data is not None
        assert variant.data.class_id.endswith(f"class-{index}")
        assert variant.data.probabilities[index].probability == 1
        assert variant.compatibility == "EXACT_INPUT"
    assert value.four_state.data.model_dump(mode="json")["source_provenance"] == {"vintage": "2023"}


@pytest.mark.parametrize(
    "reason",
    [
        "NOT_PUBLISHED",
        "DEACTIVATED",
        "UNAPPROVED_BATCH",
        "INCOMPATIBLE_RELEASE",
        "MISSING_ASSIGNMENT",
    ],
)
def test_unavailable_is_separate_from_unknown(reason: str) -> None:
    source = rows()
    source[0][0].update(availability="UNAVAILABLE", unavailable_reason=reason, class_id=None)
    source[0] = (source[0][0], {"class_id": None})
    value = assemble("02013", RELEASE, source)
    assert value.four_state.data is None
    assert value.four_state.unavailable_reason == reason
    assert value.tick_pathogen.data.class_name == "Unknown tick and pathogen status"


@pytest.mark.parametrize(
    "defect",
    [
        "sum",
        "nan",
        "negative",
        "duplicate_probability",
        "county",
        "release",
        "batch",
        "missing",
        "duplicate",
        "label",
    ],
)
def test_malformed_persistence_fails_closed(defect: str) -> None:
    source = rows()
    data = source[0][1]
    if defect == "sum":
        data["probabilities"][0]["probability"] = 0.5
    elif defect == "nan":
        data["probabilities"][0]["probability"] = float("nan")
    elif defect == "negative":
        data["probabilities"][0]["probability"] = -0.01
    elif defect == "duplicate_probability":
        data["probabilities"][0] = copy.deepcopy(data["probabilities"][1])
    elif defect == "county":
        data["county_fips"] = "01001"
    elif defect == "release":
        data["bundle_sha256"] = "e" * 64
    elif defect == "batch":
        data["batch_version"] = "v2"
    elif defect == "label":
        data["class_name"] = None
    elif defect == "missing":
        source.pop()
    else:
        source[1] = source[0]
    with pytest.raises(ValueError):
        assemble("02013", RELEASE, source)


def test_explicit_admitted_carry_forward() -> None:
    source = rows()
    for a, data in source:
        a["input_release_id"] = data["input_release_id"] = "original-input"
    assert assemble("02013", RELEASE, source).four_state.compatibility == "ADMITTED_CARRY_FORWARD"


def fake_connection(monkeypatch: pytest.MonkeyPatch, outputs: list[Any]) -> list[Any]:
    calls: list[Any] = []

    class Cursor:
        def execute(self, sql: str, params: Any, **kwargs: Any) -> None:
            calls.append((sql, params, kwargs))

        def fetchall(self) -> Any:
            value = outputs.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    class Connection(Cursor):
        def cursor(self) -> Cursor:
            return Cursor()

    @contextmanager
    def connect(*args: Any):
        yield Connection()

    monkeypatch.setattr("lyme_gap_atlas_api.surveillance_situations.read_connection", connect)
    return calls


def client() -> TestClient:
    return TestClient(create_app(settings=ApiSettings(rate_limit_requests=1000)))


def test_http_and_bounded_parameterized_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    persisted = [(json.dumps(a), json.dumps(d)) for a, d in rows()]
    calls = fake_connection(monkeypatch, [[RELEASE], [(RELEASE[0],)], persisted, [RELEASE]])
    response = client().get("/v1/counties/02013/surveillance-situations")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["county_fips"] == "02013"
    assert len(calls) == 4
    assert all(sql.startswith("SELECT ") and "LIMIT " in sql for sql, _, _ in calls)
    assert calls[1][1] == calls[2][1] == ("02013",)
    assert all(call[2]["timeout"] > 0 for call in calls)
    assert not any("FEATURE_STORE" in call[0] for call in calls)


@pytest.mark.parametrize("reason", ["NOT_PUBLISHED", "INCOMPATIBLE_RELEASE"])
def test_http_unavailable_does_not_fabricate_class(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    source = rows()
    a = source[0][0]
    a.update(availability="UNAVAILABLE", unavailable_reason=reason, class_id=None)
    source[0] = (a, {"class_id": None})
    fake_connection(monkeypatch, [[RELEASE], [(RELEASE[0],)], source, [RELEASE]])
    response = client().get("/v1/counties/02013/surveillance-situations")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["four_state"]["data"] is None
    assert response.json()["four_state"]["unavailable_reason"] == reason
    assert response.json()["tick_pathogen"]["availability"] == "AVAILABLE"


def test_unknown_county(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_connection(monkeypatch, [[RELEASE], [], [RELEASE]])
    assert client().get("/v1/counties/99999/surveillance-situations").status_code == 404


@pytest.mark.parametrize("fips", ["1234", "abcde", "123456", "０２０１３"])
def test_invalid_fips_never_queries(monkeypatch: pytest.MonkeyPatch, fips: str) -> None:
    calls = fake_connection(monkeypatch, [])
    assert client().get(f"/v1/counties/{fips}/surveillance-situations").status_code == 422
    assert not calls


@pytest.mark.parametrize("failure", ["dependency", "changed_release", "missing_availability"])
def test_dependency_and_integrity_failures_are_503(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    outputs: list[Any] = [[RELEASE], [(RELEASE[0],)], rows(), [RELEASE]]
    if failure == "dependency":
        outputs = [RuntimeError("private SQL must not leak")]
    elif failure == "changed_release":
        outputs[-1] = [("new-release", "f" * 64)]
    else:
        outputs[2] = []
    fake_connection(monkeypatch, outputs)
    response = client().get("/v1/counties/02013/surveillance-situations")
    assert response.status_code == 503
    assert "private SQL" not in response.text


def test_adapter_wraps_validation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_connection(monkeypatch, [[RELEASE], [(RELEASE[0],)], [], [RELEASE]])
    with pytest.raises(AtlasDataUnavailableError):
        SnowflakeSituationRepository(ApiSettings()).county("02013")


def test_production_authoritative_readback_all_classes_and_lineage() -> None:
    source = json.loads(
        (
            Path(__file__).parent / "fixtures" / "surveillance-situations-prod-2026-10-19.json"
        ).read_text()
    )
    classes: dict[str, set[str]] = {model: set() for model in MODELS.values()}
    for fips in {a["COUNTY_FIPS"] for a, _ in source}:
        selected = [(a, d) for a, d in source if a["COUNTY_FIPS"] == fips]
        a = selected[0][0]
        value = assemble(fips, (a["RELEASE_ID"], a["BUNDLE_SHA256"]), selected)
        for variant in (value.four_state, value.tick_pathogen):
            data = variant.data
            assert data is not None
            assert data.input_release_id == "governed-2026-10-15-january-climate-prod"
            assert data.model_version == data.batch_version == "v1"
            assert data.batch_id == "surveillance-situation-batch"
            assert data.fit_git_sha == "cd936fe345664e606c916b137dd642caa0d6d73b"
            original = next(d for _, d in selected if d["MODEL_ID"] == data.model_id)
            assert data.class_name == original["CLASS_NAME"]
            assert data.class_description == original["CLASS_DESCRIPTION"]
            assert [p.model_dump() for p in data.probabilities] == original["PROBABILITIES"]
            assert data.source_provenance == original["SOURCE_PROVENANCE"]
            classes[data.model_id].add(data.class_id)
            if fips.startswith(("02", "15")):
                assert data.class_name == "Unknown tick and pathogen status"
                assert variant.availability == "AVAILABLE"
    for model, found in classes.items():
        assert found == {f"{model}.v1.class-{i}" for i in range(4)}
