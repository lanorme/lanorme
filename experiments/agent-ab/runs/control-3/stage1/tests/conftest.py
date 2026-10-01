import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def make_client():
    def _make(model, **settings_overrides) -> TestClient:
        return TestClient(create_app(model, settings=Settings(**settings_overrides)))

    return _make
