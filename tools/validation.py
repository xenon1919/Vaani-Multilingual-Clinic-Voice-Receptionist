"""Deterministic validation the model must never do itself: phone format,
date/time parsing, and past-date checks. Raises ToolValidationError with a
structured error code the model can react to (e.g. ask for a different
date instead of just failing)."""
import re
from datetime import date, time, datetime


class ToolValidationError(Exception):
    def __init__(self, error_code: str, message: str, data: dict | None = None):
        self.error_code = error_code
        self.message = message
        self.data = data or {}
        super().__init__(message)


_PHONE_DIGITS_RE = re.compile(r"\D")
_VALID_INDIAN_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")


def normalize_phone(raw: str) -> str:
    """Normalizes a spoken/typed Indian mobile number to 10 bare digits.
    Raises ToolValidationError(invalid_phone) if it isn't a valid number."""
    digits = _PHONE_DIGITS_RE.sub("", raw)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if not _VALID_INDIAN_MOBILE_RE.match(digits):
        raise ToolValidationError(
            "invalid_phone", f"'{raw}' is not a valid 10-digit Indian mobile number."
        )
    return digits


def parse_date(raw: str) -> date:
    """Parses a strict ISO date (YYYY-MM-DD). The model resolves relative
    terms ('tomorrow', 'repu') itself and must pass the resolved ISO date —
    this only validates format and business rules."""
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        raise ToolValidationError("invalid_date", f"'{raw}' is not a valid date (expected YYYY-MM-DD).")
    if parsed < datetime.now().date():
        raise ToolValidationError("past_date", f"{raw} is in the past; appointments must be in the future.")
    return parsed


def parse_time(raw: str) -> time:
    """Parses a strict 24-hour HH:MM time."""
    try:
        return time.fromisoformat(raw)
    except ValueError:
        raise ToolValidationError("invalid_time", f"'{raw}' is not a valid time (expected 24-hour HH:MM).")
