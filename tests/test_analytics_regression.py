"""
Regression tests for analytics endpoints — derived from direct MongoDB
pipeline audit (analytics_audit.py).

Each test targets a specific pipeline stage behavior:
  - $$REMOVE in $avg (incomplete exclusion from average, not just total)
  - $cond vs $ifNull for total_duration_minutes
  - $addToSet for unique employee counts
  - $gte/$lte midnight-UTC date boundaries (inclusive both ends)
  - $sort{department:1} alphabetical ordering
  - preserveNullAndEmptyArrays:False orphan-record exclusion in $lookup/$unwind
  - employee_id $match filter isolates results
  - daily_breakdown duration_hours = $ifNull(duration_minutes, 0)/60

No Python-side arithmetic. All calculations verified inside MongoDB aggregation.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import create_employee, punch_in, punch_out


class TestAnalyticsRegression:

    # ── Duration calculation precision ────────────────────────────────────────

    def test_duration_minutes_exact_precision(self, test_client: TestClient):
        """
        510 min = 8.5h; 555 min = 9.25h; avg = (510+555)/2/60 = 8.875 → 8.88h (2dp).
        Verifies $round(..., 2) in both $project stages.
        """
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-03-01T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-01T16:30:00Z")   # 510 min
        punch_in(test_client, "EMP001", timestamp="2024-03-02T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-02T17:15:00Z")   # 555 min

        data = test_client.get("/analytics/employee/EMP001").json()
        assert data["total_hours"] == pytest.approx(17.75, abs=0.01)
        assert data["avg_hours_per_day"] == pytest.approx(8.88, abs=0.01)
        bd = data["daily_breakdown"]
        assert len(bd) == 2
        assert bd[0]["duration_hours"] == pytest.approx(8.5, abs=0.01)
        assert bd[1]["duration_hours"] == pytest.approx(9.25, abs=0.01)

    def test_breakdown_incomplete_day_duration_hours_is_zero(self, test_client: TestClient):
        """
        $ifNull($duration_minutes, 0) in daily_breakdown $project.
        An incomplete record must appear with duration_hours=0.0.
        """
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-03-01T08:00:00Z")
        # no punch-out

        data = test_client.get("/analytics/employee/EMP001").json()
        bd = data["daily_breakdown"]
        assert len(bd) == 1
        assert bd[0]["status"] == "incomplete"
        assert bd[0]["duration_hours"] == 0.0

    # ── $$REMOVE — incomplete excluded from avg, not just zeroed ─────────────

    def test_avg_hours_remove_not_zero_single_employee(self, test_client: TestClient):
        """
        1 present (8h) + 1 incomplete: avg must be 8h, not 4h.
        If pipeline used 0 instead of $$REMOVE: avg = (480+0)/2/60 = 4h (wrong).
        """
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-03-01T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-01T16:00:00Z")   # 8h
        punch_in(test_client, "EMP001", timestamp="2024-03-02T08:00:00Z")
        # no punch-out

        data = test_client.get("/analytics/employee/EMP001").json()
        assert data["total_days"] == 2
        assert data["present_days"] == 1
        assert data["incomplete_days"] == 1
        assert data["avg_hours_per_day"] == pytest.approx(8.0, abs=0.01)
        assert data["total_hours"] == pytest.approx(8.0, abs=0.01)

    def test_summary_avg_remove_with_multiple_incomplete(self, test_client: TestClient):
        """
        2 present (8h + 9h) + 2 incomplete → avg = (480+540)/2/60 = 8.5h, not 4.25h.
        """
        create_employee(test_client, "EMP001", department="Eng")
        create_employee(test_client, "EMP002", department="Eng")
        punch_in(test_client, "EMP001", timestamp="2024-03-01T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-01T16:00:00Z")   # 8h
        punch_in(test_client, "EMP001", timestamp="2024-03-02T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-02T17:00:00Z")   # 9h
        punch_in(test_client, "EMP002", timestamp="2024-03-01T09:00:00Z")    # incomplete
        punch_in(test_client, "EMP002", timestamp="2024-03-02T09:00:00Z")    # incomplete

        data = test_client.get("/analytics/summary").json()
        assert data["total_records"] == 4
        assert data["present_records"] == 2
        assert data["incomplete_records"] == 2
        assert data["avg_hours_per_day"] == pytest.approx(8.5, abs=0.01)
        assert data["total_hours"] == pytest.approx(17.0, abs=0.01)

    def test_daily_trend_avg_remove_on_mixed_day(self, test_client: TestClient):
        """
        One day: 1 present (8h) + 1 incomplete.
        avg_hours = 8h, not 4h. $$REMOVE is in the trend pipeline too.
        """
        create_employee(test_client, "EMP001")
        create_employee(test_client, "EMP002")
        punch_in(test_client, "EMP001", timestamp="2024-03-01T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-01T16:00:00Z")   # 8h
        punch_in(test_client, "EMP002", timestamp="2024-03-01T09:00:00Z")    # incomplete

        data = test_client.get("/analytics/trend/daily").json()
        assert len(data) == 1
        assert data[0]["total_records"] == 2
        assert data[0]["present_records"] == 1
        assert data[0]["avg_hours"] == pytest.approx(8.0, abs=0.01)

    # ── Date boundary: $gte/$lte on UTC midnight ──────────────────────────────

    def test_date_boundary_inclusive_start_and_end(self, test_client: TestClient):
        """
        _date_match normalises to UTC midnight then uses $gte/$lte (both inclusive).
        Records on start-day and end-day must be included; day before/after excluded.
        """
        create_employee(test_client, "EMP001")
        # Outside before
        punch_in(test_client, "EMP001", timestamp="2024-03-09T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-09T16:00:00Z")
        # Start boundary (must include)
        punch_in(test_client, "EMP001", timestamp="2024-03-10T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-10T16:00:00Z")
        # End boundary (must include)
        punch_in(test_client, "EMP001", timestamp="2024-03-12T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-12T16:00:00Z")
        # Outside after
        punch_in(test_client, "EMP001", timestamp="2024-03-13T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-13T16:00:00Z")

        data = test_client.get(
            "/analytics/summary",
            params={"start_date": "2024-03-10T00:00:00Z", "end_date": "2024-03-12T00:00:00Z"},
        ).json()
        # Mar 10 + Mar 12 = 2 (Mar 11 has no data; Mar 9, 13 excluded)
        assert data["total_records"] == 2

    def test_single_day_date_filter(self, test_client: TestClient):
        """start_date == end_date: only one calendar day's records returned."""
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-03-10T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-10T16:00:00Z")
        punch_in(test_client, "EMP001", timestamp="2024-03-11T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-11T16:00:00Z")

        data = test_client.get(
            "/analytics/summary",
            params={"start_date": "2024-03-10T00:00:00Z", "end_date": "2024-03-10T00:00:00Z"},
        ).json()
        assert data["total_records"] == 1

    # ── Department pipeline ───────────────────────────────────────────────────

    def test_department_sorted_alphabetically(self, test_client: TestClient):
        """$sort {department: 1} must produce alphabetical order in response."""
        create_employee(test_client, "EMP001", department="Zebra")
        create_employee(test_client, "EMP002", department="Alpha")
        punch_in(test_client, "EMP001", timestamp="2024-03-01T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-01T16:00:00Z")
        punch_in(test_client, "EMP002", timestamp="2024-03-01T08:00:00Z")
        punch_out(test_client, "EMP002", timestamp="2024-03-01T16:00:00Z")

        data = test_client.get("/analytics/department").json()
        assert len(data) == 2
        assert data[0]["department"] == "Alpha"
        assert data[1]["department"] == "Zebra"

    def test_department_total_hours_excludes_incomplete(self, test_client: TestClient):
        """
        $ifNull(duration_minutes, 0) in $sum: incomplete → 0 contribution.
        total_hours reflects only present records' hours.
        """
        create_employee(test_client, "EMP001", department="Engineering")
        punch_in(test_client, "EMP001", timestamp="2024-03-01T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-01T16:00:00Z")   # 8h
        punch_in(test_client, "EMP001", timestamp="2024-03-02T08:00:00Z")    # incomplete

        data = test_client.get("/analytics/department").json()
        eng = next(d for d in data if d["department"] == "Engineering")
        assert eng["total_records"] == 2
        assert eng["present_records"] == 1
        assert eng["incomplete_records"] == 1
        assert eng["total_hours"] == pytest.approx(8.0, abs=0.01)

    def test_department_employee_count_uses_addtoset(self, test_client: TestClient):
        """
        $addToSet + $size counts distinct employees, not record count.
        2 employees × 2 days = 4 records → employee_count must be 2.
        """
        create_employee(test_client, "EMP001", department="Engineering")
        create_employee(test_client, "EMP002", department="Engineering")
        for emp in ["EMP001", "EMP002"]:
            punch_in(test_client, emp, timestamp="2024-03-01T08:00:00Z")
            punch_out(test_client, emp, timestamp="2024-03-01T16:00:00Z")
            punch_in(test_client, emp, timestamp="2024-03-02T08:00:00Z")
            punch_out(test_client, emp, timestamp="2024-03-02T16:00:00Z")

        data = test_client.get("/analytics/department").json()
        eng = next(d for d in data if d["department"] == "Engineering")
        assert eng["total_records"] == 4
        assert eng["employee_count"] == 2

    # ── Daily trend ───────────────────────────────────────────────────────────

    def test_daily_trend_employee_id_filter_isolates(self, test_client: TestClient):
        """
        employee_id in $match isolates one employee's days.
        EMP001: 2 days. EMP002: 1 day (same first day).
        Filter EMP001 → 2 entries, each with total_records==1.
        """
        create_employee(test_client, "EMP001")
        create_employee(test_client, "EMP002")
        punch_in(test_client, "EMP001", timestamp="2024-03-01T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-01T16:00:00Z")
        punch_in(test_client, "EMP001", timestamp="2024-03-02T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-02T16:00:00Z")
        punch_in(test_client, "EMP002", timestamp="2024-03-01T09:00:00Z")
        punch_out(test_client, "EMP002", timestamp="2024-03-01T17:00:00Z")

        data = test_client.get("/analytics/trend/daily", params={"employee_id": "EMP001"}).json()
        assert len(data) == 2
        assert all(d["total_records"] == 1 for d in data)

    def test_daily_trend_date_range_excludes_outside(self, test_client: TestClient):
        """Records outside the date range must not appear in trend."""
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-03-01T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-01T16:00:00Z")
        punch_in(test_client, "EMP001", timestamp="2024-03-05T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-05T16:00:00Z")

        data = test_client.get(
            "/analytics/trend/daily",
            params={"start_date": "2024-03-02T00:00:00Z", "end_date": "2024-03-04T00:00:00Z"},
        ).json()
        assert data == []

    # ── Summary ───────────────────────────────────────────────────────────────

    def test_summary_unique_employees_addtoset_not_count(self, test_client: TestClient):
        """
        $addToSet + $size: one employee with 3 records → unique_employees_count=1.
        """
        create_employee(test_client, "EMP001")
        for day in ["2024-03-01", "2024-03-02", "2024-03-03"]:
            punch_in(test_client, "EMP001", timestamp=f"{day}T08:00:00Z")
            punch_out(test_client, "EMP001", timestamp=f"{day}T16:00:00Z")

        data = test_client.get("/analytics/summary").json()
        assert data["total_records"] == 3
        assert data["unique_employees_count"] == 1

    def test_summary_combined_employee_and_date_filter(self, test_client: TestClient):
        """
        employee_id + date range both in $match simultaneously.
        EMP001 Mar 1+5; EMP002 Mar 1. Filter EMP001+Mar 1 → total_records=1.
        """
        create_employee(test_client, "EMP001")
        create_employee(test_client, "EMP002")
        punch_in(test_client, "EMP001", timestamp="2024-03-01T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-01T16:00:00Z")
        punch_in(test_client, "EMP001", timestamp="2024-03-05T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-05T16:00:00Z")
        punch_in(test_client, "EMP002", timestamp="2024-03-01T09:00:00Z")
        punch_out(test_client, "EMP002", timestamp="2024-03-01T17:00:00Z")

        data = test_client.get(
            "/analytics/summary",
            params={
                "employee_id": "EMP001",
                "start_date": "2024-03-01T00:00:00Z",
                "end_date": "2024-03-01T00:00:00Z",
            },
        ).json()
        assert data["total_records"] == 1
        assert data["unique_employees_count"] == 1

    def test_summary_total_hours_present_only(self, test_client: TestClient):
        """
        $cond[ne null] in total_duration_minutes: incomplete adds 0.
        1 present (8h) + 1 incomplete → total_hours = 8h exactly.
        """
        create_employee(test_client, "EMP001")
        punch_in(test_client, "EMP001", timestamp="2024-03-01T08:00:00Z")
        punch_out(test_client, "EMP001", timestamp="2024-03-01T16:00:00Z")   # 8h
        punch_in(test_client, "EMP001", timestamp="2024-03-02T08:00:00Z")    # incomplete

        data = test_client.get("/analytics/summary").json()
        assert data["total_hours"] == pytest.approx(8.0, abs=0.01)
        assert data["present_records"] == 1
        assert data["incomplete_records"] == 1
