"""
Phone numbers and usernames: the SAME rules as Murimi Intelligent Hub
(accounts/models.py there). Every Murimi module identifies people this way,
so the three systems can be joined by matching phone numbers.
"""
import re

from django.core.exceptions import ValidationError

PHONE_DIGITS = re.compile(r"\D")
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_.]{2,29}$")
RESERVED_USERNAMES = {"admin", "administrator", "murimi", "staff", "support", "help", "root", "api"}


def normalize_phone(raw):
    """'077 123 4567', '0771234567', '771234567', '263771234567', '+263 077 123 4567' and
    '+263 77 123 4567' all become '+263771234567'."""
    raw = (raw or "").strip()
    if not raw:
        raise ValidationError("Enter a WhatsApp number.")
    has_plus = raw.startswith("+")
    digits = PHONE_DIGITS.sub("", raw)
    if not has_plus:
        if digits.startswith("00"):
            digits = digits[2:]
        elif digits.startswith("0") and len(digits) == 10:
            digits = "263" + digits[1:]
        elif len(digits) == 9 and digits[0] == "7":
            digits = "263" + digits  # 77 123 4567 typed without the 0
    if digits.startswith("2630"):
        digits = "263" + digits[4:]  # +263 077... : the 0 is dropped after the country code
    if digits.startswith("0"):
        raise ValidationError("Start with 0 and the number, e.g. 077 123 4567, or use +263.")
    if digits.startswith("263") and len(digits) != 12:
        raise ValidationError("A Zimbabwe number has 10 digits, e.g. 077 123 4567 or +263 77 123 4567.")
    if not (9 <= len(digits) <= 15):
        raise ValidationError("Enter a full number with the country code, e.g. +263 77 123 4567.")
    return "+" + digits


def normalize_username(raw):
    name = (raw or "").strip().lower().lstrip("@")
    if not USERNAME_RE.match(name):
        raise ValidationError("Use 3–30 letters, numbers, _ or ., starting with a letter (e.g. tendai_moyo).")
    if name in RESERVED_USERNAMES:
        raise ValidationError("That username is reserved. Please choose another.")
    return name


def looks_like_username(identifier):
    return any(ch.isalpha() for ch in (identifier or ""))
