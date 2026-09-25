from datetime import date
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppMode(StrEnum):
    DEMO = "demo"
    DEVELOPMENT = "development"
    TEST = "test"
    LIVE = "live"


class LLMProviderName(StrEnum):
    FALLBACK = "fallback"
    OLLAMA = "ollama"
    GROQ = "groq"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "TripMind"
    app_mode: AppMode = AppMode.DEMO
    llm_provider: LLMProviderName = LLMProviderName.FALLBACK
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2:3b"
    ollama_timeout_seconds: float = Field(default=8.0, gt=0, le=30)
    groq_api_key: SecretStr | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_timeout_seconds: float = Field(default=12.0, gt=0, le=60)
    demo_reference_date: date = date(2027, 1, 15)
    database_url: str = "sqlite:///./tripmind.db"
    max_replanning_attempts: int = Field(default=3, ge=1, le=10)
    max_tool_calls: int = Field(default=20, ge=1, le=100)
    max_agent_steps: int = Field(default=12, ge=1, le=50)
    external_providers_enabled: bool = False
    live_tool_fallback_to_local: bool = False
    serpapi_api_key: SecretStr | None = None
    staying_api_key: SecretStr | None = None
    geoapify_places_api_key: SecretStr | None = None
    geoapify_routing_api_key: SecretStr | None = None
    openweather_api_key: SecretStr | None = None
    serpapi_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    staying_api_timeout_seconds: float = Field(default=12.0, gt=0, le=60)
    staying_api_max_polls: int = Field(default=6, ge=1, le=30)
    staying_api_poll_interval_seconds: float = Field(default=1.0, ge=0, le=10)
    geoapify_timeout_seconds: float = Field(default=8.0, gt=0, le=60)
    openweather_timeout_seconds: float = Field(default=8.0, gt=0, le=60)
    live_route_cost_per_km: Decimal = Field(default=Decimal("12.00"), gt=0)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @field_validator("ollama_base_url")
    @classmethod
    def local_ollama_url_only(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("OLLAMA_BASE_URL must be a local HTTP URL")
        return normalized

    @field_validator("ollama_model")
    @classmethod
    def normalize_ollama_model(cls, value: str) -> str:
        return value.strip()

    @field_validator("groq_model")
    @classmethod
    def normalize_groq_model(cls, value: str) -> str:
        return value.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
