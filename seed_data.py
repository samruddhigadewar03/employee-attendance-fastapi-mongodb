"""
seed_data.py — Populate the hrone_attendance database with sample data.

Run this AFTER starting the API:
    python seed_data.py

Or directly against MongoDB (ensure MONGODB_URI is set or defaults to localhost):
    python seed_data.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta

# Add project root to path so we can import app config
sys.path.insert(0, os.path.dirname(__file__))

from pymongo import MongoClient

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DATABASE = os.environ.get("MONGODB_DATABASE", "hrone_attendance")


def _utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, 0, tzinfo=timezone.utc)


def _midnight(year, month, day):
    return datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc)


def seed():
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    db = client[MONGODB_DATABASE]

    employees_col = db["employees"]
    attendance_col = db["attendance"]

    # ------------------------------------------------------------------ #
    # Employees                                                            #
    # ------------------------------------------------------------------ #
    employees = [
        {
            "employee_id": "EMP001",
            "name": "Priya Sharma",
            "department": "Engineering",
            "email": "priya.sharma@hrone.com",
            "created_at": _utc(2024, 1, 1),
        },
        {
            "employee_id": "EMP002",
            "name": "Rahul Verma",
            "department": "Engineering",
            "email": "rahul.verma@hrone.com",
            "created_at": _utc(2024, 1, 1),
        },
        {
            "employee_id": "EMP003",
            "name": "Ananya Patel",
            "department": "HR",
            "email": "ananya.patel@hrone.com",
            "created_at": _utc(2024, 1, 15),
        },
        {
            "employee_id": "EMP004",
            "name": "Kiran Mehta",
            "department": "Finance",
            "email": "kiran.mehta@hrone.com",
            "created_at": _utc(2024, 2, 1),
        },
        {
            "employee_id": "EMP005",
            "name": "Siddharth Joshi",
            "department": "Marketing",
            "email": "siddharth.joshi@hrone.com",
            "created_at": _utc(2024, 2, 1),
        },
    ]

    employees_col.delete_many({})
    employees_col.insert_many(employees)
    print(f"Inserted {len(employees)} employees.")

    # ------------------------------------------------------------------ #
    # Attendance records (2 weeks of data)                                 #
    # ------------------------------------------------------------------ #
    attendance_col.delete_many({})

    records = []

    # EMP001 - Full 10 working days (Mon-Fri two weeks), all complete
    emp001_schedule = [
        ((2024, 10, 7), 9, 0, 17, 30),   # Mon
        ((2024, 10, 8), 8, 45, 18, 0),   # Tue
        ((2024, 10, 9), 9, 15, 17, 0),   # Wed
        ((2024, 10, 10), 8, 30, 17, 30), # Thu
        ((2024, 10, 11), 9, 0, 16, 30),  # Fri
        ((2024, 10, 14), 9, 0, 17, 0),   # Mon
        ((2024, 10, 15), 8, 55, 17, 45), # Tue
        ((2024, 10, 16), 9, 0, 17, 0),   # Wed
        ((2024, 10, 17), 8, 30, 18, 0),  # Thu
        ((2024, 10, 18), 9, 0, 17, 0),   # Fri
    ]
    for (yr, mo, da), pi_h, pi_m, po_h, po_m in emp001_schedule:
        pi = _utc(yr, mo, da, pi_h, pi_m)
        po = _utc(yr, mo, da, po_h, po_m)
        dur = round((po - pi).total_seconds() / 60, 2)
        records.append({
            "employee_id": "EMP001",
            "date": _midnight(yr, mo, da),
            "punch_in": pi,
            "punch_out": po,
            "duration_minutes": dur,
            "status": "present",
            "corrections": [],
        })

    # EMP002 - 8 days complete, 1 incomplete (forgot to punch out), 1 missing
    emp002_schedule = [
        ((2024, 10, 7), 9, 30, 17, 30, True),
        ((2024, 10, 8), 9, 0, 17, 0, True),
        ((2024, 10, 9), 9, 0, 17, 0, True),
        ((2024, 10, 10), 8, 45, 18, 15, True),
        ((2024, 10, 11), 9, 0, 17, 0, True),
        ((2024, 10, 14), 9, 0, 17, 0, True),
        ((2024, 10, 15), 9, 0, None, None, False),  # incomplete — no punch out
        ((2024, 10, 16), 8, 30, 17, 30, True),
        ((2024, 10, 17), 9, 0, 17, 30, True),
    ]
    for row in emp002_schedule:
        yr, mo, da = row[0]
        pi_h, pi_m = row[1], row[2]
        complete = row[5]
        pi = _utc(yr, mo, da, pi_h, pi_m)
        if complete:
            po_h, po_m = row[3], row[4]
            po = _utc(yr, mo, da, po_h, po_m)
            dur = round((po - pi).total_seconds() / 60, 2)
            records.append({
                "employee_id": "EMP002",
                "date": _midnight(yr, mo, da),
                "punch_in": pi, "punch_out": po,
                "duration_minutes": dur, "status": "present", "corrections": [],
            })
        else:
            records.append({
                "employee_id": "EMP002",
                "date": _midnight(yr, mo, da),
                "punch_in": pi, "punch_out": None,
                "duration_minutes": None, "status": "incomplete", "corrections": [],
            })

    # EMP003 - 6 days with 1 correction audit entry
    emp003_days = [
        (2024, 10, 7, 10, 0, 18, 0),
        (2024, 10, 8, 10, 15, 17, 30),
        (2024, 10, 9, 9, 45, 17, 45),
        (2024, 10, 14, 10, 0, 18, 0),
        (2024, 10, 15, 9, 30, 17, 30),
        (2024, 10, 16, 10, 0, 18, 0),
    ]
    for i, (yr, mo, da, pi_h, pi_m, po_h, po_m) in enumerate(emp003_days):
        pi = _utc(yr, mo, da, pi_h, pi_m)
        po = _utc(yr, mo, da, po_h, po_m)
        dur = round((po - pi).total_seconds() / 60, 2)
        corrections = []
        if i == 0:
            # Simulated correction: HR fixed a wrong punch-in time
            old_pi = _utc(yr, mo, da, 11, 0)  # originally recorded as 11:00
            corrections = [{
                "corrected_at": _utc(2024, 10, 8, 9, 0),
                "corrected_by": "HR Admin",
                "reason": "Employee badge reader malfunction — actual entry was 10:00",
                "original": {"punch_in": old_pi, "punch_out": po, "duration_minutes": round((po - old_pi).total_seconds() / 60, 2)},
                "updated": {"punch_in": pi, "punch_out": po, "duration_minutes": dur},
            }]
        records.append({
            "employee_id": "EMP003",
            "date": _midnight(yr, mo, da),
            "punch_in": pi, "punch_out": po,
            "duration_minutes": dur, "status": "present", "corrections": corrections,
        })

    # EMP004 & EMP005 - 5 days each
    for emp_id, offset_h in [("EMP004", 0), ("EMP005", 1)]:
        for yr, mo, da in [(2024, 10, 7), (2024, 10, 8), (2024, 10, 9), (2024, 10, 10), (2024, 10, 11)]:
            pi = _utc(yr, mo, da, 9 + offset_h, 0)
            po = _utc(yr, mo, da, 17 + offset_h, 30)
            dur = round((po - pi).total_seconds() / 60, 2)
            records.append({
                "employee_id": emp_id,
                "date": _midnight(yr, mo, da),
                "punch_in": pi, "punch_out": po,
                "duration_minutes": dur, "status": "present", "corrections": [],
            })

    attendance_col.insert_many(records)
    print(f"Inserted {len(records)} attendance records.")
    print("\n[OK] Seed data loaded successfully!")
    print(f"   Database : {MONGODB_DATABASE}")
    print(f"   Employees: {employees_col.count_documents({})}")
    print(f"   Attendance: {attendance_col.count_documents({})}")

    client.close()


if __name__ == "__main__":
    seed()
