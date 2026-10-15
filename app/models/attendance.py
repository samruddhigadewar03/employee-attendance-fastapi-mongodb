"""
Pydantic models for Attendance records and Corrections.

Key design decisions:
- punch_in / punch_out are stored as UTC datetimes in MongoDB (ISODate).
- date is the calendar day (midnight UTC) used for day-boundary queries.
- corrections is an append-only audit array embedded in the attendance document.
- duration_minutes is computed on punch-out and stored for fast analytics reads.
- status is "present" when punch_out is recorded, otherwise "incomplete".
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class CorrectionSnapshot(BaseModel):
    """The before/after values stored inside each correction audit entry."""
    punch_in: datetime
    punch_out: Optional[datetime] = None
    duration_minutes: Optional[float] = None


class CorrectionEntry(BaseModel):
    """A single immutable audit entry appended when a correction is applied."""
    corrected_at: datetime
    corrected_by: str
    reason: str
    original: CorrectionSnapshot
    updated: CorrectionSnapshot


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class PunchInRequest(BaseModel):
    employee_id: str = Field(..., min_length=1, description="Unique employee identifier")
    # timestamp is optional; defaults to server UTC now when not provided.
    timestamp: Optional[datetime] = Field(
        None,
        description="Punch-in time (UTC ISO-8601). Defaults to server time if omitted.",
    )


class PunchOutRequest(BaseModel):
    employee_id: str = Field(..., min_length=1, description="Unique employee identifier")
    timestamp: Optional[datetime] = Field(
        None,
        description="Punch-out time (UTC ISO-8601). Defaults to server time if omitted.",
    )


class CorrectionRequest(BaseModel):
    punch_in: datetime = Field(..., description="Corrected punch-in (UTC ISO-8601)")
    punch_out: Optional[datetime] = Field(
        None, description="Corrected punch-out (UTC ISO-8601)"
    )
    reason: str = Field(
        ..., min_length=1, max_length=500,
        description="Reason for the correction (1–500 characters)"
    )
    corrected_by: str = Field(
        ..., min_length=1, max_length=100,
        description="Name or ID of person making the correction (1–100 characters)"
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class AttendanceResponse(BaseModel):
    id: str = Field(..., description="MongoDB document id")
    employee_id: str
    date: datetime
    punch_in: datetime
    punch_out: Optional[datetime] = None
    duration_minutes: Optional[float] = None
    status: str
    corrections: list[CorrectionEntry] = []

    model_config = {"populate_by_name": True}

    @classmethod
    def from_mongo(cls, doc: dict) -> "AttendanceResponse":
        corrections = [
            CorrectionEntry(
                corrected_at=c["corrected_at"],
                corrected_by=c["corrected_by"],
                reason=c["reason"],
                original=CorrectionSnapshot(**c["original"]),
                updated=CorrectionSnapshot(**c["updated"]),
            )
            for c in doc.get("corrections", [])
        ]
        return cls(
            id=str(doc["_id"]),
            employee_id=doc["employee_id"],
            date=doc["date"],
            punch_in=doc["punch_in"],
            punch_out=doc.get("punch_out"),
            duration_minutes=doc.get("duration_minutes"),
            status=doc["status"],
            corrections=corrections,
        )
