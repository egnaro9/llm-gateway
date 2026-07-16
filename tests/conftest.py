import pytest
from fastapi.testclient import TestClient

from llmgateway.app import Config, create_app


@pytest.fixture
def app_config():
    # Generous rate limit so normal tests never trip it; the rate-limit test
    # builds its own tight config.
    return Config(api_keys=frozenset({"dev-key"}), rate_capacity=1000)


@pytest.fixture
def client(app_config):
    return TestClient(create_app(app_config))


@pytest.fixture
def auth():
    return {"Authorization": "Bearer dev-key"}
