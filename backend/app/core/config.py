from datetime import date
from enum import StrEnum
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppMode(StrEnum):
    DEMO = "demo"
    DEVELOPMENT = "development"
    TEST = "test"


class LLMProviderName(StrEnum):
    FALLBACK = "fallback"
    OLLAMA = "ollama"


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
    demo_reference_date: date = date(2027, 1, 15)
    database_url: str = "sqlite:///./tripmind.db"
    max_replanning_attempts: int = Field(default=3, ge=1, le=10)
    max_tool_calls: int = Field(default=20, ge=1, le=100)
    external_providers_enabled: bool = False
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

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
