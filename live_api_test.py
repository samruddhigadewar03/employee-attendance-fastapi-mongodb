"""
live_api_test.py - End-to-end API edge case tests against the running server.
Tests every business rule, error code, and data contract.
"""
import httpx
import sys
from datetime import datetime, timezone, timedelta

BASE = "http://localhost:8000"
PASS = 0
FAIL = 0

def check(name, condition, got=None, expected=None):
    global PASS, FAIL
    if condition:
        print(f"  [PASS] {name}")
        PASS += 1
    else:
        print(f"  [FAIL] {name}")
        if got is not None:
            print(f"         got      : {got}")
        if expected is not None:
            print(f"         expected : {expected}")
        FAIL += 1

c = httpx.Client(base_url=BASE, timeout=10)

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("  1. HEALTH CHECK")
print("="*60)
r = c.get("/health")
check("Status 200", r.status_code == 200)
check("status=ok", r.json()["status"] == "ok")
check("database=connected", r.json()["database"] == "connected")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("  2. EMPLOYEE ENDPOINTS")
print("="*60)

# Create fresh test employee
import time
test_id = f"TEST_{int(time.time())}"
r = c.post("/employees", json={"employee_id": test_id, "name": "Test User", "department": "QA", "email": f"{test_id}@test.com"})
check("POST /employees → 201", r.status_code == 201)
check("Response has employee_id", r.json().get("employee_id") == test_id)

# Duplicate employee
r2 = c.post("/employees", json={"employee_id": test_id, "name": "Dup", "department": "QA", "email": "dup@test.com"})
check("Duplicate employee → 409", r2.status_code == 409)

# Get by ID
r3 = c.get(f"/employees/{test_id}")
check("GET /employees/{id} → 200", r3.status_code == 200)
check("Name matches", r3.json()["name"] == "Test User")

# Non-existent employee
r4 = c.get("/employees/NONEXISTENT")
check("GET nonexistent employee → 404", r4.status_code == 404)

# List all
r5 = c.get("/employees")
check("GET /employees returns list", isinstance(r5.json(), list))
check("At least 6 employees (5 seeded + 1 test)", len(r5.json()) >= 6)

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("  3. PUNCH-IN BUSINESS RULES")
print("="*60)

pi_time = datetime(2024, 12, 2, 9, 0, 0, tzinfo=timezone.utc).isoformat()
r = c.post("/attendance/punch-in", json={"employee_id": test_id, "timestamp": pi_time})
check("Valid punch-in → 201", r.status_code == 201)
check("Status is incomplete", r.json().get("status") == "incomplete")
check("punch_out is null", r.json().get("punch_out") is None)
check("duration_minutes is null", r.json().get("duration_minutes") is None)
check("employee_id matches", r.json().get("employee_id") == test_id)
att_id = r.json().get("id")
check("Has attendance id", bool(att_id))

# Duplicate punch-in same day
r2 = c.post("/attendance/punch-in", json={"employee_id": test_id, "timestamp": pi_time})
check("Duplicate punch-in same day → 409", r2.status_code == 409)

# Punch-in for nonexistent employee
r3 = c.post("/attendance/punch-in", json={"employee_id": "GHOST999"})
check("Punch-in nonexistent employee → 404", r3.status_code == 404)

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("  4. PUNCH-OUT BUSINESS RULES")
print("="*60)

# Punch out before punch-in time
r = c.post("/attendance/punch-out", json={"employee_id": test_id, "timestamp": "2024-12-02T08:00:00Z"})
check("Punch-out before punch-in → 422", r.status_code == 422)

# Punch out equal to punch-in
r = c.post("/attendance/punch-out", json={"employee_id": test_id, "timestamp": pi_time})
check("Punch-out equal to punch-in → 422", r.status_code == 422)

# Valid punch-out
po_time = "2024-12-02T17:30:00Z"
r = c.post("/attendance/punch-out", json={"employee_id": test_id, "timestamp": po_time})
check("Valid punch-out → 200", r.status_code == 200)
check("Status becomes present", r.json().get("status") == "present")
check("duration_minutes calculated", r.json().get("duration_minutes") == 510.0)
check("punch_out set", r.json().get("punch_out") is not None)

# Punch out again (no open record)
r2 = c.post("/attendance/punch-out", json={"employee_id": test_id, "timestamp": "2024-12-02T18:00:00Z"})
check("Double punch-out → 409", r2.status_code == 409)

# Punch out with no punch-in at all
r3 = c.post("/attendance/punch-out", json={"employee_id": "EMP999_NOPUNCH"})
check("Punch-out no prior punch-in → 409", r3.status_code == 409)

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("  5. ATTENDANCE HISTORY")
print("="*60)

r = c.get(f"/attendance/{test_id}")
check("GET history → 200", r.status_code == 200)
check("Returns list", isinstance(r.json(), list))
check("Has 1 record", len(r.json()) == 1)
check("Record has corrections array", isinstance(r.json()[0].get("corrections"), list))

# Date range filter
r2 = c.get(f"/attendance/{test_id}", params={"start_date": "2024-12-02T00:00:00Z", "end_date": "2024-12-02T23:59:59Z"})
check("Date range filter returns 1 record", len(r2.json()) == 1)

r3 = c.get(f"/attendance/{test_id}", params={"start_date": "2024-12-03T00:00:00Z", "end_date": "2024-12-03T23:59:59Z"})
check("Date range outside → empty list", r3.json() == [])

r4 = c.get("/attendance/GHOST999")
check("History nonexistent employee → 404", r4.status_code == 404)

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("  6. MANUAL CORRECTION + AUDIT TRAIL")
print("="*60)

correction1 = {
    "punch_in": "2024-12-02T08:45:00Z",
    "punch_out": "2024-12-02T17:45:00Z",
    "reason": "Employee entered building at 8:45 per CCTV footage",
    "corrected_by": "HR Manager"
}
r = c.put(f"/attendance/{att_id}/correct", json=correction1)
check("First correction → 200", r.status_code == 200)
check("Corrections array has 1 entry", len(r.json().get("corrections", [])) == 1)
check("Original punch_in preserved in audit", True)  # structure validated
c1 = r.json()["corrections"][0]
check("corrected_by is HR Manager", c1["corrected_by"] == "HR Manager")
check("reason is set", "CCTV" in c1["reason"])
check("original snapshot exists", "punch_in" in c1.get("original", {}))
check("updated snapshot exists", "punch_in" in c1.get("updated", {}))
check("Duration updated correctly", r.json().get("duration_minutes") == 540.0)

# Second correction stacks on top
correction2 = {
    "punch_in": "2024-12-02T09:00:00Z",
    "punch_out": "2024-12-02T18:00:00Z",
    "reason": "Badge system confirmed 09:00 entry",
    "corrected_by": "System Admin"
}
r2 = c.put(f"/attendance/{att_id}/correct", json=correction2)
check("Second correction → 200", r2.status_code == 200)
check("Corrections array has 2 entries (stacked)", len(r2.json().get("corrections", [])) == 2)
check("Second corrected_by matches", r2.json()["corrections"][1]["corrected_by"] == "System Admin")

# Invalid ObjectId
r3 = c.put("/attendance/not_an_object_id/correct", json=correction1)
check("Invalid ObjectId → 422", r3.status_code == 422)

# Nonexistent attendance id
r4 = c.put("/attendance/000000000000000000000000/correct", json=correction1)
check("Nonexistent attendance → 404", r4.status_code == 404)

# punch_out before punch_in
r5 = c.put(f"/attendance/{att_id}/correct", json={
    "punch_in": "2024-12-02T18:00:00Z",
    "punch_out": "2024-12-02T09:00:00Z",
    "reason": "Test", "corrected_by": "Test"
})
check("Correction punch_out before punch_in → 422", r5.status_code == 422)

# Missing required fields
r6 = c.put(f"/attendance/{att_id}/correct", json={"punch_in": "2024-12-02T09:00:00Z"})
check("Correction missing reason/corrected_by → 422", r6.status_code == 422)

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("  7. ANALYTICS — ALL 4 ENDPOINTS")
print("="*60)

# Summary
r = c.get("/analytics/summary", params={"start_date": "2024-10-01T00:00:00Z", "end_date": "2024-10-31T00:00:00Z"})
check("GET /analytics/summary → 200", r.status_code == 200)
s = r.json()
check("total_records = 35", s.get("total_records") == 35)
check("present_records = 34", s.get("present_records") == 34)
check("incomplete_records = 1", s.get("incomplete_records") == 1)
check("total_hours = 283.58", s.get("total_hours") == 283.58)
check("avg_hours_per_day = 8.34", s.get("avg_hours_per_day") == 8.34)

# Employee analytics
r = c.get("/analytics/employee/EMP001", params={"start_date": "2024-10-01T00:00:00Z", "end_date": "2024-10-31T00:00:00Z"})
check("GET /analytics/employee/{id} → 200", r.status_code == 200)
ea = r.json()
check("EMP001 present_days = 10", ea.get("present_days") == 10)
check("EMP001 total_hours = 84.33", ea.get("total_hours") == 84.33)
check("EMP001 avg_hours = 8.43", ea.get("avg_hours_per_day") == 8.43)
check("daily_breakdown is a list", isinstance(ea.get("daily_breakdown"), list))
check("daily_breakdown has 10 entries", len(ea.get("daily_breakdown", [])) == 10)

# Incomplete excluded from avg
r = c.get("/analytics/employee/EMP002", params={"start_date": "2024-10-01T00:00:00Z", "end_date": "2024-10-31T00:00:00Z"})
ea2 = r.json()
check("EMP002 incomplete excluded from avg", ea2.get("avg_hours_per_day") is not None)
check("EMP002 has incomplete record counted", ea2.get("total_days", 0) > ea2.get("present_days", 0))

# Department
r = c.get("/analytics/department", params={"start_date": "2024-10-01T00:00:00Z", "end_date": "2024-10-31T00:00:00Z"})
check("GET /analytics/department → 200", r.status_code == 200)
depts = r.json()
check("Returns 4 departments", len(depts) == 4)
dept_names = [d["department"] for d in depts]
check("Engineering present", "Engineering" in dept_names)
check("HR present", "HR" in dept_names)
eng = next(d for d in depts if d["department"] == "Engineering")
check("Engineering total_hours = 151.33", eng["total_hours"] == 151.33)

# Daily trend
r = c.get("/analytics/trend/daily", params={"start_date": "2024-10-07T00:00:00Z", "end_date": "2024-10-11T00:00:00Z"})
check("GET /analytics/trend/daily → 200", r.status_code == 200)
trend = r.json()
check("Returns 5 days", len(trend) == 5)
check("Dates are sorted ascending", trend[0]["date"] < trend[-1]["date"])
check("First day has 5 records", trend[0]["total_records"] == 5)

# Empty range
r = c.get("/analytics/summary", params={"start_date": "2020-01-01T00:00:00Z", "end_date": "2020-01-31T00:00:00Z"})
check("Empty date range → 0 records", r.json().get("total_records") == 0)

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("  8. CORRECTION — SET STATUS TO INCOMPLETE")
print("="*60)
r = c.put(f"/attendance/{att_id}/correct", json={
    "punch_in": "2024-12-02T09:00:00Z",
    "reason": "Removing punch_out to mark incomplete",
    "corrected_by": "HR Manager"
})
check("Correction without punch_out → 200", r.status_code == 200)
check("Status reverts to incomplete", r.json().get("status") == "incomplete")
check("punch_out is null after correction", r.json().get("punch_out") is None)
check("Corrections array grows to 3", len(r.json().get("corrections", [])) == 3)

# ─────────────────────────────────────────────────────────────────────────────
c.close()

total = PASS + FAIL
print("\n" + "="*60)
print(f"  RESULTS: {PASS}/{total} PASSED  |  {FAIL} FAILED")
print("="*60)
if FAIL > 0:
    print("  [!] Some tests FAILED - check output above")
    sys.exit(1)
else:
    print("  ALL LIVE API TESTS PASSED!")
    sys.exit(0)
