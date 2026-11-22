"""
Single source of truth for validating numbers, dates and money.

Every entry point calls these, so the rules are enforced on the server whatever the
browser sends. Forms may also use them for early feedback, but the services are the
authority: a value refused here is refused everywhere.

Each function raises ValidationError with a message a non-technical user can act on.
"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone

MAX_MONEY = Decimal("10000000.00")       # KES 10 million per line: well above any real charge
MIN_YEAR = 1900                          # no living patient is older than this
MAX_AGE_YEARS = 130


class ValidationError(ValueError):
    """Raised with a message meant for the user."""


def _label_error(label, text):
    return ValidationError(f"{label}: {text}")


def parse_decimal(raw, label, *, minimum=None, maximum=None, places=None):
    """Parse a decimal from user input. Refuses blanks, text, NaN/Infinity and out-of-range values."""
    if raw is None or str(raw).strip() == "":
        raise _label_error(label, "enter a number.")
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise _label_error(label, f"'{raw}' is not a number.")
    if not value.is_finite():
        raise _label_error(label, "must be a real number.")
    if places is not None and value.as_tuple().exponent < -places:
        raise _label_error(label, f"use at most {places} decimal place(s).")
    if minimum is not None and value < minimum:
        raise _label_error(label, f"must be {minimum} or more.")
    if maximum is not None and value > maximum:
        raise _label_error(label, f"must be {maximum} or less.")
    return value


def parse_int(raw, label, *, minimum=None, maximum=None):
    if raw is None or str(raw).strip() == "":
        raise _label_error(label, "enter a whole number.")
    text = str(raw).strip()
    if not text.lstrip("-").isdigit():
        raise _label_error(label, f"'{raw}' is not a whole number.")
    value = int(text)
    if minimum is not None and value < minimum:
        raise _label_error(label, f"must be {minimum} or more.")
    if maximum is not None and value > maximum:
        raise _label_error(label, f"must be {maximum} or less.")
    return value


def optional_int(raw, label, **kw):
    if raw is None or str(raw).strip() == "":
        return None
    return parse_int(raw, label, **kw)


def optional_decimal(raw, label, **kw):
    if raw is None or str(raw).strip() == "":
        return None
    return parse_decimal(raw, label, **kw)


def money(raw, label, *, allow_zero=False):
    """A money amount: positive (zero only if allowed), at most MAX_MONEY, at most 2 decimal places."""
    minimum = Decimal("0.00") if allow_zero else Decimal("0.01")
    return parse_decimal(raw, label, minimum=minimum, maximum=MAX_MONEY, places=2)


def quantity(raw, label, *, maximum=Decimal("100000")):
    return parse_decimal(raw, label, minimum=Decimal("0.001"), maximum=maximum, places=3)


def past_date(value, label, *, allow_today=True, min_year=MIN_YEAR):
    """A date that has already happened (birth dates, last period, historic events)."""
    if value is None:
        raise _label_error(label, "enter a date.")
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value.strip())
        except ValueError:
            raise _label_error(label, "use a valid date.")
    today = timezone.localdate()
    if value.year < min_year:
        raise _label_error(label, f"the year must be {min_year} or later.")
    if value > today or (value == today and not allow_today):
        raise _label_error(label, "cannot be in the future.")
    return value


def birth_date(value, label="Date of birth"):
    """A date of birth: not in the future, not older than MAX_AGE_YEARS."""
    value = past_date(value, label)
    today = timezone.localdate()
    oldest = today.replace(year=today.year - MAX_AGE_YEARS)
    if value < oldest:
        raise _label_error(label, f"no one is recorded as older than {MAX_AGE_YEARS} years.")
    return value


def not_future(value, label):
    """A date-time that cannot be after now (birth time, arrival time)."""
    if value is None:
        raise _label_error(label, "enter a date and time.")
    if value > timezone.now() + timedelta(minutes=5):
        raise _label_error(label, "cannot be in the future.")
    return value


def in_range(value, label, low, high):
    """A measured value that must fall in a physiological or clinical range."""
    if value is None:
        return None
    if not (Decimal(str(low)) <= Decimal(str(value)) <= Decimal(str(high))):
        raise _label_error(label, f"must be between {low} and {high}. Check the entry.")
    return value


def text(raw, label, *, max_length, required=False):
    """Free text that is stored in a fixed-width column. Too long is refused, never cut short silently."""
    value = (raw or "").strip()
    if required and not value:
        raise _label_error(label, "this is required.")
    if len(value) > max_length:
        raise _label_error(label, f"at most {max_length} characters (you entered {len(value)}).")
    return value
