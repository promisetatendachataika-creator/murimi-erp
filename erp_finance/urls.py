from django.urls import path

from . import api

O = "orgs/<str:org_id>/"
urlpatterns = [
    path(O + "rates/", api.rates),
    path(O + "rates/latest/", api.latest),
    path(O + "rates/fetch/", api.fetch_now),
    path(O + "rates/<str:pk>/", api.rate),
    path(O + "convert/", api.convert_view),
]
