"""
Employee router — list and detail endpoints.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from pymongo.errors import DuplicateKeyError

from app.database import get_employees_collection
from app.models.employee import EmployeeCreate, EmployeeResponse

router = APIRouter(prefix="/employees", tags=["Employees"])


@router.get("", response_model=list[EmployeeResponse], summary="List all employees")
def list_employees():
    """Return all employees ordered by employee_id."""
    col = get_employees_collection()
    docs = col.find({}).sort("employee_id", 1)
    return [EmployeeResponse.from_mongo(d) for d in docs]


@router.get("/{employee_id}", response_model=EmployeeResponse, summary="Get employee by ID")
def get_employee(employee_id: str):
    """Return a single employee by their string employee_id."""
    col = get_employees_collection()
    doc = col.find_one({"employee_id": employee_id})
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found.")
    return EmployeeResponse.from_mongo(doc)


@router.post(
    "",
    response_model=EmployeeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new employee",
)
def create_employee(body: EmployeeCreate):
    """
    Create a new employee record.
    Returns 409 if employee_id already exists.
    """
    col = get_employees_collection()
    doc = {
        **body.model_dump(),
        "created_at": datetime.now(timezone.utc),
    }
    try:
        result = col.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail=f"Employee '{body.employee_id}' already exists.",
        )
    doc["_id"] = result.inserted_id
    return EmployeeResponse.from_mongo(doc)
