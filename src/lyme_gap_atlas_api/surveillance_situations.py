"""Bounded reads of approved persisted latent-class surveillance situations."""

import json
from datetime import datetime
from math import isclose
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from .config import ApiSettings
from .public_contract import PublicQueryError
from .repository import AtlasDataUnavailableError, _sql_identifier
from .snowflake_read_session import read_connection

MODELS = {
    "four_state": "surveillance-situation-four-state",
    "tick_pathogen": "surveillance-situation-tick-pathogen",
}


class MembershipProbability(BaseModel):
    """The model's membership probability for one situation class."""

    class_id: str = Field(
        min_length=1, description="Identifies the class. It is specific to a model version."
    )
    probability: float = Field(
        ge=0,
        le=1,
        allow_inf_nan=False,
        strict=True,
        description="Probability of membership in the class, from 0 to 1. The four values "
        "sum to 1. It is not a disease risk.",
    )


class SituationClassification(BaseModel):
    """The surveillance situation class assigned to a county, with how it was produced.

    A class describes the pattern of surveillance evidence. It is not a diagnosis and not an
    estimate of an individual's risk.
    """

    model_id: str = Field(min_length=1, description="Identifies the model that assigned the class.")
    model_version: str = Field(min_length=1, description="Version of the model.")
    class_id: str = Field(min_length=1, description="Identifies the assigned class.")
    class_name: str = Field(
        min_length=1, description="Display label of the class, exactly as reviewed."
    )
    class_description: str = Field(
        min_length=1, description="Explanation of the class, exactly as reviewed."
    )
    probabilities: list[MembershipProbability] = Field(
        min_length=4,
        max_length=4,
        description="Membership probability for each of the four classes.",
    )
    limitations: list[str] = Field(
        min_length=1,
        description="Readings of the class that are not supported. They list what not to "
        "conclude, not outcomes.",
    )
    batch_id: str = Field(min_length=1, description="Identifies the batch of results.")
    batch_version: str = Field(min_length=1, description="Version of the batch.")
    county_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="SHA-256 checksum of this county's input record.",
    )
    dictionary_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="SHA-256 checksum of the class dictionary the model used.",
    )
    input_release_id: str = Field(
        min_length=1, description="Identifies the data release the model read."
    )
    input_bundle_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="SHA-256 checksum of the data release the model read.",
    )
    fit_git_sha: str = Field(
        min_length=1, description="Commit of the code that fitted the model, for reproducibility."
    )
    feature_contract: dict[str, Any] = Field(
        description="The input features and settings the model used."
    )
    model_provenance: dict[str, Any] = Field(
        description="Records of how the model was fitted and reviewed."
    )
    source_provenance: dict[str, Any] = Field(
        description="Sources and source vintages the model used."
    )
    handoff_approval: dict[str, Any] = Field(
        description="Record of the review that approved the model output for use."
    )
    data_publication_authorization: dict[str, Any] = Field(
        description="Record of the approval to publish the batch."
    )
    data_authorization_id: str = Field(
        min_length=1, description="Identifies the approval to publish the batch."
    )
    staging_attempt: str = Field(
        min_length=1, description="Identifies the staging run that prepared the batch."
    )
    activated_at: datetime = Field(
        description="When the batch became active. It is not the source vintage or the "
        "request time."
    )

    @model_validator(mode="after")
    def complete_vector(self) -> "SituationClassification":
        expected = {f"{self.model_id}.{self.model_version}.class-{i}" for i in range(4)}
        if {p.class_id for p in self.probabilities} != expected or self.class_id not in expected:
            raise ValueError("Incomplete or mismatched class identities")
        if not isclose(sum(p.probability for p in self.probabilities), 1, abs_tol=1e-6):
            raise ValueError("Membership probabilities must sum to one within 1e-6")
        return self


class SituationVariant(BaseModel):
    """The result of one situation model for a county, or the reason there is none."""

    model_id: str = Field(description="Identifies the situation model.")
    availability: Literal["AVAILABLE", "UNAVAILABLE"] = Field(
        description="Whether a published classification exists. `UNAVAILABLE` is not a finding "
        "about the county."
    )
    unavailable_reason: (
        Literal[
            "DEACTIVATED",
            "NOT_PUBLISHED",
            "UNAPPROVED_BATCH",
            "INCOMPATIBLE_RELEASE",
            "MISSING_ASSIGNMENT",
        ]
        | None
    ) = Field(
        description="Why no classification is published: `NOT_PUBLISHED`, `DEACTIVATED`, "
        "`UNAPPROVED_BATCH`, `INCOMPATIBLE_RELEASE`, or `MISSING_ASSIGNMENT`. `null` when "
        "`availability` is `AVAILABLE`."
    )
    compatibility: Literal["EXACT_INPUT", "ADMITTED_CARRY_FORWARD", "UNAVAILABLE"] = Field(
        description="How the classification relates to the current data release. `EXACT_INPUT` "
        "means the model read the current release. `ADMITTED_CARRY_FORWARD` means a reviewed "
        "result computed from an earlier release is published for the current one. "
        "`UNAVAILABLE` means there is no classification."
    )
    batch_id: str | None = Field(
        description="Identifies the batch of results. `null` when unavailable."
    )
    batch_version: str | None = Field(description="Version of the batch. `null` when unavailable.")
    input_release_id: str | None = Field(
        description="Identifies the data release the model read. `null` when unavailable."
    )
    data: SituationClassification | None = Field(
        description="The classification. `null` when unavailable."
    )


class CountySurveillanceSituations(BaseModel):
    """Two distinct model outputs; Unknown is a valid available classification."""

    county_fips: str = Field(
        pattern=r"^[0-9]{5}$", description="Five-digit county FIPS code, with leading zeros."
    )
    release_id: str = Field(min_length=1, description="Identifies the current data release.")
    bundle_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="SHA-256 checksum of the current data release.",
    )
    four_state: SituationVariant = Field(description="Primary model, including human surveillance.")
    tick_pathogen: SituationVariant = Field(
        description="Secondary model, excluding human surveillance."
    )


class SituationRepository(Protocol):
    def county(self, fips: str) -> CountySurveillanceSituations: ...


def decode(value: Any) -> dict[str, Any]:
    result = json.loads(value) if isinstance(value, str) else value
    if not isinstance(result, dict):
        raise ValueError("Expected persisted object")
    return {key.lower(): item for key, item in result.items()}


def assemble(
    fips: str, release: tuple[str, str], rows: list[tuple[Any, ...]]
) -> CountySurveillanceSituations:
    """Validate stored values without fitting, reclassifying or rescaling."""
    if len(rows) != 2:
        raise ValueError("Expected exactly two model availability records")
    variants: dict[str, SituationVariant] = {}
    for raw_availability, raw_data in rows:
        a = decode(raw_availability)
        if a["county_fips"] != fips or (a["release_id"], a["bundle_sha256"]) != release:
            raise ValueError("Availability identity mismatch")
        model = a["model_id"]
        if model not in MODELS.values() or model in variants:
            raise ValueError("Unexpected or duplicate model")
        data = None
        compatibility = "UNAVAILABLE"
        stored = decode(raw_data)
        if a["availability"] == "AVAILABLE":
            if a["unavailable_reason"] is not None:
                raise ValueError("Available result has an unavailable reason")
            if stored["county_fips"] != fips or stored["model_id"] != model:
                raise ValueError("Serving identity mismatch")
            if (stored["release_id"], stored["bundle_sha256"]) != release:
                raise ValueError("Serving release mismatch")
            for key in ("batch_id", "batch_version", "input_release_id", "class_id"):
                if stored[key] != a[key]:
                    raise ValueError("Serving lineage mismatch")
            data = SituationClassification.model_validate(stored)
            compatibility = (
                "EXACT_INPUT"
                if (data.input_release_id, data.input_bundle_sha256) == release
                else "ADMITTED_CARRY_FORWARD"
            )
        elif (
            a["availability"] != "UNAVAILABLE"
            or a["unavailable_reason"] is None
            or a.get("class_id") is not None
            or stored.get("class_id") is not None
        ):
            raise ValueError("Invalid unavailable result")
        variants[model] = SituationVariant.model_validate(
            {
                **a,
                "compatibility": compatibility,
                "data": data,
            }
        )
    return CountySurveillanceSituations(
        county_fips=fips,
        release_id=release[0],
        bundle_sha256=release[1],
        four_state=variants[MODELS["four_state"]],
        tick_pathogen=variants[MODELS["tick_pathogen"]],
    )


class SnowflakeSituationRepository:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self.schema = (
            f"{_sql_identifier(settings.presentation_database)}."
            f"{_sql_identifier(settings.snowflake_presentation_schema)}"
        )

    def county(self, fips: str) -> CountySurveillanceSituations:
        try:
            with read_connection(self.settings) as connection, connection.cursor() as cursor:

                def read(sql: str, params: tuple[str, ...] = ()) -> list[tuple[Any, ...]]:
                    cursor.execute(sql, params, timeout=self.settings.public_query_timeout_seconds)
                    return list(cursor.fetchall())

                release_sql = (
                    f"SELECT RELEASE_ID,BUNDLE_SHA256 FROM {self.schema}.CURRENT_RELEASE_V LIMIT 2"
                )
                releases = read(release_sql)
                if len(releases) != 1 or not all(releases[0]):
                    raise ValueError("No authoritative release")
                release = releases[0]
                counties = read(
                    f"SELECT RELEASE_ID FROM {self.schema}.CURRENT_COUNTY_ATLAS_V "
                    "WHERE FIPS=%s LIMIT 2",
                    (fips,),
                )
                if counties and counties != [(release[0],)]:
                    raise ValueError("County release mismatch")
                rows = (
                    read(
                        "SELECT TO_JSON(OBJECT_CONSTRUCT_KEEP_NULL(a.*)), "
                        "TO_JSON(OBJECT_INSERT(OBJECT_CONSTRUCT_KEEP_NULL(s.*), 'ACTIVATED_AT', "
                        "TO_CHAR(s.ACTIVATED_AT, 'YYYY-MM-DD\"T\"HH24:MI:SS.FF9TZH:TZM'), TRUE)) "
                        f"FROM {self.schema}.COUNTY_SURVEILLANCE_SITUATION_AVAILABILITY_V a "
                        f"LEFT JOIN {self.schema}.CURRENT_COUNTY_SURVEILLANCE_SITUATIONS_V s "
                        "ON s.COUNTY_FIPS=a.COUNTY_FIPS AND s.MODEL_ID=a.MODEL_ID "
                        "WHERE a.COUNTY_FIPS=%s LIMIT 3",
                        (fips,),
                    )
                    if counties
                    else []
                )
                if read(release_sql) != releases:
                    raise ValueError("Release changed during read")
                if not counties:
                    raise PublicQueryError(
                        "RESOURCE_NOT_FOUND", "County is not in the current geography release."
                    )
                return assemble(fips, (release[0], release[1]), rows)
        except PublicQueryError:
            raise
        except Exception as exc:
            raise AtlasDataUnavailableError("Surveillance situations are unavailable") from exc
