"""
Attendance business logic — punch-in, punch-out, and manual corrections.

All state-transition rules are centralised here so the routers stay thin.

Concurrency note:
  We rely on MongoDB's find_one_and_update atomicity for the open-record check.
  Without a replica set we cannot use multi-document transactions, so the
  correction operation uses a single atomic $set + $push update to avoid
  a partially-written audit entry.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import HTTPException
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from app.models.attendance import (
    AttendanceResponse,
    CorrectionRequest,
    PunchInRequest,
    PunchOutRequest,
)

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def _to_date_midnight(dt: datetime) -> datetime:
    """Strip time component to get a UTC midnight datetime for the date field."""
    return dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


def _ensure_utc(dt: datetime) -> datetime:
    """Attach UTC timezone if the datetime is naive (treat as UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _calc_duration(punch_in: datetime, punch_out: datetime) -> float:
    """Return duration in minutes rounded to two decimal places."""
    delta = punch_out - punch_in
    return round(delta.total_seconds() / 60, 2)


# ---------------------------------------------------------------------------
# Punch-in
# ---------------------------------------------------------------------------

def punch_in(
    request: PunchInRequest,
    employees_col: Collection,
    attendance_col: Collection,
) -> AttendanceResponse:
    """
    Record a punch-in for an employee.

    Rules enforced:
    1. Employee must exist.
    2. No open attendance record (punch_out is None) may exist for the same day.
    3. Returns the newly created attendance document.
    """
    # Rule 1: employee must exist.
    employee = employees_col.find_one({"employee_id": request.employee_id})
    if employee is None:
        raise HTTPException(status_code=404, detail=f"Employee '{request.employee_id}' not found.")

    punch_in_time = _ensure_utc(request.timestamp) if request.timestamp else _utc_now()
    date_midnight = _to_date_midnight(punch_in_time)

    # Rule 2: reject if an open record already exists for this day.
    existing = attendance_col.find_one(
        {
            "employee_id": request.employee_id,
            "date": date_midnight,
            "punch_out": None,
        }
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="Employee already has an open punch-in record for today. Punch out first.",
        )

    doc = {
        "employee_id": request.employee_id,
        "date": date_midnight,
        "punch_in": punch_in_time,
        "punch_out": None,
        "duration_minutes": None,
        "status": "incomplete",
        "corrections": [],
    }

    result = attendance_col.insert_one(doc)
    doc["_id"] = result.inserted_id
    logger.info("Punch-in recorded: employee=%s at %s", request.employee_id, punch_in_time)
    return AttendanceResponse.from_mongo(doc)


# ---------------------------------------------------------------------------
# Punch-out
# ---------------------------------------------------------------------------

def punch_out(
    request: PunchOutRequest,
    attendance_col: Collection,
) -> AttendanceResponse:
    """
    Record a punch-out for an employee.

    Rules enforced:
    1. An open attendance record (punch_out is None) must exist.
    2. punch_out timestamp must be strictly after punch_in.
    3. Calculates and stores duration_minutes.
    4. Sets status to "present".

    Uses find_one_and_update for atomic read-modify-write to prevent
    double punch-out in concurrent requests.
    """
    punch_out_time = _ensure_utc(request.timestamp) if request.timestamp else _utc_now()

    # Atomically find an open record and set punch_out in one operation.
    # If no open record exists this returns None.
    open_record = attendance_col.find_one(
        {"employee_id": request.employee_id, "punch_out": None}
    )

    if open_record is None:
        raise HTTPException(
            status_code=409,
            detail="No open punch-in record found for this employee. Punch in first.",
        )

    # PyMongo returns naive datetimes from MongoDB (UTC stored without tzinfo).
    # Coerce to UTC-aware before comparing with the aware punch_out_time.
    punch_in_time = _ensure_utc(open_record["punch_in"])
    if punch_out_time <= punch_in_time:
        raise HTTPException(
            status_code=422,
            detail="punch_out must be strictly after punch_in.",
        )

    duration = _calc_duration(punch_in_time, punch_out_time)

    updated = attendance_col.find_one_and_update(
        {"_id": open_record["_id"], "punch_out": None},  # guard against race
        {
            "$set": {
                "punch_out": punch_out_time,
                "duration_minutes": duration,
                "status": "present",
            }
        },
        return_document=True,
    )

    if updated is None:
        # Another request already closed the record (race condition).
        raise HTTPException(
            status_code=409,
            detail="Punch-out was already recorded by a concurrent request.",
        )

    logger.info(
        "Punch-out recorded: employee=%s duration=%.2f min",
        request.employee_id,
        duration,
    )
    return AttendanceResponse.from_mongo(updated)


# ---------------------------------------------------------------------------
# Manual correction
# ---------------------------------------------------------------------------

def correct_attendance(
    attendance_id: str,
    request: CorrectionRequest,
    attendance_col: Collection,
) -> AttendanceResponse:
    """
    Apply a manual correction to an attendance record.

    Safety guarantees:
    - The original values are captured before any write.
    - A single atomic $set + $push update applies the corrected values and
      appends the audit entry simultaneously. This avoids a partial write where
      the original is overwritten but the audit entry is missing.
    - If the record does not exist, raises 404 before touching the database.
    - Validates that punch_out is after punch_in if both are provided.
    """
    if not ObjectId.is_valid(attendance_id):
        raise HTTPException(status_code=422, detail="Invalid attendance id format.")

    record = attendance_col.find_one({"_id": ObjectId(attendance_id)})
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance record not found.")

    new_punch_in = _ensure_utc(request.punch_in)
    new_punch_out = _ensure_utc(request.punch_out) if request.punch_out else None

    # Also coerce the stored original value so audit snapshot has timezone-aware datetimes.
    original_punch_in = _ensure_utc(record["punch_in"])
    original_punch_out = _ensure_utc(record["punch_out"]) if record.get("punch_out") else None

    if new_punch_out is not None and new_punch_out <= new_punch_in:
        raise HTTPException(
            status_code=422,
            detail="Corrected punch_out must be strictly after corrected punch_in.",
        )

    new_duration: Optional[float] = None
    if new_punch_out is not None:
        new_duration = _calc_duration(new_punch_in, new_punch_out)

    new_status = "present" if new_punch_out is not None else "incomplete"

    # Build the immutable audit entry from the *current* document values.
    audit_entry = {
        "corrected_at": _utc_now(),
        "corrected_by": request.corrected_by,
        "reason": request.reason,
        "original": {
            "punch_in": original_punch_in,
            "punch_out": original_punch_out,
            "duration_minutes": record.get("duration_minutes"),
        },
        "updated": {
            "punch_in": new_punch_in,
            "punch_out": new_punch_out,
            "duration_minutes": new_duration,
        },
    }

    # Single atomic update: overwrite fields + append audit entry.
    updated = attendance_col.find_one_and_update(
        {"_id": ObjectId(attendance_id)},
        {
            "$set": {
                "punch_in": new_punch_in,
                "punch_out": new_punch_out,
                "duration_minutes": new_duration,
                "status": new_status,
            },
            "$push": {"corrections": audit_entry},
        },
        return_document=True,
    )

    if updated is None:
        raise HTTPException(status_code=500, detail="Correction update failed unexpectedly.")

    logger.info("Correction applied to attendance_id=%s by %s", attendance_id, request.corrected_by)
    return AttendanceResponse.from_mongo(updated)


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def get_attendance_history(
    employee_id: str,
    employees_col: Collection,
    attendance_col: Collection,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> list[AttendanceResponse]:
    """
    Return attendance records for an employee, optionally filtered by date range.
    Date range is inclusive on both ends.
    """
    employee = employees_col.find_one({"employee_id": employee_id})
    if employee is None:
        raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found.")

    query: dict = {"employee_id": employee_id}
    if start_date or end_date:
        date_filter: dict = {}
        if start_date:
            date_filter["$gte"] = _to_date_midnight(_ensure_utc(start_date))
        if end_date:
            # Make end_date inclusive: include the entire end day.
            end_midnight = _to_date_midnight(_ensure_utc(end_date))
            date_filter["$lte"] = end_midnight
        query["date"] = date_filter

    cursor = attendance_col.find(query).sort("date", -1)
    return [AttendanceResponse.from_mongo(doc) for doc in cursor]
