from functools import lru_cache

from lyme_gap_atlas_shared.settings import SnowflakeSettings
from pydantic import Field, field_validator


class ApiSettings(SnowflakeSettings):
    app_name: str = "One Health Lyme Gap Atlas API"
    app_version: str = "0.1.0"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["https://carawaylabs.com", "http://localhost:3000"]
    )
    cache_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    rate_limit_per_minute: int = Field(default=120, ge=10, le=10_000)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
