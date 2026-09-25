import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.trips import get_planning_session_repository  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.main import app  # noqa: E402
from app.persistence import PlanningSessionRepository, create_database  # noqa: E402


@pytest.fixture
def api_client(tmp_path):
    """Serve API tests with an isolated database instead of the demo history file."""
    database = create_database(
        Settings(database_url=f"sqlite:///{tmp_path / 'api-test.db'}")
    )
    database.initialize()
    repository = PlanningSessionRepository(database.session_factory)
    app.dependency_overrides[get_planning_session_repository] = lambda: repository
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()
        app.dependency_overrides.pop(get_planning_session_repository, None)
        database.dispose()
