from functools import lru_cache
from typing import Annotated

from lyme_gap_atlas_shared.settings import SnowflakeSettings
from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import NoDecode, SettingsConfigDict


class ApiSettings(SnowflakeSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, extra="ignore", populate_by_name=True
    )
    app_name: str = "One Health Lyme Gap Atlas API"
    app_version: str = "0.1.0"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["https://carawaylabs.com", "http://localhost:3000"]
    )
    cache_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    rate_limit_per_minute: int = Field(default=120, ge=10, le=10_000)
    pdf_render_timeout_seconds: float = Field(default=5, gt=0, le=60)
    pdf_max_pages: int = Field(default=50, ge=1, le=1_000)
    pdf_max_report_items: int = Field(default=5_000, ge=1, le=100_000)
    pdf_max_individual_asset_bytes: int = Field(default=5 * 1024 * 1024, ge=1)
    pdf_max_aggregate_asset_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    pdf_max_pdf_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    pdf_cache_enabled: bool = True
    pdf_cache_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    pdf_cache_max_entries: int = Field(default=128, ge=1, le=10_000)
    knowledge_chat_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("KG_CHAT_ENABLED", "KNOWLEDGE_CHAT_ENABLED"),
    )
    conversation_persistence_enabled: bool = True
    # Governed KG procedures live on DEV/PROD databases; Atlas presentation may
    # remain on the legacy ONE_HEALTH_LYME_GAP_ATLAS database. When set, KG
    # Snowflake CALL statements use this database while atlas reads keep
    # snowflake_database.
    snowflake_kg_database: str = ""
    # Public Atlas reads use the approved semantic-release database rather than
    # the connection's default database. Production sets this explicitly.
    snowflake_presentation_database: str = ""
    neo4j_uri: str = ""
    neo4j_runtime_user: str = "graph_runtime"
    neo4j_runtime_password: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    kg_hash_secret: SecretStr | None = None
    kg_chat_model: str = "gpt-5.6-luna"
    supabase_url: str = ""
    supabase_secret_key: SecretStr | None = None
    supabase_jwt_issuer: str = ""
    supabase_jwt_audience: str = "authenticated"

    @property
    def kg_snowflake_database(self) -> str:
        return self.snowflake_kg_database or self.snowflake_database

    @property
    def presentation_database(self) -> str:
        return self.snowflake_presentation_database or self.snowflake_database

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
