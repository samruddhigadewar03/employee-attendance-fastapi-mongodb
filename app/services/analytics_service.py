"""
Analytics aggregation pipelines.

Every calculation is performed inside MongoDB using the aggregation framework.
Python code only shapes inputs and formats outputs — no data is loaded into
memory for calculation.

Pipeline conventions:
- $match always appears first to benefit from indexes.
- $group produces the per-employee or per-department summary.
- $project shapes the final output fields.
- Only "present" records (punch_out not null) are used for duration averages.
  Incomplete records are counted but excluded from duration averages.
- Date-range boundaries are inclusive on both ends.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from pymongo.collection import Collection

logger = logging.getLogger(__name__)


def _to_utc_midnight(dt: datetime) -> datetime:
    """Normalise a date to UTC midnight for consistent $match boundaries."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _date_match(start_date: Optional[datetime], end_date: Optional[datetime]) -> dict:
    """Build the $match date filter dict. Boundaries are inclusive."""
    date_filter: dict = {}
    if start_date:
        date_filter["$gte"] = _to_utc_midnight(start_date)
    if end_date:
        date_filter["$lte"] = _to_utc_midnight(end_date)
    return date_filter


# ---------------------------------------------------------------------------
# Summary analytics
# ---------------------------------------------------------------------------

def get_summary(
    attendance_col: Collection,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    employee_id: Optional[str] = None,
) -> dict:
    """
    Return a high-level attendance summary across all employees (or one employee)
    for the requested date range.

    Output fields:
    - total_records: total attendance records in range
    - present_records: records with punch_out (status == "present")
    - incomplete_records: records without punch_out
    - avg_hours_per_day: average duration in hours (present records only)
    - total_hours: sum of all duration_minutes / 60 (present records only)
    - unique_employees: count of distinct employees with any attendance
    """
    match_filter: dict = {}
    if start_date or end_date:
        match_filter["date"] = _date_match(start_date, end_date)
    if employee_id:
        match_filter["employee_id"] = employee_id

    pipeline = [
        {"$match": match_filter},
        {
            "$group": {
                "_id": None,
                "total_records": {"$sum": 1},
                "present_records": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "present"]}, 1, 0]
                    }
                },
                "incomplete_records": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "incomplete"]}, 1, 0]
                    }
                },
                # Average only over records that have a duration.
                "avg_duration_minutes": {
                    "$avg": {
                        "$cond": [
                            {"$ne": ["$duration_minutes", None]},
                            "$duration_minutes",
                            "$$REMOVE",
                        ]
                    }
                },
                "total_duration_minutes": {
                    "$sum": {
                        "$cond": [
                            {"$ne": ["$duration_minutes", None]},
                            "$duration_minutes",
                            0,
                        ]
                    }
                },
                "unique_employees": {"$addToSet": "$employee_id"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "total_records": 1,
                "present_records": 1,
                "incomplete_records": 1,
                "avg_hours_per_day": {
                    "$round": [{"$divide": [{"$ifNull": ["$avg_duration_minutes", 0]}, 60]}, 2]
                },
                "total_hours": {
                    "$round": [{"$divide": ["$total_duration_minutes", 60]}, 2]
                },
                "unique_employees_count": {"$size": "$unique_employees"},
            }
        },
    ]

    results = list(attendance_col.aggregate(pipeline))
    if not results:
        return {
            "total_records": 0,
            "present_records": 0,
            "incomplete_records": 0,
            "avg_hours_per_day": 0.0,
            "total_hours": 0.0,
            "unique_employees_count": 0,
        }
    return results[0]


# ---------------------------------------------------------------------------
# Per-employee analytics
# ---------------------------------------------------------------------------

def get_employee_analytics(
    employee_id: str,
    attendance_col: Collection,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> dict:
    """
    Detailed analytics for a single employee.

    Output fields:
    - employee_id
    - total_days: total attendance records
    - present_days: days with completed attendance
    - incomplete_days: days without punch_out
    - total_hours: cumulative hours worked
    - avg_hours_per_day: average hours per *present* day
    - daily_breakdown: list of {date, punch_in, punch_out, duration_hours, status}
    """
    match_filter: dict = {"employee_id": employee_id}
    if start_date or end_date:
        match_filter["date"] = _date_match(start_date, end_date)

    pipeline = [
        {"$match": match_filter},
        {"$sort": {"date": 1}},
        {
            "$facet": {
                "summary": [
                    {
                        "$group": {
                            "_id": None,
                            "total_days": {"$sum": 1},
                            "present_days": {
                                "$sum": {
                                    "$cond": [{"$eq": ["$status", "present"]}, 1, 0]
                                }
                            },
                            "incomplete_days": {
                                "$sum": {
                                    "$cond": [{"$eq": ["$status", "incomplete"]}, 1, 0]
                                }
                            },
                            "total_duration_minutes": {
                                "$sum": {"$ifNull": ["$duration_minutes", 0]}
                            },
                            "avg_duration_minutes": {
                                "$avg": {
                                    "$cond": [
                                        {"$ne": ["$duration_minutes", None]},
                                        "$duration_minutes",
                                        "$$REMOVE",
                                    ]
                                }
                            },
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "total_days": 1,
                            "present_days": 1,
                            "incomplete_days": 1,
                            "total_hours": {
                                "$round": [
                                    {"$divide": ["$total_duration_minutes", 60]},
                                    2,
                                ]
                            },
                            "avg_hours_per_day": {
                                "$round": [
                                    {
                                        "$divide": [
                                            {"$ifNull": ["$avg_duration_minutes", 0]},
                                            60,
                                        ]
                                    },
                                    2,
                                ]
                            },
                        }
                    },
                ],
                "daily_breakdown": [
                    {
                        "$project": {
                            "_id": 0,
                            "date": {
                                "$dateToString": {"format": "%Y-%m-%d", "date": "$date"}
                            },
                            "punch_in": 1,
                            "punch_out": 1,
                            "duration_hours": {
                                "$round": [
                                    {
                                        "$divide": [
                                            {"$ifNull": ["$duration_minutes", 0]},
                                            60,
                                        ]
                                    },
                                    2,
                                ]
                            },
                            "status": 1,
                        }
                    }
                ],
            }
        },
    ]

    results = list(attendance_col.aggregate(pipeline))
    if not results:
        return {
            "employee_id": employee_id,
            "total_days": 0,
            "present_days": 0,
            "incomplete_days": 0,
            "total_hours": 0.0,
            "avg_hours_per_day": 0.0,
            "daily_breakdown": [],
        }

    facet = results[0]
    summary = facet["summary"][0] if facet["summary"] else {
        "total_days": 0,
        "present_days": 0,
        "incomplete_days": 0,
        "total_hours": 0.0,
        "avg_hours_per_day": 0.0,
    }

    return {
        "employee_id": employee_id,
        **summary,
        "daily_breakdown": facet["daily_breakdown"],
    }


# ---------------------------------------------------------------------------
# Department analytics
# ---------------------------------------------------------------------------

def get_department_analytics(
    attendance_col: Collection,
    employees_col: Collection,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> list[dict]:
    """
    Per-department attendance summary.

    Uses $lookup to join attendance with employees so we can group by department.

    Output (per department):
    - department
    - total_records
    - present_records
    - incomplete_records
    - total_hours
    - avg_hours_per_day
    - employee_count: distinct employees with attendance in range
    """
    match_filter: dict = {}
    if start_date or end_date:
        match_filter["date"] = _date_match(start_date, end_date)

    pipeline = [
        {"$match": match_filter},
        # Join each attendance record with its employee document.
        {
            "$lookup": {
                "from": "employees",
                "localField": "employee_id",
                "foreignField": "employee_id",
                "as": "employee_info",
            }
        },
        # Unwind: skip attendance records with no matching employee.
        {"$unwind": {"path": "$employee_info", "preserveNullAndEmptyArrays": False}},
        {
            "$group": {
                "_id": "$employee_info.department",
                "total_records": {"$sum": 1},
                "present_records": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "present"]}, 1, 0]
                    }
                },
                "incomplete_records": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "incomplete"]}, 1, 0]
                    }
                },
                "total_duration_minutes": {
                    "$sum": {"$ifNull": ["$duration_minutes", 0]}
                },
                "avg_duration_minutes": {
                    "$avg": {
                        "$cond": [
                            {"$ne": ["$duration_minutes", None]},
                            "$duration_minutes",
                            "$$REMOVE",
                        ]
                    }
                },
                "unique_employees": {"$addToSet": "$employee_id"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "total_records": 1,
                "present_records": 1,
                "incomplete_records": 1,
                "total_hours": {
                    "$round": [{"$divide": ["$total_duration_minutes", 60]}, 2]
                },
                "avg_hours_per_day": {
                    "$round": [
                        {
                            "$divide": [
                                {"$ifNull": ["$avg_duration_minutes", 0]},
                                60,
                            ]
                        },
                        2,
                    ]
                },
                "employee_count": {"$size": "$unique_employees"},
            }
        },
        {"$sort": {"department": 1}},
    ]

    return list(attendance_col.aggregate(pipeline))


# ---------------------------------------------------------------------------
# Daily trend analytics
# ---------------------------------------------------------------------------

def get_daily_trend(
    attendance_col: Collection,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    employee_id: Optional[str] = None,
) -> list[dict]:
    """
    Day-by-day breakdown of attendance stats across all employees or for one.

    Output (per day):
    - date: "YYYY-MM-DD"
    - total_records
    - present_records
    - avg_hours
    """
    match_filter: dict = {}
    if start_date or end_date:
        match_filter["date"] = _date_match(start_date, end_date)
    if employee_id:
        match_filter["employee_id"] = employee_id

    pipeline = [
        {"$match": match_filter},
        {
            "$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date"}},
                "total_records": {"$sum": 1},
                "present_records": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "present"]}, 1, 0]
                    }
                },
                "avg_duration_minutes": {
                    "$avg": {
                        "$cond": [
                            {"$ne": ["$duration_minutes", None]},
                            "$duration_minutes",
                            "$$REMOVE",
                        ]
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$_id",
                "total_records": 1,
                "present_records": 1,
                "avg_hours": {
                    "$round": [
                        {
                            "$divide": [
                                {"$ifNull": ["$avg_duration_minutes", 0]},
                                60,
                            ]
                        },
                        2,
                    ]
                },
            }
        },
        {"$sort": {"date": 1}},
    ]

    return list(attendance_col.aggregate(pipeline))
