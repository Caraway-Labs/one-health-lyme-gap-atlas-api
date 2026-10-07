"""Read the approved, persisted Tier 1 county review projection."""

import json
from datetime import datetime
from math import isfinite
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .config import ApiSettings
from .dependency_telemetry import connect
from .repository import AtlasDataUnavailableError, _sql_identifier


class PriorityReason(BaseModel):
    code: str = Field(min_length=1)
    text: str = Field(min_length=1)


class Tier1CountyPriority(BaseModel):
    """Model-assisted surveillance review priority; never disease or clinical risk."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "county_fips": "09110",
                    "priority_tier": "HIGH",
                    "priority_percentile": 98.56824689786828,
                    "raw_model_score": 2.0739835966635036,
                    "evidence_sufficiency": "SUFFICIENT",
                    "model_version": "tier1-statistical-reference-v1",
                    "prediction_batch_version": "tier1-review-priority-744b2933bae43718",
                    "tier_policy_version": "tier1-review-percentile-v1",
                    "release_id": "governed-2026-09-17-unknown-coverage",
                    "source_commit": "c6bb8a0f48ede70f332513c1b5843f35988949f1",
                    "generated_at_utc": "2026-10-06T05:34:52Z",
                    "reasons": [
                        {"code": "PATHOGEN_PRESENT", "text": "Publisher reports pathogen Present."}
                    ],
                    "limitation_ref": "docs/contracts/tier1-persisted-output-v1.md",
                }
            ]
        }
    )

    county_fips: str = Field(
        pattern=r"^\d{5}$", description="Five-digit county FIPS for this persisted result."
    )
    priority_tier: Literal["HIGH", "MEDIUM", "LOW"] | None = Field(
        description=(
            "HIGH, MEDIUM, or LOW model-assisted surveillance review priority under the "
            "named tier policy; not disease risk. Null for NOT_ESTIMABLE."
        )
    )
    priority_percentile: float | None = Field(
        ge=0,
        le=100,
        description=(
            "Within-batch relative position among scored counties for this model and "
            "inference batch (0-100). Not a probability or comparable across batches, "
            "model versions, populations, or tier policies without a separate validation. "
            "Null for NOT_ESTIMABLE."
        ),
    )
    raw_model_score: float | None = Field(
        description=(
            "Model-native anomaly score; not a probability, incidence estimate, or "
            "clinical/disease risk. Its scale is model-specific and is not comparable "
            "across versions without validation. Null for NOT_ESTIMABLE."
        )
    )
    evidence_sufficiency: Literal["SUFFICIENT", "INSUFFICIENT", "NOT_ESTIMABLE"] = Field(
        description=(
            "Input-data sufficiency state, not calibrated model confidence. NOT_ESTIMABLE "
            "is an abstention with null tier, percentile, and score; INSUFFICIENT may "
            "retain scored fields."
        )
    )
    model_version: str = Field(
        min_length=1, description="Version of the model that generated this result."
    )
    release_id: str = Field(min_length=1, description="Governed source release for this batch.")
    source_commit: str = Field(min_length=1, description="ML source commit for reproducibility.")
    prediction_batch_version: str = Field(
        min_length=1,
        description="Inference batch identity defining the percentile comparison population.",
    )
    tier_policy_version: str = Field(
        min_length=1,
        description="Version of the policy assigning review tiers from scored results.",
    )
    generated_at_utc: datetime = Field(
        description="UTC batch generation time, not the request time or source observation date."
    )
    reasons: list[PriorityReason] = Field(max_length=3)
    limitation_ref: str = Field(min_length=1)

    @field_validator("priority_percentile", "raw_model_score")
    @classmethod
    def finite_number(cls, value: float | None) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("Non-finite model value")
        return value

    @model_validator(mode="after")
    def estimability(self) -> "Tier1CountyPriority":
        scored = (
            self.priority_tier,
            self.priority_percentile,
            self.raw_model_score,
        )
        if self.evidence_sufficiency == "NOT_ESTIMABLE":
            if any(value is not None for value in scored):
                raise ValueError("NOT_ESTIMABLE requires null tier, percentile, and score")
        elif any(value is None for value in scored):
            raise ValueError("Scored evidence requires tier, percentile, and score")
        return self


class Tier1PriorityRepository(Protocol):
    def county(self, fips: str) -> tuple[Any, ...] | None: ...


class SnowflakeTier1PriorityRepository:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings

    @property
    def view(self) -> str:
        return (
            f"{_sql_identifier(self.settings.presentation_database)}."
            f"{_sql_identifier(self.settings.snowflake_presentation_schema)}."
            "CURRENT_TIER1_COUNTY_REVIEW_V"
        )

    def county(self, fips: str) -> tuple[Any, ...] | None:
        try:
            with connect(self.settings) as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNTY_FIPS, PRIORITY_TIER, PRIORITY_PERCENTILE, "
                    "RAW_MODEL_SCORE, EVIDENCE_SUFFICIENCY, MODEL_VERSION, "
                    "PREDICTION_BATCH_VERSION, TIER_POLICY_VERSION, GENERATED_AT_UTC, "
                    "RELEASE_ID, SOURCE_COMMIT, "
                    f"TO_JSON(REASONS), LIMITATION_REF FROM {self.view} "
                    "WHERE COUNTY_FIPS = %s LIMIT 2",
                    (fips,),
                    timeout=self.settings.public_query_timeout_seconds,
                )
                rows = cursor.fetchall()
                if not rows:
                    cursor.execute(
                        f"SELECT 1 FROM {self.view} LIMIT 1",
                        timeout=self.settings.public_query_timeout_seconds,
                    )
                    if cursor.fetchone() is None:
                        raise AtlasDataUnavailableError("No active Tier 1 review batch")
        except AtlasDataUnavailableError:
            raise
        except Exception as exc:
            raise AtlasDataUnavailableError("Tier 1 review projection is unavailable") from exc
        if len(rows) > 1:
            raise AtlasDataUnavailableError("Duplicate current Tier 1 county result")
        return rows[0] if rows else None


class Tier1PriorityService:
    def __init__(self, repository: Tier1PriorityRepository) -> None:
        self.repository = repository

    def county(self, fips: str) -> Tier1CountyPriority | None:
        row = self.repository.county(fips)
        if row is None:
            return None
        try:
            reasons = json.loads(row[11]) if isinstance(row[11], str) else row[11]
            if not isinstance(reasons, list):
                raise ValueError("Reasons must be a list")
            return Tier1CountyPriority(
                county_fips=row[0],
                priority_tier=row[1],
                priority_percentile=row[2],
                raw_model_score=row[3],
                evidence_sufficiency=row[4],
                model_version=row[5],
                release_id=row[9],
                source_commit=row[10],
                prediction_batch_version=row[6],
                tier_policy_version=row[7],
                generated_at_utc=row[8],
                reasons=reasons,
                limitation_ref=row[12],
            )
        except (IndexError, TypeError, ValueError, ValidationError) as exc:
            raise AtlasDataUnavailableError("Malformed current Tier 1 county result") from exc
