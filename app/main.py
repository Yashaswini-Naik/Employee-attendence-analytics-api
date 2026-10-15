"""
Employee Attendance & Analytics API
Run: uvicorn app.main:app --port 8000
Environment: MONGO_URI, MONGO_DB
"""
import os
import re
import calendar
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path as ApiPath
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError
from bson import json_util
import json

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), serverSelectionTimeoutMS=3000)
db = client[os.getenv("MONGO_DB", "attendance_db")]
IST = timezone(timedelta(hours=5, minutes=30))
PRESENT_STATUSES = ("PRESENT", "WFH", "ON_DUTY")
ALL_STATUSES = ("PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY")
MIN_EPOCH_MS = 100_000_000_000
MAX_EPOCH_MS = 4_102_444_800_000

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


def create_indexes():
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_employee_code")
    db.employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)], name="department_employee")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="department_joined")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_attendance_employee_date")
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="attendance_date_employee")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", DESCENDING), ("status", ASCENDING)], name="attendance_employee_date_status")
    db.attendance_logs.create_index([("date", ASCENDING), ("status", ASCENDING), ("late_minutes", DESCENDING)], name="attendance_date_status_late")


@app.on_event("startup")
def startup():
    create_indexes()


def to_ms(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return int(dt.timestamp() * 1000)


def from_ms(value):
    if value is None:
        return datetime.now(IST).replace(microsecond=0)
    if isinstance(value, bool) or not isinstance(value, int) or value < MIN_EPOCH_MS or value > MAX_EPOCH_MS:
        raise ValueError("timestamp must be an integer epoch-millisecond value in the supported range")
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).astimezone(IST).replace(microsecond=0)


def utc_naive(dt):
    """PyMongo stores BSON datetimes as UTC; normalize aware input to naive UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)


def stored_to_ist(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).replace(microsecond=0)


def round_half_up(value, places=2):
    if value is None:
        return None
    q = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))


def parse_date(value, field="date"):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return parsed
    except Exception:
        raise HTTPException(422, f"{field} must be YYYY-MM-DD")


def parse_month(value):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value or ""):
        raise HTTPException(422, "month must be YYYY-MM")
    year, month = map(int, value.split("-"))
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    return start, end


def working_days(start, end):
    if start > end:
        return 0
    return sum(1 for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5)


def shift_start_dt(att_date, shift_start):
    h, m = map(int, shift_start.split(":"))
    return datetime.combine(att_date, time(h, m), IST)


def shift_end_dt(att_date, shift_start, shift_end):
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))
    start = datetime.combine(att_date, time(sh, sm), IST)
    end = datetime.combine(att_date, time(eh, em), IST)
    if end <= start:
        end += timedelta(days=1)
    return end


def attendance_date(punch_in, shift_start, shift_end):
    local = stored_to_ist(punch_in)
    start_h, start_m = map(int, shift_start.split(":"))
    end_h, end_m = map(int, shift_end.split(":"))
    if (end_h, end_m) <= (start_h, start_m) and (local.hour, local.minute) < (end_h, end_m):
        return (local.date() - timedelta(days=1)).isoformat()
    return local.date().isoformat()


def compute_late_minutes(punch_in, shift_start):
    local = stored_to_ist(punch_in)
    h, m = map(int, shift_start.split(":"))
    start = local.replace(hour=h, minute=m, second=0, microsecond=0)
    delta = (local - start).total_seconds()
    if delta > 600:
        return int(delta // 60)
    return 0


def compute_work_hours(punch_in, punch_out):
    seconds = (punch_out - punch_in).total_seconds()
    return round_half_up(seconds / 3600, 2)


def compute_overtime(punch_out, shift_start, shift_end, date_str):
    d = parse_date(date_str)
    end = shift_end_dt(d, shift_start, shift_end)
    out = stored_to_ist(punch_out)
    minutes = int((out - end).total_seconds() // 60)
    return minutes if minutes >= 30 else 0


def recompute_derived(record, emp):
    pin = record.get("punch_in")
    pout = record.get("punch_out")
    record["late_minutes"] = compute_late_minutes(pin, emp["shift_start"]) if pin else 0
    if pin and pout:
        record["work_hours"] = compute_work_hours(stored_to_ist(pin), stored_to_ist(pout))
        record["overtime_minutes"] = compute_overtime(pout, emp["shift_start"], emp["shift_end"], record["date"])
        record["half_day"] = record["work_hours"] < 4.50
    else:
        record["work_hours"] = None
        record["overtime_minutes"] = 0
        record["half_day"] = False
    return record


def serialize_attendance(doc):
    out = dict(doc)
    out.pop("_id", None)
    for field in ("punch_in", "punch_out"):
        out[field] = to_ms(stored_to_ist(out.get(field))) if out.get(field) is not None else None
    out.setdefault("history", [])
    out.setdefault("half_day", False)
    return out


def serialize_employee(doc):
    out = dict(doc)
    out.pop("_id", None)
    out["created_at"] = to_ms(stored_to_ist(out["created_at"]))
    return out


class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def valid_joined_on(cls, value):
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        except Exception:
            raise ValueError("joined_on must be YYYY-MM-DD")
        return value

    @model_validator(mode="after")
    def shift_not_equal(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def valid_timestamp(cls, value):
        if value is not None and (isinstance(value, bool) or value < MIN_EPOCH_MS or value > MAX_EPOCH_MS):
            raise ValueError("punched_at must be epoch milliseconds in the supported range")
        return value


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None

    @field_validator("punched_at")
    @classmethod
    def valid_timestamp(cls, value):
        if value is not None and (isinstance(value, bool) or value < MIN_EPOCH_MS or value > MAX_EPOCH_MS):
            raise ValueError("punched_at must be epoch milliseconds in the supported range")
        return value


class RegularizeIn(BaseModel):
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("punch_in", "punch_out")
    @classmethod
    def valid_timestamp(cls, value):
        if value is not None and (isinstance(value, bool) or value < MIN_EPOCH_MS or value > MAX_EPOCH_MS):
            raise ValueError("timestamps must be epoch milliseconds in the supported range")
        return value


@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(503, "MongoDB unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = utc_naive(datetime.now(IST))
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return serialize_employee(doc)


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {"department": department} if department else {}
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = [serialize_employee(x) for x in db.employees.find(q, {"_id": 0}).sort("emp_code", ASCENDING).skip(skip).limit(page_size)]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")
    try:
        ts_ist = from_ms(body.punched_at) if body.punched_at is not None else datetime.now(IST).replace(microsecond=0)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    d = attendance_date(ts_ist, emp["shift_start"], emp["shift_end"])
    record = {
        "emp_code": body.emp_code, "date": d, "status": body.status,
        "punch_in": utc_naive(ts_ist), "punch_out": None, "work_hours": None,
        "late_minutes": compute_late_minutes(ts_ist, emp["shift_start"]),
        "overtime_minutes": 0, "half_day": False, "history": [],
    }
    try:
        db.attendance_logs.insert_one(record)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    return serialize_attendance(record)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")
    try:
        out_ist = from_ms(body.punched_at) if body.punched_at is not None else datetime.now(IST).replace(microsecond=0)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    out_utc = utc_naive(out_ist)
    rec = db.attendance_logs.find_one({"emp_code": body.emp_code, "punch_in": {"$lte": out_utc}, "punch_out": None}, sort=[("punch_in", DESCENDING)])
    if not rec:
        any_emp = db.attendance_logs.find_one({"emp_code": body.emp_code})
        if not any_emp:
            raise HTTPException(404, "no punch-in found")
        raise HTTPException(404, "no open punch-in found")
    pin_ist = stored_to_ist(rec["punch_in"])
    delta = (out_ist - pin_ist).total_seconds()
    if delta <= 0 or delta > 24 * 3600:
        raise HTTPException(422, "punched_at must be after punch-in and within 24 hours")
    updated = db.attendance_logs.find_one_and_update(
        {"_id": rec["_id"], "punch_out": None},
        {"$set": {"punch_out": out_utc}},
        return_document=True,
    )
    if not updated:
        raise HTTPException(409, "record already punched out")
    recompute_derived(updated, emp)
    db.attendance_logs.update_one({"_id": rec["_id"]}, {"$set": {k: updated[k] for k in ("work_hours", "late_minutes", "overtime_minutes", "half_day")}})
    updated = db.attendance_logs.find_one({"_id": rec["_id"]})
    return serialize_attendance(updated)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from:
        parse_date(date_from, "date_from")
    if date_to:
        parse_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be on or before date_to")
    if date_from or date_to:
        q["date"] = {}
        if date_from: q["date"]["$gte"] = date_from
        if date_to: q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    total = db.attendance_logs.count_documents(q)
    cursor = db.attendance_logs.find(q, {"_id": 0}).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip((page - 1) * page_size).limit(page_size)
    items = [serialize_attendance(x) for x in cursor]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    parse_date(date)
    emp = db.employees.find_one({"emp_code": emp_code})
    rec = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not emp or not rec:
        raise HTTPException(404, "employee or attendance record not found")
    original = dict(rec)
    changes = {}
    for field in ("status", "punch_in", "punch_out"):
        if field in body.model_fields_set:
            value = getattr(body, field)
            if field in ("punch_in", "punch_out"):
                value = utc_naive(from_ms(value)) if value is not None else None
            old = rec.get(field)
            if old != value:
                changes[field] = {"from": to_ms(stored_to_ist(old)) if field.startswith("punch_") and old else old,
                                  "to": to_ms(stored_to_ist(value)) if field.startswith("punch_") and value else value}
                rec[field] = value
    if not changes:
        raise HTTPException(422, "request does not change anything")
    if rec.get("status") in ("ABSENT", "LEAVE"):
        if "punch_in" in body.model_fields_set and body.punch_in is not None or "punch_out" in body.model_fields_set and body.punch_out is not None:
            raise HTTPException(422, "ABSENT and LEAVE cannot have punch times")
        rec["punch_in"] = rec["punch_out"] = None
    else:
        if rec.get("punch_in") is None:
            raise HTTPException(422, "presence status requires punch_in")
        if attendance_date(stored_to_ist(rec["punch_in"]), emp["shift_start"], emp["shift_end"]) != date:
            raise HTTPException(422, "punch_in must fall on the attendance date")
        if rec.get("punch_out") is not None:
            seconds = (stored_to_ist(rec["punch_out"]) - stored_to_ist(rec["punch_in"])).total_seconds()
            if seconds <= 0 or seconds > 86400:
                raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
    recompute_derived(rec, emp)
    for field in ("work_hours", "late_minutes", "overtime_minutes", "half_day"):
        if original.get(field) != rec.get(field):
            changes[field] = {"from": original.get(field), "to": rec.get(field)}
    history_entry = {"at": utc_naive(datetime.now(IST)), "by": body.regularized_by, "reason": body.reason, "changes": changes}
    result = db.attendance_logs.update_one(
        {"_id": rec["_id"], "history": original.get("history", [])},
        {"$set": {k: rec[k] for k in ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day")},
         "$push": {"history": history_entry}}
    )
    if result.modified_count != 1:
        raise HTTPException(409, "record changed concurrently; retry")
    return serialize_attendance(db.attendance_logs.find_one({"_id": rec["_id"]}))


def monthly_log_match(emp_code, start, end):
    return {"emp_code": emp_code, "date": {"$gte": start.isoformat(), "$lte": end.isoformat()}}


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    start, end = parse_month(month)
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")
    first = max(start, date.fromisoformat(emp["joined_on"]))
    workdays = working_days(first, end) if first <= end else 0
    pipeline = [
        {"$match": monthly_log_match(emp_code, start, end)},
        {"$group": {"_id": None,
            "present_days": {"$sum": {"$cond": [{"$in": ["$status", list(PRESENT_STATUSES)]},
                {"$cond": [{"$eq": ["$half_day", True]}, 0.5, {"$cond": [{"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, [1, 7]]}, 0, 1]}]}, 0]}},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, "$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": "$overtime_minutes"}}}
    ]
    result = list(db.attendance_logs.aggregate(pipeline))
    agg = result[0] if result else {}
    present = agg.get("present_days", 0)
    pct = round_half_up(present / workdays * 100, 2) if workdays else None
    return {"emp_code": emp_code, "month": month, "working_days": workdays, "present_days": round_half_up(present, 2),
        "leave_days": agg.get("leave_days", 0), "late_count": agg.get("late_count", 0),
        "total_late_minutes": agg.get("total_late_minutes", 0), "total_overtime_minutes": agg.get("total_overtime_minutes", 0), "attendance_pct": pct}


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    start, end = parse_month(month)
    match_emp = {"joined_on": {"$lte": end.isoformat()}}
    if department:
        match_emp["department"] = department
    pipeline = [
        {"$match": match_emp},
        {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code", "joined": "$joined_on"},
            "pipeline": [{"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$code"]},
                {"$gte": ["$date", start.isoformat()]}, {"$lte": ["$date", end.isoformat()]}, {"$gte": ["$date", "$$joined"]}]}}},
                {"$project": {"status": 1, "date": 1, "half_day": 1, "work_hours": 1, "late_minutes": 1, "overtime_minutes": 1}}],
            "as": "logs"}},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {"_id": "$department", "headcount_codes": {"$addToSet": "$emp_code"},
            "present_days": {"$sum": {"$cond": [{"$in": ["$logs.status", list(PRESENT_STATUSES)]},
                {"$cond": [{"$eq": ["$logs.half_day", True]}, 0.5, {"$cond": [{"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, [1, 7]]}, 0, 1]}]}, 0]}},
            "work_hours_values": {"$push": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENT_STATUSES)]}, {"$ne": ["$logs.work_hours", None]}]}, "$logs.work_hours", "$$REMOVE"]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$logs.late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$cond": [{"$gt": ["$logs.late_minutes", 0]}, "$logs.late_minutes", 0]}},
            "leave_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}}}},
        {"$project": {"_id": 0, "department": "$_id", "headcount": {"$size": "$headcount_codes"}, "present_days": 1,
            "avg_work_hours": {"$cond": [{"$gt": [{"$size": "$work_hours_values"}, 0]}, {"$round": [{"$avg": "$work_hours_values"}, 2]}, None]},
            "late_count": 1, "total_late_minutes": 1, "leave_count": 1, "on_duty_count": 1}},
        {"$sort": {"department": 1}}
    ]
    items = list(db.employees.aggregate(pipeline))
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int = Query(10, ge=1, le=50), department: Optional[str] = None):
    start, end = parse_month(month)
    pipeline = [
        {"$match": {"date": {"$gte": start.isoformat(), "$lte": end.isoformat()}, "late_minutes": {"$gt": 0}}},
        {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}, "late_count": {"$sum": 1}}},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
    ]
    if department:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline += [
        {"$set": {"emp_code": "$_id", "name": "$employee.name", "department": "$employee.department"}},
        {"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}},
        {"$project": {"_id": 0, "rank": 1, "emp_code": 1, "name": 1, "department": 1, "total_late_minutes": 1, "late_count": 1}}
    ]
    return {"month": month, "items": list(db.attendance_logs.aggregate(pipeline))}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_date: str = Query(..., alias="from"), to_date: str = Query(..., alias="to")):
    start, end = parse_date(from_date, "from"), parse_date(to_date, "to")
    if end < start:
        raise HTTPException(422, "to must be on or after from")
    if (end - start).days + 1 > 92:
        raise HTTPException(422, "date range cannot exceed 92 days")
    if not db.employees.find_one({"department": department}):
        raise HTTPException(404, "unknown department")
    pipeline = [
        {"$match": {"department": department}},
        {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code", "joined": "$joined_on"},
            "pipeline": [{"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$code"]},
                {"$gte": ["$date", start.isoformat()]}, {"$lte": ["$date", end.isoformat()]}]}}},
                {"$project": {"date": 1, "status": 1, "half_day": 1, "late_minutes": 1}}],
            "as": "logs"}},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {"_id": {"$ifNull": ["$logs.date", None]},
            "present_count": {"$sum": {"$cond": [{"$in": ["$logs.status", list(PRESENT_STATUSES)]}, {"$cond": [{"$eq": ["$logs.half_day", True]}, 0.5, 1]}, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$logs.late_minutes", 0]}, 1, 0]}}}},
        {"$match": {"_id": {"$ne": None}}},
        {"$set": {"day": {"$dateFromString": {"dateString": "$_id"}}}},
        {"$densify": {"field": "day", "range": {"step": 1, "unit": "day", "bounds": [datetime.combine(start, time.min), datetime.combine(end, time.min)]}}},
        {"$set": {"date": {"$dateToString": {"date": "$day", "format": "%Y-%m-%d"}},
            "present_count": {"$ifNull": ["$present_count", 0]}, "late_count": {"$ifNull": ["$late_count", 0]}}},
        {"$lookup": {"from": "employees", "let": {"d": "$date"},
            "pipeline": [{"$match": {"department": department, "$expr": {"$lte": ["$joined_on", "$$d"]}}}, {"$count": "count"}],
            "as": "hc"}},
        {"$set": {"headcount": {"$ifNull": [{"$arrayElemAt": ["$hc.count", 0]}, 0]},
            "is_working_day": {"$not": [{"$in": [{"$dayOfWeek": "$day"}, [1, 7]]}]}}},
        {"$set": {"attendance_rate": {"$cond": [{"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
            {"$round": [{"$divide": ["$present_count", "$headcount"]}, 4]}, None]}}},
        {"$setWindowFields": {"sortBy": {"day": 1}, "output": {"moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}}}}},
        {"$project": {"_id": 0, "date": 1, "is_working_day": 1, "headcount": 1, "present_count": 1, "late_count": 1, "attendance_rate": 1, "moving_avg_7d": {"$round": ["$moving_avg_7d", 4]}}},
        {"$sort": {"date": 1}}
    ]
    return {"department": department, "items": list(db.employees.aggregate(pipeline))}


def explain_query(endpoint, params):
    if endpoint == "attendance_list":
        q = {}
        if params.get("emp_code"):
            q["emp_code"] = params["emp_code"]
        if params.get("status"):
            q["status"] = params["status"]
        if params.get("date_from") or params.get("date_to"):
            q["date"] = {}
            if params.get("date_from"):
                q["date"]["$gte"] = params["date_from"]
            if params.get("date_to"):
                q["date"]["$lte"] = params["date_to"]

        return "attendance_logs", db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": q,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (params.get("page", 1) - 1) * params.get("page_size", 20),
                "limit": params.get("page_size", 20),
            },
            verbosity="executionStats",
        )

    if endpoint == "employee_monthly":
        if not params.get("emp_code") or not params.get("month"):
            raise HTTPException(422, "emp_code and month are required")
        start, end = parse_month(params["month"])
        pipe = [
            {"$match": monthly_log_match(params["emp_code"], start, end)},
            {"$group": {"_id": "$emp_code", "late": {"$sum": "$late_minutes"}}},
        ]
        return "attendance_logs", db.command(
            "explain",
            {"aggregate": "attendance_logs", "pipeline": pipe, "cursor": {}},
            verbosity="executionStats",
        )

    if endpoint == "department_summary":
        if not params.get("month"):
            raise HTTPException(422, "month is required")
        start, end = parse_month(params["month"])
        pipe = [
            {"$match": {"date": {"$gte": start.isoformat(), "$lte": end.isoformat()}}},
            {"$group": {"_id": "$emp_code", "late": {"$sum": "$late_minutes"}}},
        ]
        return "attendance_logs", db.command(
            "explain",
            {"aggregate": "attendance_logs", "pipeline": pipe, "cursor": {}},
            verbosity="executionStats",
        )

    if endpoint == "late_leaderboard":
        if not params.get("month"):
            raise HTTPException(422, "month is required")
        start, end = parse_month(params["month"])
        pipe = [
            {"$match": {"date": {"$gte": start.isoformat(), "$lte": end.isoformat()}, "late_minutes": {"$gt": 0}}},
            {"$group": {"_id": "$emp_code", "total": {"$sum": "$late_minutes"}}},
        ]
        return "attendance_logs", db.command(
            "explain",
            {"aggregate": "attendance_logs", "pipeline": pipe, "cursor": {}},
            verbosity="executionStats",
        )

    if endpoint == "department_trend":
        if not params.get("department") or not params.get("from") or not params.get("to"):
            raise HTTPException(422, "department, from and to are required")
        start, end = parse_date(params["from"]), parse_date(params["to"])
        pipe = [
            {"$match": {"date": {"$gte": start.isoformat(), "$lte": end.isoformat()}}},
            {"$group": {"_id": "$date", "n": {"$sum": 1}}},
        ]
        return "attendance_logs", db.command(
            "explain",
            {"aggregate": "attendance_logs", "pipeline": pipe, "cursor": {}},
            verbosity="executionStats",
        )

    raise HTTPException(422, "unsupported endpoint")


@app.get("/admin/explain/{endpoint}")
def admin_explain(
    endpoint: Literal["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"],
    emp_code: Optional[str] = None, month: Optional[str] = None, department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50), date_from: Optional[str] = None, date_to: Optional[str] = None,
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
    from_date: Optional[str] = Query(None, alias="from"), to_date: Optional[str] = Query(None, alias="to"),
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    params = locals()
    params["from"] = from_date
    params["to"] = to_date
    collection, explanation = explain_query(endpoint, params)
    return {"endpoint": endpoint, "collection": collection, "explain": json.loads(json_util.dumps(explanation))}
