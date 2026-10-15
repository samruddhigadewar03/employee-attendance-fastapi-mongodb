"""
Tests for analytics endpoints.
All tests run real MongoDB aggregation pipelines against the test database.
Covers: summary, employee analytics, department analytics, daily trend,
empty results, date-range boundaries, incomplete records, and multi-employee.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import create_employee, punch_in, punch_out


def _seed_two_employees(client: TestClient):
    """Helper: create two employees with 2 days of attendance each."""
    create_employee(client, "EMP001", department="Engineering")
    create_employee(client, "EMP002", department="HR")

    # EMP001 — Day 1: 8h, Day 2: 9h
    punch_in(client, "EMP001", timestamp="2024-01-15T08:00:00Z")
    punch_out(client, "EMP001", timestamp="2024-01-15T16:00:00Z")
    punch_in(client, "EMP001", timestamp="2024-01-16T08:00:00Z")
    punch_out(client, "EMP001", timestamp="2024-01-16T17:00:00Z")

    # EMP002 — Day 1: 7h, Day 2: incomplete
    punch_in(client, "EMP002", timestamp="2024-01-15T09:00:00Z")
    punch_out(client, "EMP002", timestamp="2024-01-15T16:00:00Z")
    punch_in(client, "EMP002", timestamp="2024-01-16T09:00:00Z")
    # No punch-out for day 2 → incomplete record


class TestSummaryAnalytics:

    def test_summary_empty(self, test_client: TestClient):
        """Summary with no data returns zero counts, not an error."""
        resp = test_client.get("/analytics/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_records"] == 0
        assert data["present_records"] == 0
        assert data["total_hours"] == 0.0

    def test_summary_with_data(self, test_client: TestClient):
        """Summary counts present and incomplete records correctly."""
        _seed_two_employees(test_client)
        resp = test_client.get("/analytics/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_records"] == 4
        assert data["present_records"] == 3
        assert data["incomplete_records"] == 1
        assert data["unique_employees_count"] == 2
        # Total hours: 8 + 9 + 7 = 24
        assert data["total_hours"] == pytest.approx(24.0, abs=0.1)

    def test_summary_date_range(self, test_client: TestClient):
        """Date-range filter narrows the summary correctly."""
        _seed_two_employees(test_client)
        resp = test_client.get(
            "/analytics/summary",
            params={"start_date": "2024-01-16T00:00:00Z", "end_date": "2024-01-16T00:00:00Z"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_records"] == 2
        assert data["present_records"] == 1  # EMP001 only
        assert data["incomplete_records"] == 1  # EMP002

    def test_summary_single_employee_filter(self, test_client: TestClient):
        """employee_id filter limits summary to that employee."""
        _seed_two_employees(test_client)
        resp = test_client.get("/analytics/summary", params={"employee_id": "EMP001"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_records"] == 2
        assert data["present_records"] == 2
        assert data["unique_employees_count"] == 1


class TestEmployeeAnalytics:

    def test_employee_analytics_correct_totals(self, test_client: TestClient):
        """Per-employee analytics returns correct day and hour counts."""
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-01-15T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-01-15T16:00:00Z")
        punch_in(test_client, "EMP001", timestamp="2024-01-16T09:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-01-16T17:00:00Z")

        resp = test_client.get("/analytics/employee/EMP001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["employee_id"] == "EMP001"
        assert data["total_days"] == 2
        assert data["present_days"] == 2
        assert data["incomplete_days"] == 0
        assert data["total_hours"] == pytest.approx(16.0, abs=0.1)
        assert data["avg_hours_per_day"] == pytest.approx(8.0, abs=0.1)
        assert len(data["daily_breakdown"]) == 2

    def test_employee_analytics_incomplete_excluded_from_avg(self, test_client: TestClient):
        """
        Incomplete records are counted in total_days but not in avg_hours_per_day.
        avg_hours_per_day is computed over present records only.
        """
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-01-15T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-01-15T16:00:00Z")  # 8h
        punch_in(test_client, "EMP001", timestamp="2024-01-16T09:00:00Z")
        # No punch-out: incomplete

        resp = test_client.get("/analytics/employee/EMP001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_days"] == 2
        assert data["present_days"] == 1
        assert data["incomplete_days"] == 1
        assert data["total_hours"] == pytest.approx(8.0, abs=0.1)
        assert data["avg_hours_per_day"] == pytest.approx(8.0, abs=0.1)

    def test_employee_analytics_empty(self, test_client: TestClient):
        """Employee with no attendance returns zeroed response, not an error."""
        create_employee(test_client, "EMP001")
        resp = test_client.get("/analytics/employee/EMP001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_days"] == 0
        assert data["daily_breakdown"] == []

    def test_employee_analytics_date_range(self, test_client: TestClient):
        """Date range restricts the breakdown correctly."""
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-01-10T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-01-10T16:00:00Z")
        punch_in(test_client, "EMP001", timestamp="2024-01-20T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-01-20T16:00:00Z")

        resp = test_client.get(
            "/analytics/employee/EMP001",
            params={"start_date": "2024-01-15T00:00:00Z", "end_date": "2024-01-25T00:00:00Z"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_days"] == 1


class TestDepartmentAnalytics:

    def test_department_analytics(self, test_client: TestClient):
        """Department analytics groups by department correctly."""
        _seed_two_employees(test_client)
        resp = test_client.get("/analytics/department")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

        eng = next((d for d in data if d["department"] == "Engineering"), None)
        hr = next((d for d in data if d["department"] == "HR"), None)

        assert eng is not None
        assert hr is not None
        assert eng["present_records"] == 2
        assert eng["employee_count"] == 1
        assert hr["present_records"] == 1
        assert hr["incomplete_records"] == 1

    def test_department_analytics_empty(self, test_client: TestClient):
        """No data returns empty list, not an error."""
        resp = test_client.get("/analytics/department")
        assert resp.status_code == 200
        assert resp.json() == []


class TestDailyTrend:

    def test_daily_trend_sorted_ascending(self, test_client: TestClient):
        """Daily trend is sorted by date ascending."""
        _seed_two_employees(test_client)
        resp = test_client.get("/analytics/trend/daily")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["date"] == "2024-01-15"
        assert data[1]["date"] == "2024-01-16"

    def test_daily_trend_correct_counts(self, test_client: TestClient):
        """Daily trend counts match expected records per day."""
        _seed_two_employees(test_client)
        resp = test_client.get("/analytics/trend/daily")
        data = resp.json()
        day15 = data[0]
        assert day15["total_records"] == 2
        assert day15["present_records"] == 2

    def test_daily_trend_empty(self, test_client: TestClient):
        """No data returns empty list."""
        resp = test_client.get("/analytics/trend/daily")
        assert resp.status_code == 200
        assert resp.json() == []
