"""
Checking values that arrive from the browser. Each function returns the clean
value or adds a friendly message to `errors[key]`. Money and areas are always
Decimal, never float: 0.1 + 0.2 must equal 0.3 when money is involved.
"""
import datetime
import uuid
from decimal import Decimal, InvalidOperation

MISSING = object()


def text(data, key, errors, required=False, max_len=120, label="this"):
    v = str(data.get(key) or "").strip()
    if required and not v:
        errors[key] = f"Please fill in {label}."
    if len(v) > max_len:
        errors[key] = f"Keep this under {max_len} characters."
    return v[:max_len]


def choice(data, key, errors, options, required=True, default=""):
    v = data.get(key, default)
    if v in (None, ""):
        if required:
            errors[key] = "Please choose one."
        return default
    if v not in options:
        errors[key] = "Please choose one of the options."
        return default
    return v


def decimal(data, key, errors, required=False, min_value=None, max_value=Decimal("1000000000"),
            places=2, positive=False):
    raw = data.get(key)
    if raw in (None, ""):
        if required:
            errors[key] = "Please enter a number."
        return None
    try:
        v = Decimal(str(raw).replace(",", "").strip())
        if not v.is_finite():
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        errors[key] = "Enter a number, e.g. 12.5"
        return None
    if positive and v <= 0:
        errors[key] = "Enter a number above zero."
    elif min_value is not None and v < min_value:
        errors[key] = f"The smallest allowed is {min_value}."
    elif max_value is not None and v > max_value:
        errors[key] = "That number is too big."
    if -v.as_tuple().exponent > places:
        errors[key] = f"Use at most {places} decimal places."
    return v


def date(data, key, errors, required=False):
    raw = data.get(key)
    if raw in (None, ""):
        if required:
            errors[key] = "Please pick a date."
        return None
    try:
        return datetime.date.fromisoformat(str(raw)[:10])
    except ValueError:
        errors[key] = "Use a date like 2026-11-15."
        return None


def boolean(data, key, default=False):
    v = data.get(key, default)
    return v is True or str(v).lower() in ("1", "true", "yes", "on")


def uuid_value(raw):
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError, AttributeError):
        return None
