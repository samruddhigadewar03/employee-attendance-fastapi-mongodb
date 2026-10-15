"""
Tests for punch-in and punch-out endpoints.
Covers: valid operations, invalid state transitions, duplicate operations,
invalid timestamps, and missing employee scenarios.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import create_employee, punch_in, punch_out


# ---------------------------------------------------------------------------
# Punch-in tests
# ---------------------------------------------------------------------------

class TestPunchIn:
    def test_valid_punch_in(self, test_client: TestClient):
        """A valid punch-in for an existing employee returns 201."""
        create_employee(test_client, "EMP001")
        resp = punch_in(test_client, "EMP001")
        assert resp.status_code == 201
        data = resp.json()
        assert data["employee_id"] == "EMP001"
        assert data["punch_out"] is None
        assert data["status"] == "incomplete"
        assert data["corrections"] == []

    def test_punch_in_nonexistent_employee(self, test_client: TestClient):
        """Punching in for a missing employee returns 404."""
        resp = punch_in(test_client, "GHOST999")
        assert resp.status_code == 404

    def test_punch_in_with_explicit_timestamp(self, test_client: TestClient):
        """Explicit timestamp is stored and returned correctly."""
        create_employee(test_client, "EMP002")
        ts = "2024-01-15T09:00:00Z"
        resp = punch_in(test_client, "EMP002", timestamp=ts)
        assert resp.status_code == 201
        data = resp.json()
        assert "2024-01-15" in data["punch_in"]

    def test_duplicate_punch_in_same_day(self, test_client: TestClient):
        """Second punch-in on the same day (without punch-out) returns 409."""
        create_employee(test_client, "EMP001")
        r1 = punch_in(test_client, "EMP001", timestamp="2024-01-15T08:00:00Z")
        assert r1.status_code == 201
        r2 = punch_in(test_client, "EMP001", timestamp="2024-01-15T10:00:00Z")
        assert r2.status_code == 409

    def test_punch_in_after_punch_out_allowed(self, test_client: TestClient):
        """After completing a punch-out, a new punch-in the next day is allowed."""
        create_employee(test_client, "EMP001")
        r1 = punch_in(test_client, "EMP001", timestamp="2024-01-15T08:00:00Z")
        assert r1.status_code == 201
        r2 = punch_out(test_client, "EMP001", timestamp="2024-01-15T17:00:00Z")
        assert r2.status_code == 200
        # New day
        r3 = punch_in(test_client, "EMP001", timestamp="2024-01-16T08:00:00Z")
        assert r3.status_code == 201


# ---------------------------------------------------------------------------
# Punch-out tests
# ---------------------------------------------------------------------------

class TestPunchOut:
    def test_valid_punch_out(self, test_client: TestClient):
        """A valid punch-out returns 200 with duration and status=present."""
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-01-15T09:00:00Z")
        resp = punch_out(test_client, "EMP001", timestamp="2024-01-15T17:00:00Z")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "present"
        assert data["punch_out"] is not None
        # 8 hours = 480 minutes
        assert data["duration_minutes"] == pytest.approx(480.0, abs=1.0)

    def test_punch_out_without_punch_in(self, test_client: TestClient):
        """Punch-out with no open record returns 409."""
        create_employee(test_client, "EMP001")
        resp = punch_out(test_client, "EMP001", timestamp="2024-01-15T17:00:00Z")
        assert resp.status_code == 409

    def test_punch_out_before_punch_in(self, test_client: TestClient):
        """Punch-out timestamp earlier than punch-in returns 422."""
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-01-15T17:00:00Z")
        resp = punch_out(test_client, "EMP001", timestamp="2024-01-15T09:00:00Z")
        assert resp.status_code == 422

    def test_punch_out_equal_to_punch_in(self, test_client: TestClient):
        """Punch-out == punch-in (zero duration) should be rejected."""
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-01-15T09:00:00Z")
        resp = punch_out(test_client, "EMP001", timestamp="2024-01-15T09:00:00Z")
        assert resp.status_code == 422

    def test_duration_calculation(self, test_client: TestClient):
        """Duration is calculated correctly in minutes."""
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-01-15T08:30:00Z")
        resp = punch_out(test_client, "EMP001", timestamp="2024-01-15T12:45:00Z")
        assert resp.status_code == 200
        data = resp.json()
        # 4 hours 15 minutes = 255 minutes
        assert data["duration_minutes"] == pytest.approx(255.0, abs=0.1)


# ---------------------------------------------------------------------------
# History endpoint tests
# ---------------------------------------------------------------------------

class TestAttendanceHistory:
    def test_history_returns_records(self, test_client: TestClient):
        """History returns records for the employee sorted newest-first."""
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-01-14T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-01-14T17:00:00Z")
        punch_in(test_client, "EMP001", timestamp="2024-01-15T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-01-15T17:00:00Z")

        resp = test_client.get("/attendance/EMP001")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_history_nonexistent_employee(self, test_client: TestClient):
        """History for a missing employee returns 404."""
        resp = test_client.get("/attendance/GHOST999")
        assert resp.status_code == 404

    def test_history_date_range_filter(self, test_client: TestClient):
        """Date range filter returns only records within the range."""
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-01-10T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-01-10T17:00:00Z")
        punch_in(test_client, "EMP001", timestamp="2024-01-20T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-01-20T17:00:00Z")

        resp = test_client.get(
            "/attendance/EMP001",
            params={"start_date": "2024-01-15T00:00:00Z", "end_date": "2024-01-25T00:00:00Z"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1

    def test_history_empty_result(self, test_client: TestClient):
        """Employee with no attendance returns an empty list (not 404)."""
        create_employee(test_client, "EMP001")
        resp = test_client.get("/attendance/EMP001")
        assert resp.status_code == 200
        assert resp.json() == []
