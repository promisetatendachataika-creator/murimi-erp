from django.contrib import admin
from django.urls import include, path, re_path

from web.views import app, assetlinks, service_worker

admin.site.site_header = "Murimi Farm ERP: staff"
admin.site.site_title = "Murimi Farm ERP"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("accounts.urls")),
    path("api/erp/", include("erp_core.urls")),
    path("api/erp/", include("erp_finance.urls")),
    path("api/erp/", include("erp_inventory.urls")),
    path("api/erp/", include("erp_labour.urls")),
    path("api/erp/", include("erp_trade.urls")),
    path("sw.js", service_worker),
    path(".well-known/assetlinks.json", assetlinks),
    path("", app),
    re_path(r"^app/.*$", app),
]
