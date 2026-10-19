from django.contrib import admin

from .models import Batch, Equipment, Item, Movement, Store


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ("name", "organisation", "kind", "farm", "is_active")
    list_filter = ("kind", "is_active")


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("name", "organisation", "category", "unit", "reorder_level", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("name", "active_ingredient")


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("name", "organisation", "kind", "meter", "is_active")


class ReadOnly(admin.ModelAdmin):
    """Stock records are changed only through the app's actions, so the history stays true."""
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Batch)
class BatchAdmin(ReadOnly):
    list_display = ("item", "store", "received_on", "qty_received", "qty_remaining", "unit_cost", "currency", "unit_cost_usd", "expiry")
    list_filter = ("currency",)
    search_fields = ("item__name", "supplier", "lot_number")


@admin.register(Movement)
class MovementAdmin(ReadOnly):
    list_display = ("date", "kind", "item", "store", "quantity", "value_usd", "field", "equipment", "created_by")
    list_filter = ("kind",)
    search_fields = ("item__name", "reason")
