"""
Analytics router — summary, per-employee, department, and daily-trend endpoints.
All heavy computation happens inside MongoDB aggregation pipelines.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

from app.database import get_attendance_collection, get_employees_collection
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/summary", summary="Overall attendance summary")
def summary(
    start_date: Optional[datetime] = Query(None, description="Range start (inclusive)"),
    end_date: Optional[datetime] = Query(None, description="Range end (inclusive)"),
    employee_id: Optional[str] = Query(None, description="Filter to a single employee"),
):
    """
    Aggregate attendance statistics for the given date range.

    Returns total records, present/incomplete counts, total hours,
    average hours per day, and unique employee count.
    """
    return analytics_service.get_summary(
        attendance_col=get_attendance_collection(),
        start_date=start_date,
        end_date=end_date,
        employee_id=employee_id,
    )


@router.get("/employee/{employee_id}", summary="Per-employee attendance analytics")
def employee_analytics(
    employee_id: str,
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
):
    """
    Detailed analytics for a single employee including a daily breakdown.

    Returns total_days, present_days, incomplete_days, total_hours,
    avg_hours_per_day, and a per-day breakdown list.
    """
    return analytics_service.get_employee_analytics(
        employee_id=employee_id,
        attendance_col=get_attendance_collection(),
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/department", summary="Per-department attendance analytics")
def department_analytics(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
):
    """
    Attendance stats grouped by department.

    Uses $lookup to join attendance with employee records, then $group by department.
    Returns department name, record counts, total/average hours, and employee count.
    """
    return analytics_service.get_department_analytics(
        attendance_col=get_attendance_collection(),
        employees_col=get_employees_collection(),
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/trend/daily", summary="Day-by-day attendance trend")
def daily_trend(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    employee_id: Optional[str] = Query(None),
):
    """
    Day-by-day attendance breakdown.

    Returns a list of {date, total_records, present_records, avg_hours} sorted
    chronologically. Useful for trend charts.
    """
    return analytics_service.get_daily_trend(
        attendance_col=get_attendance_collection(),
        start_date=start_date,
        end_date=end_date,
        employee_id=employee_id,
    )
