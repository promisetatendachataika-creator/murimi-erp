from django.urls import path

from . import api

O = "orgs/<str:org_id>/labour/"
urlpatterns = [
    path(O + "options/", api.options),
    path(O + "summary/", api.summary),
    path(O + "workers/", api.workers),
    path(O + "workers/<str:pk>/", api.workers),
    path(O + "tasks/", api.tasks_res.collection_view()),
    path(O + "tasks/<str:pk>/", api.tasks_res.item_view()),
    path(O + "work/", api.work),
    path(O + "work/<str:pk>/", api.work_item),
    path(O + "advances/", api.advances),
    path(O + "advances/<str:pk>/", api.advance_item),
    path(O + "pay-runs/", api.pay_runs),
    path(O + "pay-runs/<str:pk>/", api.pay_run),
    path(O + "pay-runs/<str:pk>/csv/", api.pay_run_csv),
    path(O + "pay-runs/<str:pk>/<str:action>/", api.pay_run_action),
    path(O + "payslips/<str:pk>/", api.payslip),
    path(O + "rules/", api.rules),
]
