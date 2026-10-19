from django.urls import path

from . import api

O = "orgs/<str:org_id>/trade/"
urlpatterns = [
    path(O + "options/", api.options),
    path(O + "contacts/", api.contacts_res.collection_view()),
    path(O + "contacts/<str:pk>/", api.contacts_res.item_view()),
    path(O + "documents/", api.documents),
    path(O + "documents/<str:pk>/", api.document),
    path(O + "documents/<str:pk>/payments/", api.payments),
    path(O + "documents/<str:pk>/<str:action>/", api.document_action),
    path(O + "payments/<str:pk>/", api.payment),
    path(O + "harvests/", api.harvests),
    path(O + "harvests/<str:pk>/", api.harvest),
    path(O + "reports/vat/", api.vat_report),
    path(O + "reports/crops/", api.crops_report),
    path(O + "reports/profit/", api.pnl_report),
    path(O + "reports/owing/", api.owing_report),
]
