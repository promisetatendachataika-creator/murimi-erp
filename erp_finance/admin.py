from django.contrib import admin

from .models import ExchangeRate


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ("date", "currency", "units_per_usd", "source", "organisation", "created_by")
    list_filter = ("currency", "source", ("organisation", admin.EmptyFieldListFilter))
    date_hierarchy = "date"
    fields = ("date", "currency", "units_per_usd", "source", "note", "organisation")

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
