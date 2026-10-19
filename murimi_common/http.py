"""
Small helpers every JSON view uses. Same error format as the Hub:
    {"error": "One sentence for a person", "errors": {"field": "what to fix"}}
"""
import json
from functools import wraps

from django.core.cache import cache
from django.http import JsonResponse


def body(request):
    try:
        data = json.loads(request.body or b"{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def error(message, status=400, errors=None, **extra):
    payload = {"error": message, **extra}
    if errors:
        payload["errors"] = errors
    return JsonResponse(payload, status=status)


def login_required_json(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return error("Please log in.", status=401)
        return view(request, *args, **kwargs)
    return wrapped


def client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR", "")


def too_many(key, limit, window_seconds):
    """Count one attempt for `key`; True once `limit` is passed within the window."""
    if cache.add(key, 1, window_seconds):
        return False
    try:
        return cache.incr(key) > limit
    except ValueError:
        cache.set(key, 1, window_seconds)
        return False


def reset_counter(key):
    cache.delete(key)
