from datetime import date, timedelta

import pytest

from tools.validation import ToolValidationError, normalize_phone, parse_date, parse_time


def test_normalize_phone_plain_10_digits():
    assert normalize_phone("9876543210") == "9876543210"


def test_normalize_phone_with_spaces():
    assert normalize_phone("98 7654 3210") == "9876543210"


def test_normalize_phone_with_country_code():
    assert normalize_phone("+91 9876543210") == "9876543210"


def test_normalize_phone_with_leading_zero():
    assert normalize_phone("09876543210") == "9876543210"


def test_normalize_phone_invalid_too_short():
    with pytest.raises(ToolValidationError) as exc:
        normalize_phone("12345")
    assert exc.value.error_code == "invalid_phone"


def test_normalize_phone_invalid_bad_leading_digit():
    with pytest.raises(ToolValidationError) as exc:
        normalize_phone("1234567890")  # must start 6-9
    assert exc.value.error_code == "invalid_phone"


def test_parse_date_valid_future():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert parse_date(tomorrow) == date.today() + timedelta(days=1)


def test_parse_date_past_rejected():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with pytest.raises(ToolValidationError) as exc:
        parse_date(yesterday)
    assert exc.value.error_code == "past_date"


def test_parse_date_bad_format():
    with pytest.raises(ToolValidationError) as exc:
        parse_date("tomorrow")
    assert exc.value.error_code == "invalid_date"


def test_parse_time_valid():
    assert parse_time("16:00").hour == 16


def test_parse_time_bad_format():
    with pytest.raises(ToolValidationError) as exc:
        parse_time("4pm")
    assert exc.value.error_code == "invalid_time"
