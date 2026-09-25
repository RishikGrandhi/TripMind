from fastapi import APIRouter

from app.core.config import Settings, get_settings

router = APIRouter(tags=["system"])


@router.get("/health")
def health(settings: Settings = get_settings()) -> dict[str, str | bool]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "mode": settings.app_mode.value,
        "llm_provider": settings.llm_provider,
        "external_providers_enabled": settings.external_providers_enabled,
    }

