"""Shared pytest fixtures.

The data directory is redirected to a temp folder *before* any app module is
imported, because config resolves paths at import time.
"""

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="jarvis-test-")
os.environ["JARVIS_DATA_DIR"] = _TMP
os.environ["JARVIS_DATABASE_URL"] = f"sqlite:///{Path(_TMP) / 'test.db'}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def project(client):
    body = {
        "name": "Pytest House",
        "plot": {"width": 30, "depth": 50, "unit": "ft", "shape": "rectangular",
                 "road_side": "S", "country": "India", "state": "Maharashtra", "city": "Pune"},
        "requirements": {"floors": 2, "bedrooms": 3, "bathrooms": 3, "attached_baths": 1,
                         "wants_parking": "one_car", "wants_study": True},
    }
    response = client.post("/api/projects", json=body)
    assert response.status_code == 200, response.text
    return response.json()
