from django.urls import path

from . import api

urlpatterns = [
    path("health/", api.health),
    path("auth/register/", api.register),
    path("auth/login/", api.login_view),
    path("auth/logout/", api.logout_view),
    path("auth/me/", api.me),
    path("auth/password/forgot/", api.password_forgot),
    path("auth/password/reset/", api.password_reset),
]
