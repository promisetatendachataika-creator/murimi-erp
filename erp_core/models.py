"""
The backbone of Murimi Farm ERP.

    Organisation  the farm business that pays for Murimi (the "tenant").
      Membership  a person's role in that organisation (owner, manager, ...).
      Farm        a property the organisation runs.
        Field     a block or land on that farm, with its size in hectares.
      Season      a growing season, e.g. "2026/27 Summer".
        CropPlan  what is grown on which field in which season, with a budget.
      AuditEvent  who changed what, and when. Never edited, never deleted.

Every table carries `organisation`, and every query is filtered by it, so one
farm business can never see another's records. All ids are UUIDs: they can be
created on a phone while offline, and they won't clash when the Murimi
systems are joined into one database.
"""
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum

from murimi_common.choices import CROPS, CURRENCIES, PROVINCES


def uuid_pk():
    return models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)


class Organisation(models.Model):
    id = uuid_pk()
    name = models.CharField(max_length=120, help_text="e.g. Moyo Family Farms (Pvt) Ltd")
    trading_name = models.CharField(max_length=120, blank=True)
    province = models.CharField(max_length=40, choices=[(p, p) for p in PROVINCES], blank=True)
    district = models.CharField(max_length=80, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    tin = models.CharField("ZIMRA TIN / BP number", max_length=30, blank=True)
    vat_number = models.CharField("VAT number", max_length=30, blank=True, help_text="Leave empty if not VAT-registered.")
    reporting_currency = models.CharField(max_length=3, choices=CURRENCIES, default="USD")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Membership(models.Model):
    OWNER, MANAGER, ACCOUNTANT, STOREKEEPER, SUPERVISOR, VIEWER = (
        "owner", "manager", "accountant", "storekeeper", "supervisor", "viewer")
    ROLE_CHOICES = [
        (OWNER, "Owner"),
        (MANAGER, "Farm manager"),
        (ACCOUNTANT, "Accountant"),
        (STOREKEEPER, "Storekeeper"),
        (SUPERVISOR, "Field supervisor"),
        (VIEWER, "Viewer"),
    ]

    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="erp_memberships")
    role = models.CharField(max_length=12, choices=ROLE_CHOICES, default=VIEWER)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["organisation__name", "user__full_name"]
        constraints = [models.UniqueConstraint(fields=["organisation", "user"], name="erp_one_membership_per_person")]

    def __str__(self):
        return f"{self.user} - {self.get_role_display()} at {self.organisation}"


class Farm(models.Model):
    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="farms")
    name = models.CharField(max_length=120)
    province = models.CharField(max_length=40, choices=[(p, p) for p in PROVINCES])
    district = models.CharField(max_length=80)
    size_ha = models.DecimalField("Total size (ha)", max_digits=10, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["organisation", "name"], name="erp_unique_farm_name")]

    def __str__(self):
        return self.name

    @property
    def fields_ha(self):
        return self.fields.filter(is_active=True).aggregate(t=Sum("size_ha"))["t"] or Decimal("0")


class Field(models.Model):
    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="fields")
    farm = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name="fields")
    name = models.CharField(max_length=60, help_text="e.g. Block A, Pivot 2, Lower field")
    size_ha = models.DecimalField("Size (ha)", max_digits=9, decimal_places=2)
    irrigated = models.BooleanField(default=False)
    soil_type = models.CharField(max_length=60, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["farm__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["farm", "name"], name="erp_unique_field_name"),
            models.CheckConstraint(condition=models.Q(size_ha__gt=0), name="erp_field_size_positive"),
        ]

    def __str__(self):
        return f"{self.farm.name} / {self.name}"


class Season(models.Model):
    PLANNING, ACTIVE, CLOSED = "planning", "active", "closed"
    STATUS_CHOICES = [(PLANNING, "Planning"), (ACTIVE, "Active"), (CLOSED, "Closed")]

    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="seasons")
    name = models.CharField(max_length=60, help_text="e.g. 2026/27 Summer")
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PLANNING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(fields=["organisation", "name"], name="erp_unique_season_name"),
            models.CheckConstraint(condition=models.Q(end_date__gt=models.F("start_date")), name="erp_season_dates"),
        ]

    def __str__(self):
        return self.name


class CropPlan(models.Model):
    PLANNED, PLANTED, HARVESTED, ABANDONED = "planned", "planted", "harvested", "abandoned"
    STATUS_CHOICES = [(PLANNED, "Planned"), (PLANTED, "Planted"), (HARVESTED, "Harvested"), (ABANDONED, "Abandoned")]

    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="crop_plans")
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="crop_plans")
    field = models.ForeignKey(Field, on_delete=models.PROTECT, related_name="crop_plans")
    crop = models.CharField(max_length=40, choices=[(c, c) for c in CROPS])
    variety = models.CharField(max_length=60, blank=True, help_text="e.g. SC 727, Star 9113")
    planted_ha = models.DecimalField("Area (ha)", max_digits=9, decimal_places=2)
    planting_date = models.DateField(null=True, blank=True)
    expected_harvest = models.DateField(null=True, blank=True)
    target_yield_t_per_ha = models.DecimalField("Target yield (t/ha)", max_digits=8, decimal_places=2,
                                                null=True, blank=True)
    budget_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    budget_currency = models.CharField(max_length=3, choices=CURRENCIES, default="USD")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PLANNED)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["season__start_date", "field__farm__name", "field__name"]
        constraints = [models.CheckConstraint(condition=models.Q(planted_ha__gt=0), name="erp_plan_area_positive")]

    def __str__(self):
        return f"{self.crop} on {self.field} ({self.season})"


class AuditEvent(models.Model):
    """One line per change. Written by erp_core.audit.record(); never edited or deleted."""
    id = models.BigAutoField(primary_key=True)
    organisation = models.ForeignKey(Organisation, null=True, blank=True, on_delete=models.CASCADE,
                                     related_name="audit_events")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="+")
    action = models.CharField(max_length=20)
    object_type = models.CharField(max_length=40)
    object_id = models.CharField(max_length=40, blank=True)
    summary = models.CharField(max_length=200)
    changes = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-at", "-id"]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.summary}"


class Invite(models.Model):
    """A link the owner sends (e.g. by WhatsApp) so someone can join this business with a set role.

    The link carries a long random token. It works once, for 7 days, and the owner can cancel it.
    Whoever opens it signs up or logs in, and joins with the role chosen by the owner.
    """
    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="invites")
    token = models.CharField(max_length=64, unique=True)
    role = models.CharField(max_length=12, choices=Membership.ROLE_CHOICES)
    name = models.CharField("Who it's for", max_length=120, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="+")
    used_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def status(self, now):
        if self.cancelled_at:
            return "cancelled"
        if self.used_at:
            return "used"
        if self.expires_at <= now:
            return "expired"
        return "open"
