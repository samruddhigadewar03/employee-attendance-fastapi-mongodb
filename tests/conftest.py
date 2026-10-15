"""
Pytest fixtures for the HROne test suite.

Uses a SEPARATE test database (hrone_test) so production data is never touched.
All collections are wiped before each test function to ensure isolation.

Requirements:
  - A real MongoDB instance at mongodb://localhost:27017 (or MONGODB_URI env var).
  - Tests that use mocked_db are fast unit-style; tests that use test_client
    require a live database.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

# Override the database name BEFORE importing the app so config picks it up.
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ["MONGODB_DATABASE"] = "hrone_test"

from app.main import app  # noqa: E402  (import after env override)
from app.database import get_employees_collection, get_attendance_collection


# ---------------------------------------------------------------------------
# Shared test client
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_client():
    """
    Session-scoped TestClient.
    The lifespan runs once for the session: DB connects on first use.
    """
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# Per-test collection cleanup
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_collections():
    """Wipe the test collections before each test for full isolation."""
    emp_col = get_employees_collection()
    att_col = get_attendance_collection()
    emp_col.delete_many({})
    att_col.delete_many({})
    yield
    # Optionally wipe after too — uncomment if you prefer clean teardown.
    # emp_col.delete_many({})
    # att_col.delete_many({})


# ---------------------------------------------------------------------------
# Reusable helper functions (not fixtures)
# ---------------------------------------------------------------------------

def create_employee(client: TestClient, employee_id: str = "EMP001", **kwargs) -> dict:
    payload = {
        "employee_id": employee_id,
        "name": kwargs.get("name", "Test Employee"),
        "department": kwargs.get("department", "Engineering"),
        "email": kwargs.get("email", f"{employee_id.lower()}@example.com"),
    }
    resp = client.post("/employees", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def punch_in(client: TestClient, employee_id: str = "EMP001", timestamp: str | None = None) -> dict:
    payload: dict = {"employee_id": employee_id}
    if timestamp:
        payload["timestamp"] = timestamp
    resp = client.post("/attendance/punch-in", json=payload)
    return resp


def punch_out(client: TestClient, employee_id: str = "EMP001", timestamp: str | None = None) -> dict:
    payload: dict = {"employee_id": employee_id}
    if timestamp:
        payload["timestamp"] = timestamp
    resp = client.post("/attendance/punch-out", json=payload)
    return resp
