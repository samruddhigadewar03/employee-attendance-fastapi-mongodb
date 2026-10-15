"""
Attendance router — punch-in, punch-out, history, and manual corrections.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query, status

from app.database import get_attendance_collection, get_employees_collection
from app.models.attendance import (
    AttendanceResponse,
    CorrectionRequest,
    PunchInRequest,
    PunchOutRequest,
)
from app.services import attendance_service

router = APIRouter(prefix="/attendance", tags=["Attendance"])


@router.post(
    "/punch-in",
    response_model=AttendanceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record employee punch-in",
)
def punch_in(body: PunchInRequest):
    """
    Punch in an employee.

    - Employee must exist.
    - No open attendance record may exist for today.
    - Returns 201 with the new attendance record.
    - Returns 404 if employee not found.
    - Returns 409 if already punched in.
    """
    return attendance_service.punch_in(
        request=body,
        employees_col=get_employees_collection(),
        attendance_col=get_attendance_collection(),
    )


@router.post(
    "/punch-out",
    response_model=AttendanceResponse,
    summary="Record employee punch-out",
)
def punch_out(body: PunchOutRequest):
    """
    Punch out an employee.

    - An open punch-in record must exist.
    - punch_out must be after punch_in.
    - Calculates and stores duration_minutes.
    - Returns 200 with the updated record.
    - Returns 409 if no open record found.
    - Returns 422 if timestamp is invalid.
    """
    return attendance_service.punch_out(
        request=body,
        attendance_col=get_attendance_collection(),
    )


@router.get(
    "/{employee_id}",
    response_model=list[AttendanceResponse],
    summary="Get attendance history for an employee",
)
def get_attendance(
    employee_id: str,
    start_date: Optional[datetime] = Query(None, description="Filter start date (inclusive)"),
    end_date: Optional[datetime] = Query(None, description="Filter end date (inclusive)"),
):
    """
    Return attendance records for the employee, newest first.
    Optionally filter by date range (inclusive on both ends).
    Returns 404 if employee not found.
    """
    return attendance_service.get_attendance_history(
        employee_id=employee_id,
        employees_col=get_employees_collection(),
        attendance_col=get_attendance_collection(),
        start_date=start_date,
        end_date=end_date,
    )


@router.put(
    "/{attendance_id}/correct",
    response_model=AttendanceResponse,
    summary="Manually correct an attendance record",
)
def correct_attendance(attendance_id: str, body: CorrectionRequest):
    """
    Apply a manual correction to an attendance record.

    - Original values are preserved in the corrections[] audit array.
    - Correction is atomic: both the new values and audit entry are written together.
    - Returns 404 if the attendance record is not found.
    - Returns 422 if the ObjectId or timestamps are invalid.
    """
    return attendance_service.correct_attendance(
        attendance_id=attendance_id,
        request=body,
        attendance_col=get_attendance_collection(),
    )
