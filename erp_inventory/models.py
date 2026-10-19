"""
Stock and fuel.

    Store       a place stock is kept: a storeroom, a shed, or a fuel tank / bowser.
    Item        something the farm keeps: seed, fertiliser, chemicals, diesel, spares...
    Batch       one delivery of an item into a store, with what it cost. Each batch keeps
                its cost in the currency paid AND in US dollars and ZiG at that day's rate.
    Movement    every change to stock, one line per batch touched. Never edited or deleted;
                a mistake is undone with a reversal (new lines with the opposite sign).
    Equipment   tractors, trucks, generators and pumps that use fuel.

Using stock takes the OLDEST batch first (FIFO: first in, first out), so the
cost of what was used is what that stock actually cost.
"""
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models

from erp_core.models import CropPlan, Farm, Field, Organisation
from murimi_common.choices import CURRENCIES


def uuid_pk():
    return models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)


QTY = dict(max_digits=14, decimal_places=3)


class Store(models.Model):
    STORE, TANK = "store", "tank"
    KIND_CHOICES = [(STORE, "Storeroom"), (TANK, "Fuel tank / bowser")]

    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="stores")
    farm = models.ForeignKey(Farm, null=True, blank=True, on_delete=models.SET_NULL, related_name="stores")
    name = models.CharField(max_length=80, help_text="e.g. Main store, Chemical shed, Diesel tank 1")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=STORE)
    capacity = models.DecimalField("Capacity (for tanks, litres)", null=True, blank=True, **QTY)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["organisation", "name"], name="erp_unique_store_name")]

    def __str__(self):
        return self.name


class Item(models.Model):
    SEED, FERTILISER, CHEMICAL, FUEL, FEED, SPARES, PACKAGING, OTHER = (
        "seed", "fertiliser", "chemical", "fuel", "feed", "spares", "packaging", "other")
    CATEGORY_CHOICES = [
        (SEED, "Seed"), (FERTILISER, "Fertiliser"), (CHEMICAL, "Chemicals"), (FUEL, "Fuel and oil"),
        (FEED, "Animal feed"), (SPARES, "Spares and tools"), (PACKAGING, "Packaging"), (OTHER, "Other"),
    ]
    UNITS = ["kg", "g", "tonne", "L", "mL", "50 kg bag", "25 kg bag", "10 kg bag", "5 L can", "20 L can",
             "each", "box", "roll", "pack"]

    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=120, help_text="e.g. Compound D, Ammonium Nitrate, SC 727 seed, Diesel")
    category = models.CharField(max_length=12, choices=CATEGORY_CHOICES)
    unit = models.CharField(max_length=20, choices=[(u, u) for u in UNITS])
    reorder_level = models.DecimalField("Warn me when stock falls to", null=True, blank=True, **QTY)
    active_ingredient = models.CharField(max_length=120, blank=True)
    reentry_hours = models.PositiveIntegerField("Re-entry interval (hours)", null=True, blank=True,
                                                help_text="How long people must stay out of a sprayed field.")
    preharvest_days = models.PositiveIntegerField("Pre-harvest interval (days)", null=True, blank=True,
                                                  help_text="Days between the last spray and harvest.")
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category", "name"]
        constraints = [models.UniqueConstraint(fields=["organisation", "name"], name="erp_unique_item_name")]

    def __str__(self):
        return f"{self.name} ({self.unit})"


class Equipment(models.Model):
    HOURS, KM, NONE = "hours", "km", "none"
    METER_CHOICES = [(HOURS, "Hour meter"), (KM, "Odometer (km)"), (NONE, "No meter")]
    KIND_CHOICES = [("tractor", "Tractor"), ("truck", "Truck"), ("bakkie", "Bakkie / pickup"),
                    ("generator", "Generator"), ("pump", "Water pump"), ("motorbike", "Motorbike"), ("other", "Other")]

    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="equipment")
    name = models.CharField(max_length=80, help_text="e.g. Tractor 2 (MF 375)")
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default="tractor")
    registration = models.CharField(max_length=30, blank=True)
    meter = models.CharField(max_length=6, choices=METER_CHOICES, default=HOURS)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["organisation", "name"], name="erp_unique_equipment_name")]

    def __str__(self):
        return self.name


class Batch(models.Model):
    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="batches")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="batches")
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="batches")
    received_on = models.DateField()
    supplier = models.CharField(max_length=120, blank=True)
    lot_number = models.CharField(max_length=60, blank=True)
    expiry = models.DateField(null=True, blank=True)
    qty_received = models.DecimalField(**QTY)
    qty_remaining = models.DecimalField(**QTY)
    # What one unit cost, in the currency it was paid in...
    unit_cost = models.DecimalField(max_digits=16, decimal_places=4)
    currency = models.CharField(max_length=3, choices=CURRENCIES)
    # ...and the same cost in US dollars and ZiG, at the rates on the day it arrived.
    unit_cost_usd = models.DecimalField(max_digits=18, decimal_places=6)
    unit_cost_zwg = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    rate_zwg = models.DecimalField("ZiG per USD used", max_digits=18, decimal_places=6, null=True, blank=True)
    rate_other = models.DecimalField("Other currency per USD used", max_digits=18, decimal_places=6, null=True, blank=True)
    source_batch = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                     help_text="Set when this batch came from a transfer between stores.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["received_on", "created_at"]
        constraints = [models.CheckConstraint(condition=models.Q(qty_remaining__gte=0), name="erp_batch_not_negative")]

    def __str__(self):
        return f"{self.item.name} received {self.received_on} ({self.qty_remaining}/{self.qty_received})"


class Movement(models.Model):
    RECEIPT, ISSUE, TRANSFER_OUT, TRANSFER_IN, ADJUST_IN, ADJUST_OUT = (
        "receipt", "issue", "transfer_out", "transfer_in", "adjust_in", "adjust_out")
    KIND_CHOICES = [(RECEIPT, "Received"), (ISSUE, "Used / issued"), (TRANSFER_OUT, "Moved out"),
                    (TRANSFER_IN, "Moved in"), (ADJUST_IN, "Stock take: more than expected"),
                    (ADJUST_OUT, "Stock take: less than expected")]

    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="movements")
    group = models.UUIDField(db_index=True, help_text="All lines made by one action share a group (one action can touch several batches).")
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    date = models.DateField()
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="movements")
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="movements")
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="movements")
    quantity = models.DecimalField(help_text="Positive = into the store, negative = out of it.", **QTY)
    value_usd = models.DecimalField(max_digits=16, decimal_places=2)
    value_zwg = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    field = models.ForeignKey(Field, null=True, blank=True, on_delete=models.PROTECT, related_name="stock_used")
    crop_plan = models.ForeignKey(CropPlan, null=True, blank=True, on_delete=models.SET_NULL, related_name="stock_used")
    equipment = models.ForeignKey(Equipment, null=True, blank=True, on_delete=models.PROTECT, related_name="fuel_used")
    meter_reading = models.DecimalField(max_digits=12, decimal_places=1, null=True, blank=True)
    rate_per_ha = models.DecimalField("Rate per hectare", null=True, blank=True, **QTY)
    reason = models.CharField(max_length=200, blank=True)
    reversal_of = models.UUIDField(null=True, blank=True, db_index=True,
                                   help_text="Group id of the action this line undoes.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["organisation", "item", "store"]), models.Index(fields=["organisation", "date"])]

    def __str__(self):
        return f"{self.get_kind_display()} {self.quantity} {self.item.unit} {self.item.name} ({self.date})"
