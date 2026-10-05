"""API18 typed tools reuse canonical public services without an LLM or HTTP route."""

import json
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from test_public_observations import row

from lyme_gap_atlas_api import ask_atlas_tools
from lyme_gap_atlas_api.ask_atlas_tools import (
    CONTRACT_VERSION,
    StructuredTools,
    _coverage_id,
)
from lyme_gap_atlas_api.public_contract import (
    Indicator,
    Measure,
    Methodology,
    ObservationQuery,
    Source,
)
from lyme_gap_atlas_api.public_observations import ObservationService


class Repository:
    release = "release-1"

    def current_release(self):
        return self.release

    def measure_exists(self, measure_id, release):
        return measure_id == "case_count_floor_2023" and release == self.release

    def query(self, query, release, offset):
        rows = [
            row("01001", None, "MISSING"),
            row("01003", "0", "ZERO"),
            row("01005", "12", "OBSERVED"),
            row("01009", None, "SUPPRESSED"),
            row("01011", None, "UNAVAILABLE"),
        ]
        if query.year != 2023 and not (
            query.start_date and query.start_date.year <= 2023 <= query.end_date.year
        ):
            return []
        return [r for r in rows if r[2] in query.geography_id][offset:][: query.page_size + 1]


class Metadata:
    def discover(self):
        return (
            [
                Indicator(
                    indicator_id="human",
                    label="Human",
                    definition=None,
                    measure_ids=["case_count_floor_2023"],
                    semantic_version="1.0.0",
                    release_version="release-1",
                )
            ],
            [
                Measure(
                    measure_id="case_count_floor_2023",
                    indicator_id="human",
                    label="Case count floor",
                    definition=None,
                    semantic_version="1.0.0",
                    release_version="release-1",
                    unit="cases",
                    geography_semantics="COUNTY",
                    temporal_semantics="YEAR",
                )
            ],
        )


class Provenance:
    def source(self, identifier):
        return Source(
            source_id=identifier,
            label="Human surveillance",
            publisher="CDC",
            lineage_source_id="cdc_lyme",
            dataset_id="x5j9-wybp",
            semantic_version="1.0.0",
            release_version="release-1",
        )

    def methodology(self, identifier):
        return Methodology(
            methodology_id=identifier,
            measure_id="case_count_floor_2023",
            version="release-method-v1",
            description="count method",
            limitations=[],
            semantic_version="1.0.0",
            release_version="release-1",
        )


def tools():
    repository = Repository()
    return StructuredTools(Metadata(), ObservationService(repository), Provenance()), repository


def query(**updates):
    return {
        "contract_version": CONTRACT_VERSION,
        "measure_id": "case_count_floor_2023",
        "geography_type": "county",
        "geography_ids": ["01001", "01003", "01005", "01007"],
        "year": 2023,
        **updates,
    }


def test_observation_parity_coverage_metadata_and_release():
    service, repository = tools()
    found = service.find_measures(
        {"contract_version": CONTRACT_VERSION, "search_text": "  CASE   COUNT floor "}
    )
    assert found.status == "ok" and found.measures[0].measure_id == "case_count_floor_2023"
    result = service.get_observations(query())
    assert result.status == "ok" and result.release_id == "release-1"
    schema = json.loads(
        (Path(__file__).parents[1] / "docs" / "ask-atlas-results-v1.schema.json").read_text()
    )
    assert not list(
        Draft202012Validator(
            {**schema, "$ref": "#/$defs/tool_result"}, format_checker=FormatChecker()
        ).iter_errors(result.model_dump(mode="json"))
    )
    assert [o.value_state for o in result.observations] == ["MISSING", "ZERO", "OBSERVED"]
    assert [o.value for o in result.observations] == [None, 0, 12]
    assert [c.state for c in result.coverage] == ["present", "present", "present", "absent"]
    assert result.coverage[-1].coverage_id == _coverage_id(
        "release-1", "case_count_floor_2023", "01007", date(2023, 1, 1), date(2023, 12, 31)
    )
    assert (
        result.observations[0].model_dump()
        == ObservationService(repository)
        .search(
            ObservationQuery(
                measure_id="case_count_floor_2023",
                geography_type="county",
                geography_id=["01001", "01003", "01005", "01007"],
                year=2023,
                page_size=200,
            )
        )
        .data[0]
        .model_dump()
    )
    ids = [o.observation_id for o in result.observations]
    metadata = service.get_evidence_metadata(
        {"contract_version": CONTRACT_VERSION, "observation_ids": ids}
    )
    assert metadata.status == "ok" and len(metadata.metadata) == 3
    assert metadata.metadata[0].source.publisher == "CDC"
    repository.release = "release-2"
    race = service.get_evidence_metadata(
        {"contract_version": CONTRACT_VERSION, "observation_ids": ids}
    )
    assert race.status == "error" and race.error_code == "RELEASE_CHANGED"
    assert race.metadata == [] and race.release_id is None


def test_rejects_unbounded_invalid_and_unsupported_inputs():
    service, _ = tools()
    for payload, code in [
        (query(sql="SELECT *"), "INVALID_REQUEST"),
        (query(geography_ids=["01001"] * 21), "QUERY_TOO_BROAD"),
        (query(geography_ids=["bad"]), "INVALID_REQUEST"),
        (query(geography_ids=["01001", "01001"]), "INVALID_REQUEST"),
        (query(geography_type="state", geography_ids=["01"]), "UNSUPPORTED_FILTER"),
        (query(year=None, start_date="2023-03-01", end_date="2023-12-31"), "UNSUPPORTED_FILTER"),
        (query(year=None, start_date="1950-01-01", end_date="2011-12-31"), "QUERY_TOO_BROAD"),
    ]:
        result = service.get_observations(payload)
        assert result.status == "error" and result.error_code == code
        assert not result.observations and not result.coverage
    denied = service.get_evidence_metadata(
        {"contract_version": CONTRACT_VERSION, "observation_ids": ["foreign"]}
    )
    assert denied.error_code == "RESOURCE_NOT_FOUND"


def test_discovery_bounds_and_schema_no_escape_hatch():
    service, _ = tools()
    assert (
        service.find_measures(
            {"contract_version": CONTRACT_VERSION, "search_text": "case", "page_size": 1}
        ).status
        == "ok"
    )
    assert (
        service.find_measures(
            {"contract_version": CONTRACT_VERSION, "search_text": "none"}
        ).error_code
        == "RESOURCE_NOT_FOUND"
    )
    assert (
        service.find_measures(
            {"contract_version": CONTRACT_VERSION, "search_text": "case", "indicator_id": "human"}
        ).error_code
        == "INVALID_REQUEST"
    )
    assert (
        service.find_measures(
            {"contract_version": CONTRACT_VERSION, "search_text": "case", "query_text": "SQL"}
        ).error_code
        == "INVALID_REQUEST"
    )


def test_annual_range_all_absent_and_conflicting_selectors():
    service, _ = tools()
    result = service.get_observations(
        query(geography_ids=["01007"], year=None, start_date="2022-01-01", end_date="2023-12-31")
    )
    assert result.status == "ok" and len(result.coverage) == 2
    assert all(item.state == "absent" and item.observation_id is None for item in result.coverage)
    assert result.observations == []
    conflict = service.get_observations(query(start_date="2023-01-01", end_date="2023-12-31"))
    assert conflict.error_code == "INVALID_REQUEST"


def test_nonnumeric_states_and_freshness_are_preserved():
    service, _ = tools()
    result = service.get_observations(query(geography_ids=["01009", "01011"]))
    assert result.status == "ok"
    assert [o.value_state for o in result.observations] == ["SUPPRESSED", "UNAVAILABLE"]
    assert all(o.value is None and o.source_published_at is None for o in result.observations)
    assert all(o.atlas_acquired_at is not None for o in result.observations)


def test_dependency_failure_and_metadata_admission_are_all_or_nothing():
    service, repository = tools()
    accepted = service.get_observations(query(geography_ids=["01001"]))
    assert accepted.status == "ok"
    denied = service.get_evidence_metadata(
        {
            "contract_version": CONTRACT_VERSION,
            "observation_ids": [accepted.observations[0].observation_id, "foreign"],
        }
    )
    assert denied.error_code == "RESOURCE_NOT_FOUND" and denied.metadata == []
    repository.current_release = lambda: (_ for _ in ()).throw(TimeoutError())
    failed = service.get_observations(query(geography_ids=["01001"]))
    assert failed.error_code == "SOURCE_UNAVAILABLE" and failed.observations == []


def test_deadline_expires_request_local_adapter(monkeypatch):
    service, repository = tools()
    original = repository.current_release

    def slow_release():
        time.sleep(0.3)
        return original()

    repository.current_release = slow_release
    monkeypatch.setitem(ask_atlas_tools.TOOL_DEADLINES, "get_observations", 0.01)
    start = time.monotonic()
    result = service.get_observations(query(geography_ids=["01001"]))
    assert result.error_code == "SOURCE_UNAVAILABLE"
    assert time.monotonic() - start < 0.25
    assert (
        service.get_observations(query(geography_ids=["01001"])).error_code == "SOURCE_UNAVAILABLE"
    )


def test_trace_uses_bounded_noncontent_dimensions(monkeypatch):
    attributes = {}

    class Span:
        def set_attribute(self, key, value):
            attributes[key] = value

    class Tracer:
        @contextmanager
        def start_as_current_span(self, name, **kwargs):
            assert name == "atlas.ask_atlas.tool"
            yield Span()

    monkeypatch.setattr(ask_atlas_tools.trace, "get_tracer", lambda name: Tracer())
    service, _ = tools()
    assert service.get_observations(query(geography_ids=["01001"])).status == "ok"
    assert attributes == {
        "atlas.tool.id": "get_observations",
        "atlas.tool.contract_version": CONTRACT_VERSION,
        "atlas.tool.outcome": "ok",
        "atlas.tool.result_count": 1,
    }
