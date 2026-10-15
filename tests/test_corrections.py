"""
Tests for the manual correction endpoint.
Covers: valid corrections, audit trail integrity, invalid inputs,
missing records, and cascade to status/duration.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import create_employee, punch_in, punch_out


class TestCorrections:

    def _get_attendance_id(self, test_client: TestClient, employee_id: str) -> str:
        resp = test_client.get(f"/attendance/{employee_id}")
        assert resp.status_code == 200
        records = resp.json()
        assert len(records) > 0
        return records[0]["id"]

    def test_valid_correction_preserves_audit(self, test_client: TestClient):
        """
        A valid correction stores original values in corrections[] and
        updates the record fields atomically.
        """
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-01-15T09:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-01-15T17:00:00Z")

        att_id = self._get_attendance_id(test_client, "EMP001")

        payload = {
            "punch_in": "2024-01-15T08:00:00Z",
            "punch_out": "2024-01-15T18:00:00Z",
            "reason": "Employee reported wrong clock",
            "corrected_by": "HR Manager",
        }
        resp = test_client.put(f"/attendance/{att_id}/correct", json=payload)
        assert resp.status_code == 200

        data = resp.json()
        # Updated fields reflect corrected values.
        assert "08:00" in data["punch_in"]
        assert "18:00" in data["punch_out"]
        assert data["duration_minutes"] == pytest.approx(600.0, abs=1.0)
        assert data["status"] == "present"

        # Audit entry is created.
        assert len(data["corrections"]) == 1
        entry = data["corrections"][0]
        assert entry["corrected_by"] == "HR Manager"
        assert entry["reason"] == "Employee reported wrong clock"

        # Original values are preserved.
        assert "09:00" in entry["original"]["punch_in"]
        assert "17:00" in entry["original"]["punch_out"]
        assert entry["original"]["duration_minutes"] == pytest.approx(480.0, abs=1.0)

        # Updated snapshot matches correction request.
        assert "08:00" in entry["updated"]["punch_in"]
        assert "18:00" in entry["updated"]["punch_out"]

    def test_multiple_corrections_stack(self, test_client: TestClient):
        """Each correction appends a new entry; old entries are never removed."""
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-01-15T09:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-01-15T17:00:00Z")
        att_id = self._get_attendance_id(test_client, "EMP001")

        c1 = {
            "punch_in": "2024-01-15T08:30:00Z",
            "punch_out": "2024-01-15T17:30:00Z",
            "reason": "First fix",
            "corrected_by": "Admin1",
        }
        r1 = test_client.put(f"/attendance/{att_id}/correct", json=c1)
        assert r1.status_code == 200

        c2 = {
            "punch_in": "2024-01-15T08:00:00Z",
            "punch_out": "2024-01-15T18:00:00Z",
            "reason": "Second fix",
            "corrected_by": "Admin2",
        }
        r2 = test_client.put(f"/attendance/{att_id}/correct", json=c2)
        assert r2.status_code == 200

        data = r2.json()
        assert len(data["corrections"]) == 2
        assert data["corrections"][0]["corrected_by"] == "Admin1"
        assert data["corrections"][1]["corrected_by"] == "Admin2"

    def test_correction_nonexistent_record(self, test_client: TestClient):
        """Correcting a non-existent record returns 404."""
        fake_id = "000000000000000000000001"
        payload = {
            "punch_in": "2024-01-15T08:00:00Z",
            "reason": "Test",
            "corrected_by": "Admin",
        }
        resp = test_client.put(f"/attendance/{fake_id}/correct", json=payload)
        assert resp.status_code == 404

    def test_correction_invalid_object_id(self, test_client: TestClient):
        """Invalid ObjectId format returns 422."""
        payload = {
            "punch_in": "2024-01-15T08:00:00Z",
            "reason": "Test",
            "corrected_by": "Admin",
        }
        resp = test_client.put("/attendance/not-an-id/correct", json=payload)
        assert resp.status_code == 422

    def test_correction_punch_out_before_punch_in(self, test_client: TestClient):
        """Corrected punch_out before punch_in returns 422."""
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-01-15T09:00:00Z")
        att_id = self._get_attendance_id(test_client, "EMP001")

        payload = {
            "punch_in": "2024-01-15T17:00:00Z",
            "punch_out": "2024-01-15T09:00:00Z",
            "reason": "Bad correction",
            "corrected_by": "Admin",
        }
        resp = test_client.put(f"/attendance/{att_id}/correct", json=payload)
        assert resp.status_code == 422

    def test_correction_missing_required_fields(self, test_client: TestClient):
        """Missing reason or corrected_by returns 422."""
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-01-15T09:00:00Z")
        att_id = self._get_attendance_id(test_client, "EMP001")

        # Missing 'reason'.
        payload = {
            "punch_in": "2024-01-15T08:00:00Z",
            "corrected_by": "Admin",
        }
        resp = test_client.put(f"/attendance/{att_id}/correct", json=payload)
        assert resp.status_code == 422

    def test_correction_sets_incomplete_status_when_no_punch_out(self, test_client: TestClient):
        """
        If correction does not include punch_out, status reverts to incomplete
        and duration_minutes is null.
        """
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-01-15T09:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-01-15T17:00:00Z")
        att_id = self._get_attendance_id(test_client, "EMP001")

        payload = {
            "punch_in": "2024-01-15T08:00:00Z",
            "reason": "Removing punch-out",
            "corrected_by": "Admin",
        }
        resp = test_client.put(f"/attendance/{att_id}/correct", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "incomplete"
        assert data["punch_out"] is None
        assert data["duration_minutes"] is None
