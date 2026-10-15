# Design Decisions — HROne Attendance API

This document records every significant decision made during implementation,
the reasoning behind it, the alternatives considered, and the trade-offs accepted.

---

## 1. MongoDB Document Structure

### Decision
Attendance corrections are stored as an **embedded array** (`corrections: []`)
inside the attendance document rather than in a separate collection.

### Reasoning
- The corrections array belongs to a single attendance record. There is no
  scenario where a correction needs to be queried across multiple records.
- Embedding keeps the audit trail co-located with the record: one document
  read returns the full history.
- MongoDB allows atomic `$push` on the array in the same operation that
  updates `punch_in` and `punch_out`, which eliminates the need for a
  multi-document transaction.

### Alternative Considered
A separate `corrections` collection with a foreign key to `attendance._id`.
This would allow independent querying of corrections but adds a `$lookup` join
for every attendance read and requires coordination across two documents.

### Trade-off
The embedded array grows unbounded if an attendance record is corrected many
times. In practice, HR corrections per record are rare (1–3 lifetime). For
an assignment context this is fine. A production system might cap corrections
or archive old entries.

---

## 2. No Multi-Document Transactions

### Decision
The implementation does **not** use MongoDB multi-document transactions.

### Reasoning
Multi-document transactions require a replica set. A standalone MongoDB instance
(the expected assignment environment) does not support them. Designing the system
to require replica sets would make the assignment untestable on a standard local
setup.

All safety is achieved via:
- `find_one_and_update` for atomic single-document read-modify-write.
- `$set + $push` in a single update command for the correction operation.

### Alternative Considered
Conditionally enabling transactions when a replica set is detected. This adds
complexity for no benefit in the assignment context.

### Trade-off
A multi-record consistency failure is possible if the MongoDB process crashes
between the `find_one` and the `update` in `correct_attendance()`. The
probability is very low, and the impact (a missing audit entry) is observable
and correctable.

---

## 3. Timestamp Storage and Timezone Handling

### Decision
All timestamps are stored as **UTC-aware datetime** objects (MongoDB ISODate).
The `_ensure_utc()` helper converts naive datetimes (assumed UTC) to aware
datetimes before any database write.

The `date` field stores the **UTC midnight** of the day (`2024-01-15T00:00:00Z`),
not a date string.

### Reasoning
- MongoDB's `ISODate` type is timezone-neutral (always UTC). Storing UTC avoids
  daylight-saving ambiguity.
- Using a `datetime` for `date` (not a string) enables `$gte`/`$lte` range
  queries with proper BSON date comparison, which is faster than string-prefix
  matching.
- UTC midnight as the day boundary is consistent regardless of the server's
  local timezone.

### Alternative Considered
Store `date` as a string `"YYYY-MM-DD"`. Simpler to read but breaks range queries
and makes timezone reasoning harder.

### Trade-off
Clients displaying times in IST (UTC+5:30) must apply the offset themselves.
The API does not localise timestamps. This is documented in the README.

---

## 4. Date-Range Boundary Semantics

### Decision
Date-range filters in analytics and history are **inclusive on both ends**.
`start_date` is normalised to UTC midnight of that day; `end_date` is normalised
to UTC midnight of that day (i.e. `$lte` the start of `end_date`).

### Reasoning
The `date` field stores the start of the day at midnight UTC. Using `$lte` on
the midnight of `end_date` includes all records for that day (since all records
on that day share the same `date` value).

### Trade-off
A record punched in at 23:59 IST on `end_date` has a UTC `date` of the previous
UTC day. This is a known edge case: if the HROne team uses IST-aligned days,
they should call the API with IST-midnight as the boundary (e.g.
`end_date=2024-01-15T18:30:00Z` for 2024-01-16 00:00 IST). This limitation is
documented in the README.

---

## 5. Aggregation Pipeline Design

### Decision
All analytics calculations (sums, averages, counts) are performed entirely inside
MongoDB aggregation pipelines. No attendance documents are loaded into Python
for arithmetic.

### Reasoning
- MongoDB is designed to aggregate data at the storage layer. Fetching all
  records into Python is O(n) in memory and O(n) in network bytes.
- Pipelines can be explained via `explain()` and optimised with indexes without
  changing application code.
- Using `$facet` for per-employee analytics avoids two separate round-trips
  (summary and daily breakdown) for the price of one aggregation.

### Specific Choices
- **`$$REMOVE`** in `$cond` for averages: causes `$avg` to skip `null`
  duration values (incomplete records), so the average is computed only over
  complete records. This is the correct business definition.
- **`$lookup` in department analytics**: joins attendance with employees to
  retrieve the `department` field, which is not stored on the attendance document.
  `preserveNullAndEmptyArrays: False` silently drops orphaned attendance records.
- **`$dateToString`** for the `date` field in daily breakdowns: returns
  `"YYYY-MM-DD"` for easy client consumption without timezone ambiguity.

---

## 6. Attendance State Machine

### Decision
The attendance status is either `"incomplete"` (no punch-out) or `"present"`
(punch-out recorded). There is no `"absent"` state.

### Reasoning
Absence is defined by the *non-existence* of an attendance record for a given
day, not by a record with a special status. Creating "absent" records for every
employee every day would make the database grow as O(employees × calendar_days)
regardless of activity.

### Trade-off
Analytics cannot easily answer "how many employees were absent today" using only
the attendance collection; it would require a cross-join with the employees
collection. This is acceptable for the scope of the assignment.

---

## 7. Validation and Error Responses

### Decision
- 404 is returned for missing employees or attendance records.
- 409 is returned for business rule violations (already punched in, no open record).
- 422 is returned for invalid inputs (bad timestamp order, invalid ObjectId format).
- 500 is returned for unexpected database failures.

FastAPI's built-in 422 Unprocessable Entity is used for Pydantic validation
failures (missing fields, wrong types).

### Reasoning
This mapping follows standard REST conventions and makes the error cause
unambiguous to the caller without exposing internal details.

### Alternative Considered
Using 400 for all business rule violations. Rejected because 409 Conflict
explicitly communicates state conflicts (e.g., already punched in), which is
semantically more accurate and easier to handle in client code.

---

## 8. Concurrency Safety on Punch-Out

### Decision
`punch_out()` uses `find_one_and_update` with filter `{"_id": …, "punch_out": None}`
as a guard.

### Reasoning
Without this guard, two simultaneous punch-out requests could both `find_one` an
open record, both calculate a duration, and both `update_one` — resulting in two
writes where the second silently overwrites the first. The `find_one_and_update`
guard ensures only one of them succeeds; the other gets `None` back and raises
HTTP 409.

This is safe on a single MongoDB instance because `findAndModify` is an atomic
single-document operation.

---

## 9. Testing Strategy and Limitations

### Decision
Tests use a real MongoDB instance (`hrone_test` database) rather than mocks for
service-layer tests.

### Reasoning
The primary risk area is the aggregation pipelines. A mock cannot verify that a
`$group + $project` pipeline produces the correct result; only execution against
a real MongoDB engine can. Integration tests with `hrone_test` give genuine
coverage.

Unit tests with mocks are used only for focused validation logic (e.g., checking
that the service raises 422 for an invalid ObjectId), where the MongoDB
interaction is irrelevant to the assertion.

### Limitation
Tests require a running MongoDB instance. If MongoDB is not running, all tests
that use `test_client` will fail with a connection error. The README documents
the setup requirement.

---

## 10. Frontend Architecture

### Decision
The frontend is a single HTML + CSS + JavaScript file set with no external
JavaScript framework.

### Reasoning
- The assignment does not require a JavaScript framework.
- Vanilla JS is easier to explain in a trainee interview context.
- The canvas-based chart avoids adding Chart.js or similar as a dependency.

### Trade-off
Adding interactivity (real-time updates, optimistic UI) would require more
boilerplate than React or Vue. Acceptable for the assignment scope.
