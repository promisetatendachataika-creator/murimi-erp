from django.urls import path

from . import api

O = "orgs/<str:org_id>/"
urlpatterns = [
    path(O + "stock/options/", api.options),
    path(O + "stores/", api.stores_res.collection_view()),
    path(O + "stores/<str:pk>/", api.stores_res.item_view()),
    path(O + "items/", api.items_res.collection_view()),
    path(O + "items/<str:pk>/", api.items_res.item_view()),
    path(O + "equipment/", api.equipment_res.collection_view()),
    path(O + "equipment/<str:pk>/", api.equipment_res.item_view()),
    path(O + "stock/", api.on_hand),
    path(O + "stock/batches/", api.batches),
    path(O + "stock/movements/", api.movements),
    path(O + "stock/receive/", api.receive),
    path(O + "stock/issue/", api.issue),
    path(O + "stock/transfer/", api.transfer),
    path(O + "stock/count/", api.stock_take),
    path(O + "stock/movements/<str:group>/undo/", api.reverse),
    path(O + "stock/alerts/", api.alerts),
    path(O + "stock/plan-costs/", api.plan_costs),
    path(O + "fuel/", api.fuel),
]
