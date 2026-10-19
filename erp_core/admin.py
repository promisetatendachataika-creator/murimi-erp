"""Staff view of every organisation. Day-to-day work happens in the web app, not here."""
from django.contrib import admin

from .models import AuditEvent, CropPlan, Farm, Field, Membership, Organisation, Season


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ("name", "province", "district", "reporting_currency", "is_active", "created_at")
    list_filter = ("province", "is_active")
    search_fields = ("name", "trading_name", "tin", "vat_number")
    inlines = [MembershipInline]


@admin.register(Farm)
class FarmAdmin(admin.ModelAdmin):
    list_display = ("name", "organisation", "province", "district", "size_ha", "is_active")
    list_filter = ("province", "is_active")
    search_fields = ("name", "organisation__name")


@admin.register(Field)
class FieldAdmin(admin.ModelAdmin):
    list_display = ("name", "farm", "size_ha", "irrigated", "is_active")
    list_filter = ("irrigated", "is_active")
    search_fields = ("name", "farm__name")


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ("name", "organisation", "start_date", "end_date", "status")
    list_filter = ("status",)


@admin.register(CropPlan)
class CropPlanAdmin(admin.ModelAdmin):
    list_display = ("crop", "field", "season", "planted_ha", "status")
    list_filter = ("crop", "status")


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("at", "organisation", "user", "action", "summary")
    list_filter = ("action", "object_type")
    search_fields = ("summary",)
    readonly_fields = [f.name for f in AuditEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
