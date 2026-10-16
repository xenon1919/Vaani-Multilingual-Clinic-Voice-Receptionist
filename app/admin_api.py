"""Read-only JSON endpoints backing the admin dashboard and the patient
booking-lookup page. Admin routes are gated by a simple shared password
(HTTP Basic) -- not a real auth system, see docs/privacy.md's "Known
gaps". Lookup is intentionally open: it only ever returns data for the
exact phone number the caller provides, the same pattern as looking up an
order by order number. Known gap, also in docs/privacy.md: no OTP/identity
verification before showing that data.
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings
from app.db import get_pool
from tools.validation import ToolValidationError, normalize_phone

router = APIRouter(prefix="/api")
security = HTTPBasic()


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    if not settings.admin_password:
        raise HTTPException(503, "Admin dashboard not configured (set ADMIN_PASSWORD in .env)")
    correct_username = secrets.compare_digest(credentials.username, "admin")
    correct_password = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (correct_username and correct_password):
        raise HTTPException(401, "Invalid admin credentials", headers={"WWW-Authenticate": "Basic"})


@router.get("/appointments")
async def list_appointments(
    _: None = Depends(require_admin),
    date: str | None = Query(None),
    doctor_id: int | None = None,
    status: str | None = None,
):
    pool = get_pool()
    query = """
        SELECT a.id, a.patient_name, a.phone_number, a.status, a.created_at,
               s.slot_date, s.slot_time, d.id AS doctor_id, d.name AS doctor_name
        FROM appointments a
        JOIN slots s ON s.id = a.slot_id
        JOIN doctors d ON d.id = s.doctor_id
        WHERE 1=1
    """
    params: list = []
    if date:
        params.append(date)
        query += f" AND s.slot_date = ${len(params)}"
    if doctor_id:
        params.append(doctor_id)
        query += f" AND d.id = ${len(params)}"
    if status:
        params.append(status)
        query += f" AND a.status = ${len(params)}"
    query += " ORDER BY s.slot_date, s.slot_time"
    rows = await pool.fetch(query, *params)
    return [dict(r) for r in rows]


@router.get("/doctors")
async def list_doctors(_: None = Depends(require_admin)):
    pool = get_pool()
    rows = await pool.fetch("SELECT id, name, specialty FROM doctors ORDER BY name")
    return [dict(r) for r in rows]


@router.get("/calls")
async def list_calls(_: None = Depends(require_admin), limit: int = 50):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT call_id, started_at, ended_at, caller_language, outcome FROM call_logs ORDER BY started_at DESC LIMIT $1",
        limit,
    )
    return [dict(r) for r in rows]


@router.get("/lookup")
async def lookup_by_phone(phone: str):
    try:
        normalized = normalize_phone(phone)
    except ToolValidationError:
        raise HTTPException(400, "That doesn't look like a valid 10-digit phone number.")
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT a.patient_name, a.status, s.slot_date, s.slot_time, d.name AS doctor_name
        FROM appointments a
        JOIN slots s ON s.id = a.slot_id
        JOIN doctors d ON d.id = s.doctor_id
        WHERE a.phone_number = $1
        ORDER BY s.slot_date DESC, s.slot_time DESC
        """,
        normalized,
    )
    return [dict(r) for r in rows]
