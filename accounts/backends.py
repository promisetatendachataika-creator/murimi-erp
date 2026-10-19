"""Log in with EITHER a WhatsApp number OR a username (same as the Hub)."""
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import ValidationError

from murimi_common.identity import looks_like_username, normalize_phone

from .models import User


def find_user(identifier):
    identifier = (identifier or "").strip()
    try:
        if looks_like_username(identifier):
            return User.objects.get(username=identifier.lower().lstrip("@"))
        return User.objects.get(phone=normalize_phone(identifier))
    except (User.DoesNotExist, ValidationError):
        return None


class PhoneOrUsernameBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        user = find_user(username or kwargs.get("phone"))
        if user is None:
            User().set_password(password)  # same work either way, so timing doesn't reveal accounts
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
