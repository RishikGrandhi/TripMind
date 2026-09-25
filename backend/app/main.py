from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.core.config import get_settings
from app.persistence import get_database

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    database = get_database()
    database.initialize()
    yield
    database.dispose()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Agentic constraint-aware travel planning system",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)
