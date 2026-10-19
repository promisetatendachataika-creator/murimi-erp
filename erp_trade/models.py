"""
Sales, purchases, VAT, harvests and the fiscal-device slot.

    Contact       a customer or supplier (or both), with TIN and VAT number.
    VatRate       the standard VAT rate from a date (15% until 2025, 15.5% from 1 Jan 2026).
    Document      a sales invoice or a purchase (supplier's invoice). Stores the exchange rate
                  of its day, so its US$ value never changes. A sales invoice is a DRAFT until
                  issued; issuing gives it the next number, takes any stock out of the store and
                  locks it. A purchase is recorded straight away (and puts stock into the store).
                  Mistakes are VOIDED with a reason, never deleted.
    Line          one line of a document: what, how many, price, VAT category, and optionally
                  a stock item + store and a crop plan (so money is counted against the crop).
    Payment       money received from a customer or paid to a supplier against a document.
    Harvest       produce taken off a field: tonnes per crop plan, optionally put into a store.

VAT categories: standard (15.5%), zero-rated (0%, input VAT still claimable), exempt (no VAT,
no claim; most prescribed farm produce from 2026). Businesses that are not VAT-registered
charge no VAT at all.
"""
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from erp_core.models import CropPlan, Organisation

MONEY = dict(max_digits=16, decimal_places=2)
QTY = dict(max_digits=14, decimal_places=3)


def uuid_pk():
    return models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)


class Contact(models.Model):
    CUSTOMER, SUPPLIER, BOTH = "customer", "supplier", "both"
    KIND_CHOICES = [(CUSTOMER, "Customer"), (SUPPLIER, "Supplier"), (BOTH, "Customer and supplier")]

    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="contacts")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=CUSTOMER)
    name = models.CharField(max_length=120)
    tin = models.CharField("TIN", max_length=20, blank=True)
    vat_number = models.CharField("VAT number", max_length=20, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["organisation", "name"], name="erp_contact_unique_name")]

    def __str__(self):
        return self.name


class VatRate(models.Model):
    effective_from = models.DateField(unique=True)
    standard_pct = models.DecimalField(max_digits=5, decimal_places=2)
    source = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-effective_from"]

    def __str__(self):
        return f"{self.standard_pct}% from {self.effective_from}"


class Document(models.Model):
    SALE, PURCHASE = "sale", "purchase"
    KIND_CHOICES = [(SALE, "Sales invoice"), (PURCHASE, "Purchase")]
    DRAFT, ISSUED, VOID = "draft", "issued", "void"
    STATUS_CHOICES = [(DRAFT, "Draft"), (ISSUED, "Issued"), (VOID, "Void")]
    FISCAL_NONE, FISCAL_MANUAL, FISCAL_FDMS = "none", "manual", "fdms"
    FISCAL_CHOICES = [(FISCAL_NONE, "Not fiscalised"), (FISCAL_MANUAL, "Fiscal details entered by hand"),
                      (FISCAL_FDMS, "Sent to ZIMRA FDMS")]

    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="trade_documents")
    kind = models.CharField(max_length=8, choices=KIND_CHOICES)
    number = models.CharField(max_length=40, blank=True, help_text="Our invoice number, or the supplier's")
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="documents")
    date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3)
    units_per_usd = models.DecimalField("1 USD = (on the document's day)", max_digits=18, decimal_places=6)
    prices_include_vat = models.BooleanField(default=False)
    vat_registered = models.BooleanField(default=True, help_text="Was our business VAT-registered on this document?")
    net = models.DecimalField(default=0, **MONEY)
    vat = models.DecimalField(default=0, **MONEY)
    total = models.DecimalField(default=0, **MONEY)
    status = models.CharField(max_length=6, choices=STATUS_CHOICES, default=DRAFT)
    void_reason = models.CharField(max_length=200, blank=True)
    reference = models.CharField(max_length=60, blank=True, help_text="Order number, delivery note...")
    notes = models.TextField(blank=True)
    # Fiscal device slot. Sales: our fiscal receipt. Purchases: the supplier's fiscal tax invoice,
    # which ZIMRA needs before input VAT can be claimed.
    fiscal_status = models.CharField(max_length=6, choices=FISCAL_CHOICES, default=FISCAL_NONE)
    fiscal_device = models.CharField(max_length=40, blank=True)
    fiscal_receipt_no = models.CharField(max_length=60, blank=True)
    fiscal_code = models.CharField("Verification / QR code", max_length=200, blank=True)
    fiscalised_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    issued_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["organisation", "kind", "date"])]
        constraints = [models.UniqueConstraint(fields=["organisation", "kind", "number"],
                                               condition=Q(kind="sale") & ~Q(number=""),
                                               name="erp_unique_sales_number")]

    def __str__(self):
        return f"{self.get_kind_display()} {self.number or '(draft)'}"


class Line(models.Model):
    STANDARD, ZERO, EXEMPT, NONE = "standard", "zero", "exempt", "none"
    VAT_CHOICES = [(STANDARD, "Standard rate"), (ZERO, "Zero-rated (0%)"), (EXEMPT, "Exempt"),
                   (NONE, "No VAT (not registered)")]

    id = uuid_pk()
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveSmallIntegerField(default=0)
    description = models.CharField(max_length=200)
    category = models.CharField(max_length=20, blank=True, help_text="What a purchase was for")
    quantity = models.DecimalField(default=Decimal("1"), **QTY)
    unit = models.CharField(max_length=20, blank=True)
    unit_price = models.DecimalField(max_digits=16, decimal_places=4)
    vat_category = models.CharField(max_length=8, choices=VAT_CHOICES, default=STANDARD)
    vat_pct = models.DecimalField(default=0, max_digits=5, decimal_places=2)
    net = models.DecimalField(default=0, **MONEY)
    vat = models.DecimalField(default=0, **MONEY)
    total = models.DecimalField(default=0, **MONEY)
    item = models.ForeignKey("erp_inventory.Item", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    store = models.ForeignKey("erp_inventory.Store", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    movement_group = models.UUIDField(null=True, blank=True)
    crop_plan = models.ForeignKey(CropPlan, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["position"]


class Payment(models.Model):
    METHODS = [("cash", "Cash"), ("ecocash", "EcoCash / mobile money"), ("bank", "Bank transfer"),
               ("swipe", "Card / swipe"), ("other", "Other")]

    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="trade_payments")
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="payments")
    date = models.DateField()
    amount = models.DecimalField(**MONEY)
    method = models.CharField(max_length=8, choices=METHODS, default="cash")
    reference = models.CharField(max_length=60, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="erp_payment_positive")]


class Harvest(models.Model):
    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="harvests")
    crop_plan = models.ForeignKey(CropPlan, on_delete=models.PROTECT, related_name="harvests")
    date = models.DateField()
    tonnes = models.DecimalField(max_digits=12, decimal_places=3)
    grade = models.CharField(max_length=40, blank=True)
    item = models.ForeignKey("erp_inventory.Item", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    store = models.ForeignKey("erp_inventory.Store", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField("Quantity put into the store (in the item's unit)", null=True, blank=True, **QTY)
    movement_group = models.UUIDField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [models.CheckConstraint(condition=Q(tonnes__gt=0), name="erp_harvest_positive")]
