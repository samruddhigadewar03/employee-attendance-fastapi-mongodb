"""
MongoDB client lifecycle management.

- A single MongoClient is created at startup and shared across requests.
- Indexes are created at startup in an idempotent way (create_if_missing).
- The client is closed cleanly on shutdown.

Design note: We do NOT use transactions because the assignment targets a
standalone MongoDB instance (no replica set). Atomic single-document
operations ($set, $push in the same update) are used instead.
"""
import logging
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.database import Database

from app.config import settings

logger = logging.getLogger(__name__)

# Module-level references populated by startup / torn down by shutdown.
_client: MongoClient | None = None
_db: Database | None = None


def connect_db() -> None:
    """Create the MongoClient and ensure all required indexes exist."""
    global _client, _db
    _client = MongoClient(settings.MONGODB_URI, serverSelectionTimeoutMS=5000)

    # Eagerly test connectivity so a misconfigured URI fails at startup.
    _client.admin.command("ping")
    logger.info("Connected to MongoDB: %s", settings.MONGODB_URI)

    _db = _client[settings.MONGODB_DATABASE]
    _create_indexes()


def disconnect_db() -> None:
    """Close the MongoClient on application shutdown."""
    global _client, _db
    if _client is not None:
        _client.close()
        logger.info("Disconnected from MongoDB")
    _client = None
    _db = None


def get_db() -> Database:
    """Return the shared database instance (raises if not connected)."""
    if _db is None:
        raise RuntimeError("Database not initialised. Call connect_db() first.")
    return _db


def get_employees_collection() -> Collection:
    return get_db()["employees"]


def get_attendance_collection() -> Collection:
    return get_db()["attendance"]


# ---------------------------------------------------------------------------
# Index creation
# ---------------------------------------------------------------------------

def _create_indexes() -> None:
    """
    Create all required indexes idempotently.

    Indexes are crucial for:
    - Fast employee lookups by employee_id (unique).
    - Attendance queries by employee_id + date (composite, the most common query).
    - Analytics date-range scans on the 'date' field.
    - Status filtering (for analytics on complete records).
    """
    employees = get_employees_collection()
    attendance = get_attendance_collection()

    # Unique employee identifier index.
    employees.create_index(
        [("employee_id", ASCENDING)],
        unique=True,
        name="idx_employees_employee_id_unique",
    )

    # Composite index: most punch-in / punch-out queries filter on both fields.
    attendance.create_index(
        [("employee_id", ASCENDING), ("date", ASCENDING)],
        name="idx_attendance_employee_date",
    )

    # Stand-alone date index used by analytics date-range $match stages.
    attendance.create_index(
        [("date", ASCENDING)],
        name="idx_attendance_date",
    )

    # Status index used when filtering for "present" records in analytics.
    attendance.create_index(
        [("status", ASCENDING)],
        name="idx_attendance_status",
    )

    logger.info("MongoDB indexes ensured.")
