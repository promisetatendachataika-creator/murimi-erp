from django.urls import path

from . import api

O = "orgs/<str:org_id>/"
urlpatterns = [
    path("lists/", api.lists),
    path("orgs/", api.organisations),
    path(O, api.organisation),
    path(O + "summary/", api.summary),
    path(O + "members/", api.members),
    path(O + "members/<str:pk>/", api.member),
    path(O + "members/<str:pk>/reset-password/", api.member_reset_password),
    path(O + "farms/", api.farms_res.collection_view()),
    path(O + "farms/<str:pk>/", api.farms_res.item_view()),
    path(O + "fields/", api.fields_res.collection_view()),
    path(O + "fields/<str:pk>/", api.fields_res.item_view()),
    path(O + "seasons/", api.seasons_res.collection_view()),
    path(O + "seasons/<str:pk>/", api.seasons_res.item_view()),
    path(O + "crop-plans/", api.plans_res.collection_view()),
    path(O + "crop-plans/<str:pk>/", api.plans_res.item_view()),
    path(O + "audit/", api.audit_log),
]
