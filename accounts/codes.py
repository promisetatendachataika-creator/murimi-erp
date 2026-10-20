"""
Making and checking the 6-digit codes.

Rules (so nobody can spam people or guess codes):
  - a code works for 10 minutes and only once;
  - 5 wrong tries and that code stops working;
  - wait 60 seconds before asking for another code, and at most 5 codes an hour.
"""
import secrets
from datetime import timedelta

from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from . import sender
from .models import PhoneCode

CODE_MINUTES = 10
MAX_WRONG = 5
RESEND_SECONDS = 60
MAX_PER_HOUR = 5


class CodeError(Exception):
    def __init__(self, message, status=400, retry_in=None):
        super().__init__(message)
        self.message, self.status, self.retry_in = message, status, retry_in


def _fingerprint(code, phone, purpose):
    return salted_hmac("murimi.phonecode", f"{purpose}:{phone}:{code}").hexdigest()


def issue(user, purpose):
    """Create a code, send it to the person's number and return it (for test mode)."""
    now = timezone.now()
    recent = PhoneCode.objects.filter(user=user, created_at__gte=now - timedelta(hours=1))
    last = recent.filter(purpose=purpose).first()
    if last:
        waited = (now - last.created_at).total_seconds()
        if waited < RESEND_SECONDS:
            wait = int(RESEND_SECONDS - waited) + 1
            raise CodeError(f"Please wait {wait} seconds before asking for another code.", 429, retry_in=wait)
    if recent.count() >= MAX_PER_HOUR:
        raise CodeError("You've asked for a lot of codes. Please try again in an hour.", 429)

    # Only the newest code works.
    PhoneCode.objects.filter(user=user, purpose=purpose, used_at__isnull=True, expires_at__gt=now) \
        .update(expires_at=now)
    code = f"{secrets.randbelow(10 ** 6):06d}"
    record = PhoneCode.objects.create(
        user=user, phone=user.phone, purpose=purpose,
        code_hash=_fingerprint(code, user.phone, purpose),
        expires_at=now + timedelta(minutes=CODE_MINUTES))
    try:
        sender.send_code(user.phone, code, purpose)
    except sender.SendError as e:
        record.delete()
        raise CodeError(str(e), 503)
    return code


def check(user, purpose, code):
    """Raise CodeError unless `code` is the person's current, unused code."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    now = timezone.now()
    record = PhoneCode.objects.filter(user=user, purpose=purpose, used_at__isnull=True).first()
    if record is None or record.expires_at <= now or record.phone != user.phone:
        raise CodeError("That code has expired. Ask for a new one.")
    if record.attempts >= MAX_WRONG:
        raise CodeError("Too many wrong tries. Ask for a new code.")
    if len(code) != 6 or not constant_time_compare(record.code_hash, _fingerprint(code, user.phone, purpose)):
        record.attempts += 1
        record.save(update_fields=["attempts"])
        left = MAX_WRONG - record.attempts
        if left <= 0:
            raise CodeError("Too many wrong tries. Ask for a new code.")
        raise CodeError(f"That code isn't right. You have {left} {'try' if left == 1 else 'tries'} left.")
    record.used_at = now
    record.save(update_fields=["used_at"])


def mask(phone):
    """+263771234567 -> +263 ••• ••4567"""
    return f"{phone[:4]} ••• ••{phone[-4:]}" if len(phone) > 8 else phone
