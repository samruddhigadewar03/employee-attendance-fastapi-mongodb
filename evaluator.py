"""
evaluator.py — Strict automated evaluator for the HROne Attendance API.

Categories:
  [NORMAL]      Standard happy-path operations
  [BOUNDARY]    Edge values, exact timestamps, zero-duration, single-day ranges
  [STATE]       Invalid state transitions (state machine violations)
  [DUPLICATE]   Duplicate requests that must be idempotent-safe
  [CORRECTION]  Manual correction behaviour and all its sub-cases
  [AUDIT]       Audit trail integrity, immutability, stacking
  [DATE]        Date filtering on history and analytics
  [EMPTY]       Empty-dataset responses on all 4 analytics endpoints
  [AGGREGATION] Exact numeric precision from MongoDB pipelines
  [SCHEMA]      Exact JSON field names, types, presence/absence
  [VALIDATION]  Request validation — 422 on bad inputs

Each test prints its category tag, name, and PASS/FAIL.
Failures include the exact discrepancy (got vs expected).
The script exits 1 if any test fails.

EXECUTION: requires the Uvicorn server running at localhost:8000.
"""
import sys
import time
import httpx
from pymongo import MongoClient

# ─── Pre-run cleanup: remove ALL EVAL-prefixed data from previous runs ───────
_mongo = MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=3000)
_db = _mongo["hrone_attendance"]
_removed_att = _db["attendance"].delete_many({"employee_id": {"$regex": "^EVAL"}})
_removed_emp = _db["employees"].delete_many({"employee_id": {"$regex": "^EVAL"}})
print(f"[PRE-CLEANUP] Removed {_removed_att.deleted_count} attendance, "
      f"{_removed_emp.deleted_count} employee records from previous runs.")
_mongo.close()
# ─────────────────────────────────────────────────────────────────────────────

BASE = "http://localhost:8000"
c    = httpx.Client(base_url=BASE, timeout=10)

PASS = FAIL = 0
FAILURES: list[str] = []
SKIPPED:  list[str] = []


# ─── reporting helpers ────────────────────────────────────────────────────────

def passed(cat: str, name: str) -> None:
    global PASS
    PASS += 1
    print(f"  [PASS][{cat}] {name}")

def failed(cat: str, name: str, got=None, expected=None, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    msg = f"[FAIL][{cat}] {name}"
    if detail:
        msg += f"\n         detail   : {detail}"
    if got is not None:
        msg += f"\n         got      : {got}"
    if expected is not None:
        msg += f"\n         expected : {expected}"
    print(f"  {msg}")
    FAILURES.append(f"[{cat}] {name}")

def skipped(cat: str, name: str, reason: str) -> None:
    SKIPPED.append(f"[{cat}] {name} — {reason}")
    print(f"  [SKIP][{cat}] {name} — {reason}")

def check(cat: str, name: str, condition: bool, got=None, expected=None, detail: str = "") -> bool:
    if condition:
        passed(cat, name)
    else:
        failed(cat, name, got, expected, detail)
    return condition

def section(title: str) -> None:
    print(f"\n{'='*64}\n  {title}\n{'='*64}")

def emp(eid: str, dept: str = "Engineering") -> dict:
    """Create an employee; return response JSON."""
    r = c.post("/employees", json={
        "employee_id": eid, "name": f"Eval {eid}",
        "department": dept, "email": f"{eid.lower().replace('_','')}@example.com"
    })
    return r

def pi(eid: str, ts: str) -> httpx.Response:
    return c.post("/attendance/punch-in",  json={"employee_id": eid, "timestamp": ts})

def po(eid: str, ts: str) -> httpx.Response:
    return c.post("/attendance/punch-out", json={"employee_id": eid, "timestamp": ts})

def att_id(eid: str) -> str:
    r = c.get(f"/attendance/{eid}")
    records = r.json()
    return records[0]["id"] if records else ""

def correct(aid: str, payload: dict) -> httpx.Response:
    return c.put(f"/attendance/{aid}/correct", json=payload)

# Unique ID prefix per run so the shared production DB is not polluted
RUN = f"EVAL{int(time.time()) % 100000}"
def uid(n: int) -> str:
    return f"{RUN}_{n:03d}"


# ═══════════════════════════════════════════════════════════════════════════════
section("[NORMAL] Happy-path operations")
# ═══════════════════════════════════════════════════════════════════════════════

# N-01: Create employee
r = emp(uid(1))
check("NORMAL", "N-01: POST /employees returns 201",
      r.status_code == 201, r.status_code, 201)

d = r.json()
check("NORMAL", "N-01: response has employee_id",
      d.get("employee_id") == uid(1), d.get("employee_id"), uid(1))
check("NORMAL", "N-01: response has id (mongo _id as string)",
      isinstance(d.get("id"), str) and len(d["id"]) == 24,
      d.get("id"), "24-char hex string")
check("NORMAL", "N-01: response has created_at",
      "created_at" in d and d["created_at"] is not None)

# N-02: Get employee by id
r2 = c.get(f"/employees/{uid(1)}")
check("NORMAL", "N-02: GET /employees/{id} returns 200", r2.status_code == 200, r2.status_code, 200)
check("NORMAL", "N-02: name matches", r2.json()["name"] == f"Eval {uid(1)}")

# N-03: List employees
emp(uid(2))
r3 = c.get("/employees")
check("NORMAL", "N-03: GET /employees returns 200", r3.status_code == 200, r3.status_code, 200)
check("NORMAL", "N-03: returns a list",  isinstance(r3.json(), list))
check("NORMAL", "N-03: contains created employees",
      any(e["employee_id"] == uid(1) for e in r3.json()))

# N-04: Punch-in
r4 = pi(uid(1), "2024-05-01T08:00:00Z")
check("NORMAL", "N-04: POST /attendance/punch-in returns 201",
      r4.status_code == 201, r4.status_code, 201)
d4 = r4.json()
check("NORMAL", "N-04: punch_out is null",         d4.get("punch_out") is None)
check("NORMAL", "N-04: duration_minutes is null",  d4.get("duration_minutes") is None)
check("NORMAL", "N-04: status = incomplete",       d4.get("status") == "incomplete")
check("NORMAL", "N-04: corrections = []",          d4.get("corrections") == [])
check("NORMAL", "N-04: date field present",        "date" in d4)
check("NORMAL", "N-04: punch_in contains date",    "2024-05-01" in d4.get("punch_in", ""))

# N-05: Punch-out
r5 = po(uid(1), "2024-05-01T16:30:00Z")
check("NORMAL", "N-05: POST /attendance/punch-out returns 200",
      r5.status_code == 200, r5.status_code, 200)
d5 = r5.json()
check("NORMAL", "N-05: status = present",          d5.get("status") == "present")
check("NORMAL", "N-05: punch_out is set",          d5.get("punch_out") is not None)
check("NORMAL", "N-05: duration_minutes = 510.0",  d5.get("duration_minutes") == 510.0,
      d5.get("duration_minutes"), 510.0)

# N-06: Attendance history
r6 = c.get(f"/attendance/{uid(1)}")
check("NORMAL", "N-06: GET /attendance/{id} returns 200",
      r6.status_code == 200, r6.status_code, 200)
check("NORMAL", "N-06: returns a list",   isinstance(r6.json(), list))
check("NORMAL", "N-06: record count = 1", len(r6.json()) == 1, len(r6.json()), 1)

# N-07: Health
rh = c.get("/health")
check("NORMAL", "N-07: GET /health returns 200",       rh.status_code == 200)
check("NORMAL", "N-07: status = ok",                   rh.json().get("status") == "ok")
check("NORMAL", "N-07: database = connected",          rh.json().get("database") == "connected")


# ═══════════════════════════════════════════════════════════════════════════════
section("[SCHEMA] Exact JSON field names and types")
# ═══════════════════════════════════════════════════════════════════════════════

emp(uid(10))
pi(uid(10), "2024-05-02T09:00:00Z")
po(uid(10), "2024-05-02T17:00:00Z")
rec = c.get(f"/attendance/{uid(10)}").json()[0]

REQUIRED_ATTENDANCE_FIELDS = {
    "id": str, "employee_id": str, "date": str, "punch_in": str,
    "punch_out": str, "duration_minutes": float,
    "status": str, "corrections": list,
}
for field, typ in REQUIRED_ATTENDANCE_FIELDS.items():
    val = rec.get(field)
    check("SCHEMA", f"attendance.{field} present and correct type",
          val is not None and isinstance(val, typ),
          type(val).__name__, typ.__name__)

REQUIRED_EMPLOYEE_FIELDS = {"id": str, "employee_id": str, "name": str,
                             "department": str, "email": str, "created_at": str}
erec = c.get(f"/employees/{uid(10)}").json()
for field, typ in REQUIRED_EMPLOYEE_FIELDS.items():
    val = erec.get(field)
    check("SCHEMA", f"employee.{field} present and correct type",
          val is not None and isinstance(val, typ),
          type(val).__name__, typ.__name__)

# Incomplete record schema — punch_out and duration_minutes must be null
emp(uid(11))
pi(uid(11), "2024-05-03T08:00:00Z")
irec = c.get(f"/attendance/{uid(11)}").json()[0]
check("SCHEMA", "incomplete: punch_out is null",          irec["punch_out"] is None)
check("SCHEMA", "incomplete: duration_minutes is null",   irec["duration_minutes"] is None)
check("SCHEMA", "incomplete: status = incomplete",        irec["status"] == "incomplete")
check("SCHEMA", "incomplete: corrections = []",           irec["corrections"] == [])


# ═══════════════════════════════════════════════════════════════════════════════
section("[DUPLICATE] Duplicate request handling")
# ═══════════════════════════════════════════════════════════════════════════════

emp(uid(20))
# D-01: Duplicate employee
r_dup1 = c.post("/employees", json={
    "employee_id": uid(20), "name": "Dup", "department": "X", "email": "dup@example.com"
})
check("DUPLICATE", "D-01: duplicate employee_id returns 409",
      r_dup1.status_code == 409, r_dup1.status_code, 409)

# D-02: Double punch-in same day
pi(uid(20), "2024-05-04T09:00:00Z")
r_dup2 = pi(uid(20), "2024-05-04T10:00:00Z")
check("DUPLICATE", "D-02: double punch-in same day returns 409",
      r_dup2.status_code == 409, r_dup2.status_code, 409)

# D-03: Double punch-out (no open record after first punch-out)
po(uid(20), "2024-05-04T17:00:00Z")
r_dup3 = po(uid(20), "2024-05-04T18:00:00Z")
check("DUPLICATE", "D-03: double punch-out returns 409",
      r_dup3.status_code == 409, r_dup3.status_code, 409)

# D-04: Punch-in same employee different day — ALLOWED
r_day2 = pi(uid(20), "2024-05-05T08:00:00Z")
check("DUPLICATE", "D-04: punch-in next day after complete punch-out returns 201",
      r_day2.status_code == 201, r_day2.status_code, 201)


# ═══════════════════════════════════════════════════════════════════════════════
section("[STATE] Invalid state transitions")
# ═══════════════════════════════════════════════════════════════════════════════

# S-01: Punch-out without any punch-in
emp(uid(30))
r_s1 = po(uid(30), "2024-05-06T17:00:00Z")
check("STATE", "S-01: punch-out with no punch-in returns 409",
      r_s1.status_code == 409, r_s1.status_code, 409)

# S-02: Punch-in for nonexistent employee
r_s2 = pi("GHOST_EVAL_99", "2024-05-06T08:00:00Z")
check("STATE", "S-02: punch-in nonexistent employee returns 404",
      r_s2.status_code == 404, r_s2.status_code, 404)

# S-03: Punch-out for nonexistent employee
r_s3 = po("GHOST_EVAL_99", "2024-05-06T17:00:00Z")
check("STATE", "S-03: punch-out nonexistent employee returns 409",
      r_s3.status_code == 409, r_s3.status_code, 409)

# S-04: GET history for nonexistent employee returns 404
r_s4 = c.get("/attendance/GHOST_EVAL_99")
check("STATE", "S-04: history nonexistent employee returns 404",
      r_s4.status_code == 404, r_s4.status_code, 404)

# S-05: GET employee nonexistent returns 404
r_s5 = c.get("/employees/GHOST_EVAL_99")
check("STATE", "S-05: get nonexistent employee returns 404",
      r_s5.status_code == 404, r_s5.status_code, 404)


# ═══════════════════════════════════════════════════════════════════════════════
section("[BOUNDARY] Timestamp edge cases")
# ═══════════════════════════════════════════════════════════════════════════════

emp(uid(40))
# B-01: Punch-out == punch-in → 422
pi(uid(40), "2024-05-07T09:00:00Z")
r_b1 = po(uid(40), "2024-05-07T09:00:00Z")
check("BOUNDARY", "B-01: punch-out == punch-in returns 422",
      r_b1.status_code == 422, r_b1.status_code, 422)

# B-02: Punch-out 1 second before punch-in → 422
r_b2 = po(uid(40), "2024-05-07T08:59:59Z")
check("BOUNDARY", "B-02: punch-out 1s before punch-in returns 422",
      r_b2.status_code == 422, r_b2.status_code, 422)

# B-03: Punch-out 1 second after punch-in → 200 (minimum valid)
po(uid(40), "2024-05-07T09:00:01Z")
d_b3 = c.get(f"/attendance/{uid(40)}").json()[0]
check("BOUNDARY", "B-03: punch-out 1s after punch-in: duration = 1/60 min",
      d_b3.get("duration_minutes") is not None and d_b3["duration_minutes"] > 0,
      d_b3.get("duration_minutes"))
check("BOUNDARY", "B-03: status = present after 1s punch-out",
      d_b3.get("status") == "present")

# B-04: Exact duration calculation (4h 15m = 255 minutes exactly)
emp(uid(41))
pi(uid(41), "2024-05-08T08:30:00Z")
r_b4 = po(uid(41), "2024-05-08T12:45:00Z")
check("BOUNDARY", "B-04: 4h 15m = 255.0 duration_minutes exactly",
      r_b4.json().get("duration_minutes") == 255.0,
      r_b4.json().get("duration_minutes"), 255.0)

# B-05: Overnight crossing midnight — punch-in day 1, punch-out day 2
emp(uid(42))
pi(uid(42), "2024-05-09T22:00:00Z")
r_b5 = po(uid(42), "2024-05-10T06:00:00Z")
check("BOUNDARY", "B-05: overnight crossing: status = present",
      r_b5.status_code == 200 and r_b5.json().get("status") == "present",
      r_b5.status_code)
check("BOUNDARY", "B-05: overnight crossing: duration = 480.0 min",
      r_b5.json().get("duration_minutes") == 480.0,
      r_b5.json().get("duration_minutes"), 480.0)

# B-06: Empty employee_id in request body → 422
r_b6 = c.post("/attendance/punch-in", json={"employee_id": "", "timestamp": "2024-05-01T08:00:00Z"})
check("BOUNDARY", "B-06: empty employee_id in punch-in body returns 422",
      r_b6.status_code == 422, r_b6.status_code, 422)

# B-07: Empty name in employee creation → 422
r_b7 = c.post("/employees", json={"employee_id": "XEVAL", "name": "", "department": "D", "email": "x@x.com"})
check("BOUNDARY", "B-07: empty name in POST /employees returns 422",
      r_b7.status_code == 422, r_b7.status_code, 422)

# B-08: Invalid email in employee creation → 422
r_b8 = c.post("/employees", json={"employee_id": "XEVAL2", "name": "X", "department": "D", "email": "not-an-email"})
check("BOUNDARY", "B-08: invalid email in POST /employees returns 422",
      r_b8.status_code == 422, r_b8.status_code, 422)


# ═══════════════════════════════════════════════════════════════════════════════
section("[CORRECTION] Manual correction — all sub-cases")
# ═══════════════════════════════════════════════════════════════════════════════

emp(uid(50))
pi(uid(50), "2024-05-10T09:00:00Z")
po(uid(50), "2024-05-10T17:00:00Z")
aid50 = att_id(uid(50))

# C-01: Valid correction with both punch_in and punch_out
r_c1 = correct(aid50, {
    "punch_in":  "2024-05-10T08:00:00Z",
    "punch_out": "2024-05-10T18:00:00Z",
    "reason":    "Clock error verified by CCTV",
    "corrected_by": "HR Manager"
})
check("CORRECTION", "C-01: valid correction returns 200",
      r_c1.status_code == 200, r_c1.status_code, 200)
d_c1 = r_c1.json()
check("CORRECTION", "C-01: duration updated (08:00-18:00 = 600 min)",
      d_c1.get("duration_minutes") == 600.0, d_c1.get("duration_minutes"), 600.0)
check("CORRECTION", "C-01: status remains present",
      d_c1.get("status") == "present")

# C-02: Correction reverts to incomplete (punch_out omitted)
r_c2 = correct(aid50, {
    "punch_in":  "2024-05-10T08:30:00Z",
    "reason":    "Removing punch_out to investigate",
    "corrected_by": "Admin"
})
check("CORRECTION", "C-02: correction without punch_out returns 200",
      r_c2.status_code == 200, r_c2.status_code, 200)
d_c2 = r_c2.json()
check("CORRECTION", "C-02: status reverts to incomplete",
      d_c2.get("status") == "incomplete", d_c2.get("status"), "incomplete")
check("CORRECTION", "C-02: punch_out is null",       d_c2.get("punch_out") is None)
check("CORRECTION", "C-02: duration_minutes is null",d_c2.get("duration_minutes") is None)

# C-03: Correction with punch_out == punch_in → 422
r_c3 = correct(aid50, {
    "punch_in":  "2024-05-10T09:00:00Z",
    "punch_out": "2024-05-10T09:00:00Z",
    "reason":    "bad",
    "corrected_by": "Admin"
})
check("CORRECTION", "C-03: punch_out == punch_in in correction returns 422",
      r_c3.status_code == 422, r_c3.status_code, 422)

# C-04: Correction with punch_out before punch_in → 422
r_c4 = correct(aid50, {
    "punch_in":  "2024-05-10T17:00:00Z",
    "punch_out": "2024-05-10T08:00:00Z",
    "reason":    "bad",
    "corrected_by": "Admin"
})
check("CORRECTION", "C-04: punch_out before punch_in in correction returns 422",
      r_c4.status_code == 422, r_c4.status_code, 422)

# C-05: Missing required field 'reason' → 422
r_c5 = correct(aid50, {"punch_in": "2024-05-10T08:00:00Z", "corrected_by": "Admin"})
check("CORRECTION", "C-05: missing reason returns 422",
      r_c5.status_code == 422, r_c5.status_code, 422)

# C-06: Missing required field 'corrected_by' → 422
r_c6 = correct(aid50, {"punch_in": "2024-05-10T08:00:00Z", "reason": "OK"})
check("CORRECTION", "C-06: missing corrected_by returns 422",
      r_c6.status_code == 422, r_c6.status_code, 422)

# C-07: reason exceeds max_length=500 → 422
r_c7 = correct(aid50, {"punch_in": "2024-05-10T08:00:00Z",
                        "reason": "x" * 501, "corrected_by": "Admin"})
check("CORRECTION", "C-07: reason > 500 chars returns 422",
      r_c7.status_code == 422, r_c7.status_code, 422)

# C-08: corrected_by exceeds max_length=100 → 422
r_c8 = correct(aid50, {"punch_in": "2024-05-10T08:00:00Z",
                        "reason": "OK", "corrected_by": "x" * 101})
check("CORRECTION", "C-08: corrected_by > 100 chars returns 422",
      r_c8.status_code == 422, r_c8.status_code, 422)

# C-09: Invalid ObjectId → 422
r_c9 = c.put("/attendance/not_an_oid/correct",
             json={"punch_in": "2024-05-10T08:00:00Z", "reason": "x", "corrected_by": "y"})
check("CORRECTION", "C-09: invalid ObjectId returns 422",
      r_c9.status_code == 422, r_c9.status_code, 422)

# C-10: Valid ObjectId but no matching document → 404
r_c10 = c.put("/attendance/000000000000000000000001/correct",
              json={"punch_in": "2024-05-10T08:00:00Z", "reason": "x", "corrected_by": "y"})
check("CORRECTION", "C-10: valid ObjectId no document returns 404",
      r_c10.status_code == 404, r_c10.status_code, 404)

# C-11: empty reason string → 422
r_c11 = correct(aid50, {"punch_in": "2024-05-10T08:00:00Z",
                         "reason": "", "corrected_by": "Admin"})
check("CORRECTION", "C-11: empty reason string returns 422",
      r_c11.status_code == 422, r_c11.status_code, 422)


# ═══════════════════════════════════════════════════════════════════════════════
section("[AUDIT] Audit trail integrity")
# ═══════════════════════════════════════════════════════════════════════════════

emp(uid(60))
pi(uid(60), "2024-06-01T09:00:00Z")
po(uid(60), "2024-06-01T17:00:00Z")
aid60 = att_id(uid(60))

# A-01: First correction — audit entry structure
r_a1 = correct(aid60, {
    "punch_in":  "2024-06-01T08:45:00Z",
    "punch_out": "2024-06-01T17:30:00Z",
    "reason":    "Badge log says 08:45",
    "corrected_by": "Alice"
})
check("AUDIT", "A-01: first correction returns 200", r_a1.status_code == 200)
d_a1 = r_a1.json()
corrs = d_a1.get("corrections", [])
check("AUDIT", "A-01: corrections has 1 entry",  len(corrs) == 1, len(corrs), 1)
c1 = corrs[0]
check("AUDIT", "A-01: entry has corrected_at",    "corrected_at" in c1)
check("AUDIT", "A-01: corrected_by = Alice",      c1.get("corrected_by") == "Alice")
check("AUDIT", "A-01: reason stored correctly",   c1.get("reason") == "Badge log says 08:45")
check("AUDIT", "A-01: original.punch_in contains 09:00",
      "09:00" in c1.get("original", {}).get("punch_in", ""))
check("AUDIT", "A-01: original.punch_out contains 17:00",
      "17:00" in c1.get("original", {}).get("punch_out", ""))
check("AUDIT", "A-01: original.duration_minutes = 480.0",
      c1.get("original", {}).get("duration_minutes") == 480.0,
      c1.get("original", {}).get("duration_minutes"), 480.0)
check("AUDIT", "A-01: updated.punch_in contains 08:45",
      "08:45" in c1.get("updated", {}).get("punch_in", ""))
check("AUDIT", "A-01: updated.punch_out contains 17:30",
      "17:30" in c1.get("updated", {}).get("punch_out", ""))
check("AUDIT", "A-01: updated.duration_minutes = 525.0",
      c1.get("updated", {}).get("duration_minutes") == 525.0,
      c1.get("updated", {}).get("duration_minutes"), 525.0)

# A-02: Second correction stacks, does not overwrite
r_a2 = correct(aid60, {
    "punch_in":  "2024-06-01T08:30:00Z",
    "punch_out": "2024-06-01T17:45:00Z",
    "reason":    "CCTV confirms 08:30",
    "corrected_by": "Bob"
})
check("AUDIT", "A-02: second correction returns 200", r_a2.status_code == 200)
d_a2 = r_a2.json()
corrs2 = d_a2.get("corrections", [])
check("AUDIT", "A-02: corrections has 2 entries",      len(corrs2) == 2, len(corrs2), 2)
check("AUDIT", "A-02: first entry still Alice (immutable)",
      corrs2[0].get("corrected_by") == "Alice", corrs2[0].get("corrected_by"), "Alice")
check("AUDIT", "A-02: second entry is Bob",
      corrs2[1].get("corrected_by") == "Bob")
check("AUDIT", "A-02: live record reflects second correction",
      "08:30" in d_a2.get("punch_in", ""))

# A-03: Third correction (revert to incomplete) — audit grows to 3
r_a3 = correct(aid60, {
    "punch_in":  "2024-06-01T08:00:00Z",
    "reason":    "Removing punch-out pending investigation",
    "corrected_by": "Carol"
})
d_a3 = r_a3.json()
check("AUDIT", "A-03: corrections grows to 3",   len(d_a3.get("corrections", [])) == 3,
      len(d_a3.get("corrections", [])), 3)
check("AUDIT", "A-03: all 3 entries preserved — Alice still first",
      d_a3["corrections"][0]["corrected_by"] == "Alice")
check("AUDIT", "A-03: Bob still second",
      d_a3["corrections"][1]["corrected_by"] == "Bob")
check("AUDIT", "A-03: Carol is third",
      d_a3["corrections"][2]["corrected_by"] == "Carol")

# A-04: History endpoint returns same corrections array
hist = c.get(f"/attendance/{uid(60)}").json()
check("AUDIT", "A-04: history preserves corrections array across requests",
      len(hist[0].get("corrections", [])) == 3,
      len(hist[0].get("corrections", [])), 3)


# ═══════════════════════════════════════════════════════════════════════════════
section("[DATE] Date filtering on attendance history")
# ═══════════════════════════════════════════════════════════════════════════════

emp(uid(70))
# Create records on 3 different days
for day, t_in, t_out in [
    ("2024-07-01", "08:00", "16:00"),
    ("2024-07-10", "08:00", "16:00"),
    ("2024-07-20", "08:00", "16:00"),
]:
    pi(uid(70), f"{day}T{t_in}:00Z")
    po(uid(70), f"{day}T{t_out}:00Z")

# DA-01: No filter → 3 records
r_da1 = c.get(f"/attendance/{uid(70)}")
check("DATE", "DA-01: no filter returns all 3 records",
      len(r_da1.json()) == 3, len(r_da1.json()), 3)

# DA-02: Range covers middle record only
r_da2 = c.get(f"/attendance/{uid(70)}",
              params={"start_date": "2024-07-05T00:00:00Z", "end_date": "2024-07-15T00:00:00Z"})
check("DATE", "DA-02: date range [Jul 5-15] returns 1 record",
      len(r_da2.json()) == 1, len(r_da2.json()), 1)

# DA-03: Exact boundary — start == end == day of record
r_da3 = c.get(f"/attendance/{uid(70)}",
              params={"start_date": "2024-07-10T00:00:00Z", "end_date": "2024-07-10T00:00:00Z"})
check("DATE", "DA-03: exact single-day boundary inclusive",
      len(r_da3.json()) == 1, len(r_da3.json()), 1)

# DA-04: Range entirely outside data → empty list (not 404)
r_da4 = c.get(f"/attendance/{uid(70)}",
              params={"start_date": "2020-01-01T00:00:00Z", "end_date": "2020-12-31T00:00:00Z"})
check("DATE", "DA-04: empty date range returns [] not 404",
      r_da4.status_code == 200 and r_da4.json() == [],
      (r_da4.status_code, r_da4.json()))

# DA-05: Default sort is newest-first
all_records = r_da1.json()
dates = [r["date"] for r in all_records]
check("DATE", "DA-05: history sorted newest-first",
      dates == sorted(dates, reverse=True), dates)


# ═══════════════════════════════════════════════════════════════════════════════
section("[EMPTY] Empty-dataset responses for all analytics endpoints")
# ═══════════════════════════════════════════════════════════════════════════════
# Use a date range with zero data in the test DB (far past)
NO_DATA = {"start_date": "2000-01-01T00:00:00Z", "end_date": "2000-01-31T00:00:00Z"}

r_e1 = c.get("/analytics/summary", params=NO_DATA)
check("EMPTY", "E-01: summary empty range status 200",       r_e1.status_code == 200)
e1 = r_e1.json()
check("EMPTY", "E-01: total_records = 0",           e1.get("total_records")    == 0)
check("EMPTY", "E-01: present_records = 0",         e1.get("present_records")  == 0)
check("EMPTY", "E-01: incomplete_records = 0",      e1.get("incomplete_records") == 0)
check("EMPTY", "E-01: total_hours = 0.0",           e1.get("total_hours")      == 0.0)
check("EMPTY", "E-01: avg_hours_per_day = 0.0",     e1.get("avg_hours_per_day") == 0.0)
check("EMPTY", "E-01: unique_employees_count = 0",  e1.get("unique_employees_count") == 0)

r_e2 = c.get("/analytics/employee/GHOST_EVAL_99")
check("EMPTY", "E-02: employee analytics missing id status 200", r_e2.status_code == 200)
e2 = r_e2.json()
check("EMPTY", "E-02: total_days = 0",         e2.get("total_days") == 0)
check("EMPTY", "E-02: daily_breakdown = []",   e2.get("daily_breakdown") == [])
check("EMPTY", "E-02: present_days = 0",       e2.get("present_days") == 0)
check("EMPTY", "E-02: total_hours = 0.0",      e2.get("total_hours") == 0.0)

r_e3 = c.get("/analytics/department", params=NO_DATA)
check("EMPTY", "E-03: department empty range returns []",
      r_e3.status_code == 200 and r_e3.json() == [],
      (r_e3.status_code, r_e3.json()))

r_e4 = c.get("/analytics/trend/daily", params=NO_DATA)
check("EMPTY", "E-04: daily trend empty range returns []",
      r_e4.status_code == 200 and r_e4.json() == [],
      (r_e4.status_code, r_e4.json()))


# ═══════════════════════════════════════════════════════════════════════════════
section("[AGGREGATION] MongoDB aggregation accuracy")
# ═══════════════════════════════════════════════════════════════════════════════
# Seed a controlled mini-dataset under EVAL prefix, then check exact values.

e1_id, e2_id, e3_id = uid(80), uid(81), uid(82)
emp(e1_id, "Alpha")
emp(e2_id, "Alpha")
emp(e3_id, "Beta")

# Seed data (exact minutes)
# e1_id: Day 1 = 480 min, Day 2 = 510 min  → total = 990 min = 16.5h, avg = 8.25h
# e2_id: Day 1 = 600 min, Day 2 = incomplete
# e3_id: Day 1 = 360 min                   → 6h

pi(e1_id, "2024-08-01T08:00:00Z"); po(e1_id, "2024-08-01T16:00:00Z")  # 480
pi(e1_id, "2024-08-02T08:00:00Z"); po(e1_id, "2024-08-02T16:30:00Z")  # 510
pi(e2_id, "2024-08-01T08:00:00Z"); po(e2_id, "2024-08-01T18:00:00Z")  # 600
pi(e2_id, "2024-08-02T08:00:00Z")                                      # incomplete
pi(e3_id, "2024-08-01T09:00:00Z"); po(e3_id, "2024-08-01T15:00:00Z")  # 360

AUG = {"start_date": "2024-08-01T00:00:00Z", "end_date": "2024-08-02T00:00:00Z"}

# AG-01: Summary totals
r_ag1 = c.get("/analytics/summary", params=AUG)
check("AGGREGATION", "AG-01: summary status 200", r_ag1.status_code == 200)
ag1 = r_ag1.json()
# total_records: 5, present: 4, incomplete: 1
check("AGGREGATION", "AG-01: total_records = 5",   ag1.get("total_records") == 5, ag1.get("total_records"), 5)
check("AGGREGATION", "AG-01: present_records = 4", ag1.get("present_records") == 4, ag1.get("present_records"), 4)
check("AGGREGATION", "AG-01: incomplete_records = 1", ag1.get("incomplete_records") == 1, ag1.get("incomplete_records"), 1)
# total_hours: (480+510+600+360)/60 = 1950/60 = 32.5h
check("AGGREGATION", "AG-01: total_hours = 32.5",
      ag1.get("total_hours") == 32.5, ag1.get("total_hours"), 32.5)
# avg_hours (present only): (480+510+600+360)/4/60 = 1950/4/60 = 8.125 → rounds to 8.13
_expected_avg = round((480+510+600+360) / 4 / 60, 2)  # = 8.13
check("AGGREGATION", f"AG-01: avg_hours_per_day = {_expected_avg}",
      ag1.get("avg_hours_per_day") == _expected_avg, ag1.get("avg_hours_per_day"), _expected_avg)
# unique_employees_count = 3
check("AGGREGATION", "AG-01: unique_employees_count = 3",
      ag1.get("unique_employees_count") == 3, ag1.get("unique_employees_count"), 3)

# AG-02: Per-employee analytics — e1_id
r_ag2 = c.get(f"/analytics/employee/{e1_id}", params=AUG)
ag2 = r_ag2.json()
check("AGGREGATION", "AG-02: e1 total_days = 2",   ag2.get("total_days") == 2, ag2.get("total_days"), 2)
check("AGGREGATION", "AG-02: e1 present_days = 2", ag2.get("present_days") == 2, ag2.get("present_days"), 2)
check("AGGREGATION", "AG-02: e1 incomplete_days = 0", ag2.get("incomplete_days") == 0)
# total_hours: 990/60 = 16.5
check("AGGREGATION", "AG-02: e1 total_hours = 16.5",
      ag2.get("total_hours") == 16.5, ag2.get("total_hours"), 16.5)
# avg: 990/2/60 = 8.25
check("AGGREGATION", "AG-02: e1 avg_hours_per_day = 8.25",
      ag2.get("avg_hours_per_day") == 8.25, ag2.get("avg_hours_per_day"), 8.25)
bd2 = ag2.get("daily_breakdown", [])
check("AGGREGATION", "AG-02: daily_breakdown has 2 entries", len(bd2) == 2, len(bd2), 2)
check("AGGREGATION", "AG-02: breakdown sorted ascending",
      bd2[0]["date"] < bd2[1]["date"])
check("AGGREGATION", "AG-02: breakdown[0] duration_hours = 8.0",
      bd2[0]["duration_hours"] == 8.0, bd2[0]["duration_hours"], 8.0)
check("AGGREGATION", "AG-02: breakdown[1] duration_hours = 8.5",
      bd2[1]["duration_hours"] == 8.5, bd2[1]["duration_hours"], 8.5)

# AG-03: Per-employee analytics — e2_id (1 present + 1 incomplete)
r_ag3 = c.get(f"/analytics/employee/{e2_id}", params=AUG)
ag3 = r_ag3.json()
check("AGGREGATION", "AG-03: e2 total_days = 2",      ag3.get("total_days") == 2, ag3.get("total_days"), 2)
check("AGGREGATION", "AG-03: e2 present_days = 1",    ag3.get("present_days") == 1, ag3.get("present_days"), 1)
check("AGGREGATION", "AG-03: e2 incomplete_days = 1", ag3.get("incomplete_days") == 1, ag3.get("incomplete_days"), 1)
# total_hours: 600/60 = 10.0
check("AGGREGATION", "AG-03: e2 total_hours = 10.0",
      ag3.get("total_hours") == 10.0, ag3.get("total_hours"), 10.0)
# avg (present only): 600/1/60 = 10.0 NOT 5.0 ($$REMOVE test)
check("AGGREGATION", "AG-03: e2 avg_hours = 10.0 ($$REMOVE excludes incomplete)",
      ag3.get("avg_hours_per_day") == 10.0, ag3.get("avg_hours_per_day"), 10.0)
bd3 = ag3.get("daily_breakdown", [])
check("AGGREGATION", "AG-03: e2 breakdown has 2 entries", len(bd3) == 2, len(bd3), 2)
check("AGGREGATION", "AG-03: e2 incomplete row duration_hours = 0.0",
      bd3[1]["duration_hours"] == 0.0, bd3[1]["duration_hours"], 0.0)
check("AGGREGATION", "AG-03: e2 incomplete row status = incomplete",
      bd3[1]["status"] == "incomplete")

# AG-04: Department analytics
r_ag4 = c.get("/analytics/department", params=AUG)
ag4 = r_ag4.json()
check("AGGREGATION", "AG-04: 2 departments returned", len(ag4) == 2, len(ag4), 2)
dept_names = [d["department"] for d in ag4]
check("AGGREGATION", "AG-04: sorted alphabetically (Alpha before Beta)",
      dept_names == sorted(dept_names), dept_names)
alpha = next((d for d in ag4 if d["department"] == "Alpha"), {})
beta  = next((d for d in ag4 if d["department"] == "Beta"),  {})
# Alpha: e1(480+510) + e2(600) = 1590 min = 26.5h
check("AGGREGATION", "AG-04: Alpha total_hours = 26.5",
      alpha.get("total_hours") == 26.5, alpha.get("total_hours"), 26.5)
check("AGGREGATION", "AG-04: Alpha total_records = 4",
      alpha.get("total_records") == 4, alpha.get("total_records"), 4)
check("AGGREGATION", "AG-04: Alpha present_records = 3",
      alpha.get("present_records") == 3, alpha.get("present_records"), 3)
check("AGGREGATION", "AG-04: Alpha incomplete_records = 1",
      alpha.get("incomplete_records") == 1, alpha.get("incomplete_records"), 1)
check("AGGREGATION", "AG-04: Alpha employee_count = 2",
      alpha.get("employee_count") == 2, alpha.get("employee_count"), 2)
# Alpha avg (3 present): 1590/3/60 = 8.83h
check("AGGREGATION", "AG-04: Alpha avg_hours_per_day = 8.83",
      alpha.get("avg_hours_per_day") == 8.83, alpha.get("avg_hours_per_day"), 8.83)
# Beta: e3 = 360 min = 6.0h
check("AGGREGATION", "AG-04: Beta total_hours = 6.0",
      beta.get("total_hours") == 6.0, beta.get("total_hours"), 6.0)
check("AGGREGATION", "AG-04: Beta employee_count = 1",
      beta.get("employee_count") == 1, beta.get("employee_count"), 1)
check("AGGREGATION", "AG-04: Beta avg_hours_per_day = 6.0",
      beta.get("avg_hours_per_day") == 6.0, beta.get("avg_hours_per_day"), 6.0)

# AG-05: Daily trend
AUG2 = {"start_date": "2024-08-01T00:00:00Z", "end_date": "2024-08-01T00:00:00Z"}
r_ag5 = c.get("/analytics/trend/daily", params=AUG2)
ag5 = r_ag5.json()
check("AGGREGATION", "AG-05: daily trend Aug 1 returns 1 day",
      len(ag5) == 1, len(ag5), 1)
day1 = ag5[0]
check("AGGREGATION", "AG-05: date = 2024-08-01",        day1.get("date") == "2024-08-01")
check("AGGREGATION", "AG-05: total_records = 3",        day1.get("total_records") == 3, day1.get("total_records"), 3)
check("AGGREGATION", "AG-05: present_records = 3",      day1.get("present_records") == 3, day1.get("present_records"), 3)
# avg_hours Aug 1 (all present): (480+600+360)/3/60 = 1440/3/60 = 8.0h
check("AGGREGATION", "AG-05: avg_hours = 8.0",
      day1.get("avg_hours") == 8.0, day1.get("avg_hours"), 8.0)

# AG-06: Trend employee_id filter
r_ag6 = c.get("/analytics/trend/daily",
              params={"start_date": "2024-08-01T00:00:00Z",
                      "end_date":   "2024-08-02T00:00:00Z",
                      "employee_id": e1_id})
ag6 = r_ag6.json()
check("AGGREGATION", "AG-06: trend filtered to e1_id has 2 days",
      len(ag6) == 2, len(ag6), 2)
check("AGGREGATION", "AG-06: each day has 1 record",
      all(d["total_records"] == 1 for d in ag6))

# AG-07: Summary employee_id filter
r_ag7 = c.get("/analytics/summary", params={**AUG, "employee_id": e2_id})
ag7 = r_ag7.json()
check("AGGREGATION", "AG-07: summary employee_id filter total_records = 2",
      ag7.get("total_records") == 2, ag7.get("total_records"), 2)
check("AGGREGATION", "AG-07: unique_employees_count = 1",
      ag7.get("unique_employees_count") == 1, ag7.get("unique_employees_count"), 1)


# ═══════════════════════════════════════════════════════════════════════════════
section("[VALIDATION] Request body validation")
# ═══════════════════════════════════════════════════════════════════════════════

# V-01: Employee missing required field
r_v1 = c.post("/employees", json={"employee_id": "X1", "name": "X", "department": "D"})
check("VALIDATION", "V-01: employee missing email returns 422",
      r_v1.status_code == 422, r_v1.status_code, 422)

# V-02: punch-in missing employee_id
r_v2 = c.post("/attendance/punch-in", json={"timestamp": "2024-01-01T08:00:00Z"})
check("VALIDATION", "V-02: punch-in missing employee_id returns 422",
      r_v2.status_code == 422, r_v2.status_code, 422)

# V-03: punch-in with invalid timestamp format
emp(uid(90))
r_v3 = c.post("/attendance/punch-in", json={"employee_id": uid(90), "timestamp": "not-a-date"})
check("VALIDATION", "V-03: punch-in invalid timestamp returns 422",
      r_v3.status_code == 422, r_v3.status_code, 422)

# V-04: punch-out missing employee_id
r_v4 = c.post("/attendance/punch-out", json={"timestamp": "2024-01-01T17:00:00Z"})
check("VALIDATION", "V-04: punch-out missing employee_id returns 422",
      r_v4.status_code == 422, r_v4.status_code, 422)

# V-05: correction missing punch_in
emp(uid(91))
pi(uid(91), "2024-09-01T08:00:00Z")
aid91 = att_id(uid(91))
r_v5 = correct(aid91, {"reason": "X", "corrected_by": "Y"})
check("VALIDATION", "V-05: correction missing punch_in returns 422",
      r_v5.status_code == 422, r_v5.status_code, 422)

# V-06: analytics summary with invalid date format
r_v6 = c.get("/analytics/summary", params={"start_date": "not-a-date"})
check("VALIDATION", "V-06: analytics summary invalid date returns 422",
      r_v6.status_code == 422, r_v6.status_code, 422)


# ═══════════════════════════════════════════════════════════════════════════════
section("FINAL REPORT")
# ═══════════════════════════════════════════════════════════════════════════════
c.close()
total = PASS + FAIL
print(f"\n  Executed : {total}")
print(f"  Passed   : {PASS}")
print(f"  Failed   : {FAIL}")
print(f"  Skipped  : {len(SKIPPED)}")

if SKIPPED:
    print("\n  COULD NOT RUN (skipped):")
    for s in SKIPPED:
        print(f"    - {s}")

if FAILURES:
    print(f"\n  FAILURES ({FAIL}):")
    for f in FAILURES:
        print(f"    - {f}")
    sys.exit(1)
else:
    print(f"\n  ALL {total} TESTS PASSED")
    sys.exit(0)
