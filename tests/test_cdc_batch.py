"""Bounded page routing uses projected fixtures, never live acceptance evidence."""

import hashlib
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest
from lyme_gap_atlas_data.cdc_consumer_batch import CDCBatchResultV1
from test_cdc_surveillance import envelope as base_envelope
from test_cdc_surveillance import params, settings

from lyme_gap_atlas_api.cdc_surveillance import IDENTITIES, RELEASE, CDCObservationAdapter
from lyme_gap_atlas_api.public_contract import ObservationQuery
from lyme_gap_atlas_api.public_observations import ObservationService
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError


def envelope(measure, county):
    result = base_envelope(measure, county)
    original = result["semantic"]["observation"]["id"]
    identity = "observation:v1:" + hashlib.sha256(f"{measure}:{county}".encode()).hexdigest()
    return json.loads(json.dumps(result).replace(original, identity))


def batch(measure, counties):
    return CDCBatchResultV1(
        measure, RELEASE, counties, tuple(envelope(measure, county) for county in counties)
    )


def adapter(singleton=None, producer=batch):
    return CDCObservationAdapter(
        settings(), singleton or Mock(side_effect=envelope), batch_producer=producer
    )


@pytest.mark.parametrize(
    "selected,page_size,processed,path",
    [
        (1, 10, 1, "batch"),
        (2, 10, 2, "batch"),
        (5, 10, 5, "batch"),
        (49, 49, 49, "batch"),
        (50, 50, 50, "batch"),
        (51, 50, 51, "singleton"),
        (100, 25, 26, "batch"),
        (100, 50, 51, "singleton"),
        (100, 100, 100, "singleton"),
    ],
)
def test_actual_page_slice_selects_one_batch_or_preserved_singletons(
    selected, page_size, processed, path
):
    counties = [f"{i:05}" for i in range(1, selected + 1)]
    singleton, grouped = Mock(side_effect=envelope), Mock(side_effect=batch)
    result = adapter(singleton, grouped).search(
        ObservationQuery(**params(county=list(reversed(counties)), page_size=page_size)), RELEASE, 0
    )
    assert [item.geography.geography_id for item in result] == counties[:processed]
    assert singleton.call_count == (processed if path == "singleton" else 0)
    assert grouped.call_count == (1 if path == "batch" else 0)
    if path == "batch":
        grouped.assert_called_once_with("scapularis_status", tuple(counties[:processed]))


def test_continuation_recomputes_remaining_slice_and_preserves_order():
    counties = [f"{i:05}" for i in range(1, 101)]
    singleton, grouped = Mock(side_effect=envelope), Mock(side_effect=batch)
    repository = Mock()
    repository.current_release.return_value = RELEASE
    service = ObservationService(repository, cdc_adapter=adapter(singleton, grouped))
    query = ObservationQuery(**params(county=counties, page_size=50))
    first = service.search(query)
    assert singleton.call_count == 51 and grouped.call_count == 0
    second = service.search(query.model_copy(update={"page_token": first.meta.next_page_token}))
    grouped.assert_called_once_with("scapularis_status", tuple(counties[50:]))
    assert second.meta.next_page_token is None
    assert [item.geography.geography_id for item in first.data + second.data] == counties


@pytest.mark.parametrize("page_size", [1, 49, 50])
def test_lookahead_pagination_has_no_omissions_or_duplicates(page_size):
    counties = [f"{i:05}" for i in range(1, 52)]
    repository = Mock()
    repository.current_release.return_value = RELEASE
    service = ObservationService(repository, cdc_adapter=adapter())
    query = ObservationQuery(**params(county=counties, page_size=page_size))
    observed = []
    while True:
        response = service.search(query)
        observed.extend(item.geography.geography_id for item in response.data)
        if response.meta.next_page_token is None:
            break
        query = query.model_copy(update={"page_token": response.meta.next_page_token})
    assert observed == counties


@pytest.mark.parametrize("measure", list(IDENTITIES))
def test_batch_and_singleton_public_dto_serialization_parity(measure):
    counties = ["01001", "13081"] if measure == "scapularis_status" else ["12047", "19135"]
    query = ObservationQuery(**params(measure, counties))
    singleton = CDCObservationAdapter(settings(), envelope)
    expected = singleton.search(query, RELEASE, 0)
    actual = adapter().search(query, RELEASE, 0)
    assert [item.model_dump(mode="json") for item in actual] == [
        item.model_dump(mode="json") for item in expected
    ]


@pytest.mark.parametrize(
    "field",
    [
        "contract",
        "measure",
        "release",
        "missing",
        "unexpected",
        "duplicate",
        "order",
        "observation_order",
    ],
)
def test_malformed_batch_identity_fails_closed_without_singleton_fallback(field):
    counties = ("01001", "13081")
    result = batch("scapularis_status", counties)
    changes = {
        "contract": {"contract_version": "unreviewed"},
        "measure": {"measure_id": "burgdorferi_status"},
        "release": {"release_id": "unreviewed"},
        "missing": {"observations": result.observations[:1]},
        "unexpected": {"county_fips": ("01001", "99999")},
        "duplicate": {"county_fips": ("01001", "01001")},
        "order": {"county_fips": counties[::-1]},
        "observation_order": {"observations": result.observations[::-1]},
    }
    singleton = Mock(side_effect=envelope)
    with pytest.raises(AtlasDataUnavailableError):
        adapter(singleton, lambda *_: replace(result, **changes[field])).search(
            ObservationQuery(**params(county=list(counties))), RELEASE, 0
        )
    singleton.assert_not_called()


@pytest.mark.parametrize("field", ["metadata", "release", "county", "private", "value"])
def test_second_projected_observation_failure_never_returns_partial_page(field):
    def invalid(measure, counties):
        result = batch(measure, counties)
        item = result.observations[1]
        if field == "metadata":
            item["semantic"]["measure"]["metadata_revision"] = 2
        elif field == "release":
            item["semantic"]["release"]["id"] = "unreviewed"
        elif field == "county":
            item["semantic"]["observation"]["geography"]["county_fips"] = "99999"
        elif field == "private":
            item["semantic"]["lineage"]["private_authority"] = "restricted"
        else:
            item["surveillance_evidence"]["value_state"] = "NO_RECORDS"
        return result

    singleton = Mock(side_effect=envelope)
    with pytest.raises(AtlasDataUnavailableError):
        adapter(singleton, invalid).search(
            ObservationQuery(**params(county=["01001", "13081"])), RELEASE, 0
        )
    singleton.assert_not_called()


def test_failed_batch_never_retries_singletons_and_empty_slice_never_calls_producer():
    singleton, grouped = Mock(), Mock(side_effect=PermissionError("private authority"))
    subject = adapter(singleton, grouped)
    query = ObservationQuery(**params(county=["01001"]))
    with pytest.raises(AtlasDataUnavailableError, match="Governed CDC"):
        subject.search(query, RELEASE, 0)
    singleton.assert_not_called()
    grouped.assert_called_once()
    grouped.reset_mock()
    assert subject.search(query, RELEASE, 1) == []
    grouped.assert_not_called()


def test_duplicate_observation_identity_is_rejected():
    def duplicate(measure, counties):
        return CDCBatchResultV1(
            measure,
            RELEASE,
            counties,
            tuple(base_envelope(measure, county) for county in counties),
        )

    singleton = Mock(side_effect=envelope)
    with pytest.raises(AtlasDataUnavailableError):
        adapter(singleton, duplicate).search(
            ObservationQuery(**params(county=["01001", "13081"])), RELEASE, 0
        )
    singleton.assert_not_called()
