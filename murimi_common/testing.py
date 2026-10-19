"""Helpers for tests: a JSON client that sends the CSRF token like the web app does."""
import json

from django.core.cache import cache
from django.test import Client

from accounts.models import User


class ApiClient:
    def __init__(self, user=None, password="harvest-time-2026"):
        cache.clear()
        self.c = Client(enforce_csrf_checks=True)
        self.c.get("/api/health/")
        if user is not None:
            r = self.post("/api/auth/login/", {"login": user.phone, "password": password})
            assert r.status_code == 200, r.content

    def _send(self, method, url, data=None):
        token = self.c.cookies["csrftoken"].value
        return getattr(self.c, method)(url, data=json.dumps(data or {}), content_type="application/json",
                                       HTTP_X_CSRFTOKEN=token)

    def get(self, url, params=None):
        return self.c.get(url, params or {})

    def post(self, url, data=None):
        return self._send("post", url, data)

    def patch(self, url, data=None):
        return self._send("patch", url, data)

    def delete(self, url):
        return self._send("delete", url)


def make_user(phone, name, password="harvest-time-2026", **extra):
    return User.objects.create_user(phone=phone, password=password, full_name=name, **extra)
