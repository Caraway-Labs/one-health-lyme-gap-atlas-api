"""API18 typed tools reuse canonical public services without an LLM or HTTP route."""

import json
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from test_public_observations import row

from lyme_gap_atlas_api import ask_atlas_tools
from lyme_gap_atlas_api.ask_atlas_tools import (
    CONTRACT_VERSION,
    StructuredTools,
    _coverage_id,
)
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.public_contract import (
    Indicator,
    Measure,
    Methodology,
    ObservationQuery,
    Source,
)
from lyme_gap_atlas_api.public_metadata import (
    MetadataRows,
    MetadataService,
    SnowflakeMetadataRepository,
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
    def discover(self, *, checkpoint=None):
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
    def source(self, identifier, *, checkpoint=None):
        return Source(
            source_id=identifier,
            label="Human surveillance",
            publisher="CDC",
            lineage_source_id="cdc_lyme",
            dataset_id="x5j9-wybp",
            semantic_version="1.0.0",
            release_version="release-1",
        )

    def methodology(self, identifier, *, checkpoint=None):
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
    result = {
        "tool": "get_observations",
        "measure_id": "case_count_floor_2023",
        "geography_type": "county",
        "geography_ids": ["01001", "01003", "01005", "01007"],
        "year": 2023,
        **updates,
    }
    if result["year"] is None:
        del result["year"]
    return result


def test_observation_parity_coverage_metadata_and_release():
    service, repository = tools()
    found = service.find_measures(
        {"tool": "find_measures", "search_text": "  CASE   COUNT floor ", "page_size": 20}
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
        {"tool": "get_evidence_metadata", "observation_ids": ids}
    )
    assert metadata.status == "ok" and len(metadata.metadata) == 3
    assert metadata.metadata[0].source.publisher == "CDC"
    assert service.get_observations(query()).status == "ok"
    repository.release = "release-2"
    race = service.get_evidence_metadata({"tool": "get_evidence_metadata", "observation_ids": ids})
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
        {"tool": "get_evidence_metadata", "observation_ids": ["foreign"]}
    )
    assert denied.error_code == "RESOURCE_NOT_FOUND"


def test_discovery_bounds_and_schema_no_escape_hatch():
    service, _ = tools()
    assert (
        service.find_measures(
            {"tool": "find_measures", "search_text": "case", "page_size": 1}
        ).status
        == "ok"
    )
    assert (
        service.find_measures(
            {"tool": "find_measures", "search_text": "none", "page_size": 20}
        ).error_code
        == "RESOURCE_NOT_FOUND"
    )
    assert (
        service.find_measures(
            {
                "tool": "find_measures",
                "search_text": "case",
                "indicator_id": "human",
                "page_size": 20,
            }
        ).error_code
        == "INVALID_REQUEST"
    )
    assert (
        service.find_measures(
            {"tool": "find_measures", "search_text": "case", "query_text": "SQL", "page_size": 20}
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
            "tool": "get_evidence_metadata",
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
    calls = []

    def slow_release():
        calls.append("release")
        time.sleep(0.3)
        return original()

    repository.current_release = slow_release
    monkeypatch.setitem(ask_atlas_tools.TOOL_DEADLINES, "get_observations", 0.1)
    start = time.monotonic()
    result = service.get_observations(query(geography_ids=["01001"]))
    assert result.error_code == "SOURCE_UNAVAILABLE"
    assert time.monotonic() - start < 0.25
    assert (
        service.get_observations(query(geography_ids=["01001"])).error_code == "SOURCE_UNAVAILABLE"
    )
    time.sleep(0.35)
    assert calls == ["release"]
    assert service.admitted == {}


def test_final_release_check_uses_remaining_request_deadline():
    service, repository = tools()
    service.pinned_release = repository.release
    service.admitted["fixture"] = object()  # type: ignore[assignment]
    service.started = time.monotonic() - 11.9

    def slow_release():
        time.sleep(0.6)
        return repository.release

    repository.current_release = slow_release
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        service.verify_release()
    assert time.monotonic() - start < 0.35
    assert service.expired
    assert service.admitted == {}
    with pytest.raises(TimeoutError):
        service.verify_release()


def test_timeout_inside_observation_service_stops_follow_on_repository_io(monkeypatch):
    class SlowRepository(Repository):
        calls = 0
        downstream = 0

        def current_release(self):
            self.calls += 1
            if self.calls == 2:
                time.sleep(0.3)
            return self.release

        def measure_exists(self, measure_id, release):
            self.downstream += 1
            return super().measure_exists(measure_id, release)

    repository = SlowRepository()
    service = StructuredTools(Metadata(), ObservationService(repository), Provenance())
    monkeypatch.setitem(ask_atlas_tools.TOOL_DEADLINES, "get_observations", 0.02)
    assert (
        service.get_observations(query(geography_ids=["01001"])).error_code == "SOURCE_UNAVAILABLE"
    )
    time.sleep(0.35)
    assert repository.calls == 2 and repository.downstream == 0
    assert service.admitted == {}


@pytest.mark.parametrize("slow_query", [1, 2])
def test_metadata_expiry_stops_second_query_and_environmental_io(monkeypatch, slow_query):
    statements = []
    environmental = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, *args, **kwargs):
            statements.append(statement)
            if len(statements) == slow_query:
                time.sleep(0.3)

        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(
        "lyme_gap_atlas_api.public_metadata.connect", lambda _settings: Connection()
    )
    monkeypatch.setattr(
        "lyme_gap_atlas_api.public_metadata.EnvironmentalRepository.metadata",
        lambda *args, **kwargs: environmental.append("called"),
    )
    settings = ApiSettings(environmental_context_enabled=True)
    service = StructuredTools(
        MetadataService(SnowflakeMetadataRepository(settings)),
        ObservationService(Repository()),
        Provenance(),
    )
    monkeypatch.setitem(ask_atlas_tools.TOOL_DEADLINES, "find_measures", 0.1)
    result = service.find_measures(
        {"tool": "find_measures", "search_text": "case", "page_size": 20}
    )
    assert result.error_code == "SOURCE_UNAVAILABLE"
    time.sleep(0.35)
    assert len(statements) == slow_query
    assert environmental == []


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


def test_real_metadata_service_governed_county_shape():
    class CanonicalRows:
        def load_metadata(self, *, checkpoint=None):
            return MetadataRows(
                indicators=[("human", "Human", None, None, None, None, "1.0.0", "release-1")],
                measures=[
                    (
                        "case_count_floor_2023",
                        "human",
                        "Case count floor",
                        None,
                        "NUMBER",
                        "cases",
                        None,
                        "COUNTY_FIPS_5",
                        "2023",
                        None,
                        None,
                        None,
                        "unknown",
                        "method",
                        None,
                        "1.0.0",
                        "release-1",
                    )
                ],
            )

    service = StructuredTools(
        MetadataService(CanonicalRows()), ObservationService(Repository()), Provenance()
    )
    result = service.get_observations(query(geography_ids=["01005"]))
    assert result.status == "ok" and result.observations[0].value == 12
    wrong_year = service.get_observations(query(geography_ids=["01005"], year=2022))
    assert wrong_year.error_code == "UNSUPPORTED_FILTER"


def test_accepted_golden_structured_calls_use_normative_schema():
    assert (
        Path(ask_atlas_tools.__file__).with_name("ask-atlas-tools-v1.schema.json").read_bytes()
        == (Path(__file__).parents[1] / "docs" / "ask-atlas-tools-v1.schema.json").read_bytes()
    )
    fixture = json.loads(
        (
            Path(__file__).parents[1] / "tests" / "fixtures" / "ask_atlas_contract_v1.json"
        ).read_text()
    )
    validator = Draft202012Validator(ask_atlas_tools.TOOL_SCHEMA, format_checker=FormatChecker())
    structured = [
        call
        for case in fixture["cases"]
        for call in case.get("calls", [])
        if call["tool"] != "literature_answer"
    ]
    assert len(structured) == 13

    class GoldenRepository(Repository):
        release = "fixture-release-1"

        def measure_exists(self, measure_id, release):
            return measure_id == "case_count_floor_2023" and release == self.release

        def query(self, query, release, offset):
            cases = [
                ("08001", "0", "ZERO", "fixture-zero"),
                ("08013", None, "MISSING", "fixture-missing"),
                ("08059", "4", "OBSERVED", "fixture-observed"),
            ]
            if query.year != 2023:
                return []
            rows = []
            for fips, value, state, identifier in cases:
                if fips in query.geography_id:
                    record = list(row(fips, value, state))
                    record[0] = identifier
                    record[13] = self.release
                    record[14] = "fixture-source"
                    record[27] = "fixture-method"
                    rows.append(tuple(record))
            return rows[offset:][: query.page_size + 1]

    class GoldenMetadata:
        def discover(self, *, checkpoint=None):
            return (
                [
                    Indicator(
                        indicator_id="lyme_cases",
                        label="Lyme cases",
                        definition=None,
                        measure_ids=["case_count_floor_2023"],
                        semantic_version="fixture-semantic-1",
                        release_version="fixture-release-1",
                    )
                ],
                [
                    Measure(
                        measure_id="case_count_floor_2023",
                        indicator_id="lyme_cases",
                        label="2023 Lyme case count floor",
                        definition=None,
                        semantic_version="fixture-semantic-1",
                        release_version="fixture-release-1",
                        unit="cases",
                        geography_semantics="COUNTY_FIPS_5",
                        temporal_semantics="2023",
                    )
                ],
            )

    class GoldenProvenance:
        def source(self, identifier, *, checkpoint=None):
            return Source(
                source_id=identifier,
                label="Fixture source",
                publisher=None,
                lineage_source_id="fixture-lineage",
                dataset_id="fixture-dataset",
                semantic_version="fixture-semantic-1",
                release_version="fixture-release-1",
            )

        def methodology(self, identifier, *, checkpoint=None):
            return Methodology(
                methodology_id=identifier,
                measure_id="case_count_floor_2023",
                version="fixture-method-v1",
                description="Fixture method",
                limitations=[],
                semantic_version="fixture-semantic-1",
                release_version="fixture-release-1",
            )

    for case in fixture["cases"]:
        service = StructuredTools(
            GoldenMetadata(), ObservationService(GoldenRepository()), GoldenProvenance()
        )
        for call in case.get("calls", []):
            if call["tool"] == "literature_answer":
                continue
            assert not list(validator.iter_errors(call)), call
            if call["tool"] == "find_measures":
                result = service.find_measures(call)
            elif call["tool"] == "get_observations":
                result = service.get_observations(call)
            else:
                result = service.get_evidence_metadata(call)
            assert result.status == "ok", (case["id"], call, result.error_code)


def test_malformed_backend_maps_unavailable_and_invalid_fips_never_reads():
    class BrokenMetadata:
        def discover(self, *, checkpoint=None):
            from pydantic import ValidationError

            raise ValidationError.from_exception_data(
                "Measure", [{"type": "missing", "loc": ("label",), "input": {}}]
            )

    repository = Repository()
    service = StructuredTools(BrokenMetadata(), ObservationService(repository), Provenance())
    bad = service.find_measures({"tool": "find_measures", "search_text": "case", "page_size": 20})
    assert bad.error_code == "SOURCE_UNAVAILABLE"
    repository.current_release = lambda: (_ for _ in ()).throw(AssertionError("I/O occurred"))
    malformed = service.get_observations(query(geography_ids=["bad"]))
    assert malformed.error_code == "INVALID_REQUEST"


def test_release_race_preserves_code_with_rows_and_without_rows():
    class RacingRepository(Repository):
        def query(self, query, release, offset):
            rows = super().query(query, release, offset)
            self.release = "release-2"
            return rows

    for year in (2023, 2022):
        repository = RacingRepository()
        service = StructuredTools(Metadata(), ObservationService(repository), Provenance())
        result = service.get_observations(query(geography_ids=["01005"], year=year))
        assert result.error_code == "RELEASE_CHANGED"
        assert result.observations == [] and result.coverage == []


def test_known_measure_year_rejected_before_observation_io():
    class YearMetadata(Metadata):
        def discover(self, *, checkpoint=None):
            indicators, measures = super().discover(checkpoint=checkpoint)
            measures[0].temporal_semantics = "2023"
            return indicators, measures

    class NoObservationIO(Repository):
        def measure_exists(self, measure_id, release):
            raise AssertionError("measure lookup started")

        def query(self, query, release, offset):
            raise AssertionError("observation query started")

    service = StructuredTools(YearMetadata(), ObservationService(NoObservationIO()), Provenance())
    result = service.get_observations(query(geography_ids=["01005"], year=2022))
    assert result.error_code == "UNSUPPORTED_FILTER"


def test_release_change_during_measure_lookup_failure_is_preserved():
    class RacingLookupRepository(Repository):
        def measure_exists(self, measure_id, release):
            self.release = "release-2"
            return False

    service = StructuredTools(
        Metadata(), ObservationService(RacingLookupRepository()), Provenance()
    )
    result = service.get_observations(query(geography_ids=["01005"]))
    assert result.error_code == "RELEASE_CHANGED"
    assert result.observations == [] and result.coverage == []


@pytest.mark.parametrize("tool", ["find_measures", "get_observations"])
@pytest.mark.parametrize("scenario", ["missing", "over_limit"])
@pytest.mark.parametrize("catalogue_release", ["release-1", "release-2"])
def test_catalogue_release_precedes_selection_early_exits(tool, scenario, catalogue_release):
    class CatalogueRows:
        def load_metadata(self, *, checkpoint=None):
            identifiers = (
                ["unrelated"]
                if scenario == "missing"
                else ["case_count_floor_2023", *[f"case_measure_{i}" for i in range(20)]]
            )
            return MetadataRows(
                indicators=[("human", "Human", None, None, None, None, "1.0.0", catalogue_release)],
                measures=[
                    (
                        identifier,
                        "human",
                        "Case count floor",
                        None,
                        "NUMBER",
                        "cases",
                        None,
                        "COUNTY_FIPS_5",
                        "2023",
                        None,
                        None,
                        None,
                        "unknown",
                        "method",
                        None,
                        "1.0.0",
                        catalogue_release,
                    )
                    for identifier in identifiers
                ],
            )

    class OversizedRepository(Repository):
        query_calls = 0

        def query(self, query, release, offset):
            self.query_calls += 1
            return [row("01005", "12", "OBSERVED")] * 201

    repository = OversizedRepository()
    service = StructuredTools(
        MetadataService(CatalogueRows()), ObservationService(repository), Provenance()
    )
    if tool == "find_measures":
        result = service.find_measures(
            {
                "tool": tool,
                "search_text": "missing" if scenario == "missing" else "case",
                "page_size": 20,
            }
        )
    else:
        result = service.get_observations(query(geography_ids=["01005"]))
    expected = (
        "RELEASE_CHANGED"
        if catalogue_release == "release-2"
        else "RESOURCE_NOT_FOUND"
        if scenario == "missing"
        else "QUERY_TOO_BROAD"
    )
    assert result.error_code == expected
    assert result.release_id is None
    assert not result.measures and not result.observations and not result.coverage
    assert repository.query_calls == int(
        catalogue_release == "release-1" and tool == "get_observations" and scenario == "over_limit"
    )
