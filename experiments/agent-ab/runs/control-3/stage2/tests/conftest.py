import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ADMIN_KEY = "test-admin-key"


@pytest.fixture
def make_client():
    def _make(model, **settings_overrides) -> TestClient:
        settings_overrides.setdefault("admin_api_key", ADMIN_KEY)
        return TestClient(create_app(model, settings=Settings(**settings_overrides)))

    return _make
