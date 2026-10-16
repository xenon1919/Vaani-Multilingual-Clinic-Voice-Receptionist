"""Static clinic facts (hours/location — same ones baked into the system
prompt, kept here too so get_clinic_info can return them as structured
data) and doctor name lookup against the DB (the source of truth for
doctors/fees, shared with db/seed.py)."""
import re

import asyncpg

from tools.validation import ToolValidationError

CLINIC_NAME = "Aarogya Clinic"
CLINIC_HOURS = "Monday to Saturday, 9:00 AM-1:00 PM and 2:00 PM-6:00 PM. Closed Sundays."
CLINIC_LOCATION = "12-3-45 Main Road, Vijayawada, Andhra Pradesh"

_DOCTOR_PREFIX_RE = re.compile(r"^\s*(dr\.?|doctor)\s+", re.IGNORECASE)


async def find_doctor(conn: asyncpg.Connection, doctor_name: str) -> asyncpg.Record:
    """Case-insensitive partial match on doctor name. Raises
    ToolValidationError(doctor_not_found) listing valid names if no match."""
    cleaned = _DOCTOR_PREFIX_RE.sub("", doctor_name).strip()
    row = await conn.fetchrow(
        "SELECT id, name, specialty, consultation_fee FROM doctors WHERE name ILIKE $1 ORDER BY id LIMIT 1",
        f"%{cleaned}%",
    )
    if row is None:
        all_doctors = await conn.fetch("SELECT name FROM doctors ORDER BY id")
        names = [d["name"] for d in all_doctors]
        raise ToolValidationError(
            "doctor_not_found",
            f"No doctor matching '{doctor_name}'. Available doctors: {', '.join(names)}.",
        )
    return row
