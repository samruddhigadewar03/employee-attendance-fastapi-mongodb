"""
Pydantic models for the Employee resource.

employee_id is a plain string identifier (e.g. "EMP001").
MongoDB's _id ObjectId is serialised as a string in responses via PyObjectId.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId
from pydantic import BaseModel, Field, field_validator
from pydantic import EmailStr


class PyObjectId(str):
    """Pydantic-compatible wrapper that validates and serialises ObjectId."""

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v: Any) -> str:
        if isinstance(v, ObjectId):
            return str(v)
        if ObjectId.is_valid(str(v)):
            return str(v)
        raise ValueError(f"Invalid ObjectId: {v!r}")

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        from pydantic_core import core_schema
        return core_schema.no_info_plain_validator_function(cls.validate)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class EmployeeBase(BaseModel):
    employee_id: str = Field(..., min_length=1, description="Unique employee identifier, e.g. EMP001")
    name: str = Field(..., min_length=1, description="Full name")
    department: str = Field(..., min_length=1, description="Department name")
    email: EmailStr = Field(..., description="Work email (RFC 5322 format required)")


class EmployeeCreate(EmployeeBase):
    pass


class EmployeeResponse(EmployeeBase):
    id: str = Field(..., description="MongoDB document id")
    created_at: datetime

    model_config = {"populate_by_name": True}

    @classmethod
    def from_mongo(cls, doc: dict) -> "EmployeeResponse":
        return cls(
            id=str(doc["_id"]),
            employee_id=doc["employee_id"],
            name=doc["name"],
            department=doc["department"],
            email=doc["email"],
            created_at=doc["created_at"],
        )
