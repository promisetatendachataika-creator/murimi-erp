"""
Labour, piece work and payroll.

    Worker         a person the farm pays: permanent, contract, seasonal or casual.
                   Paid monthly (a salary), daily, or by the piece.
    Task           a piece rate, e.g. "Picking cotton: US$0.05 per kg".
    WorkEntry      one person's work on one day: a day (or half day) worked, or a
                   quantity of piece work. Supervisors record these in the field.
                   Each entry can point at a crop plan, so labour counts in the crop's costs.
    Advance        money given to a worker before pay day; taken off the next pay run.
    PayrollRule    PAYE tax bands, AIDS levy and NSSA rates, with the date they start.
                   Murimi keeps the national ones; a business can add its own.
    MinimumWage    NEC minimum wage per grade, with the date it starts.
    PayRun         paying everyone for a period. A draft can be recalculated as often
                   as you like; once APPROVED its payslips, work entries and advances lock.
    Payslip        one worker's pay in one pay run, with every figure and the rules used.

Tax and wage figures change often: they are rows in tables, never numbers in the code.
"""
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from erp_core.models import CropPlan, Farm, Field, Organisation

MONEY = dict(max_digits=14, decimal_places=2)
RATE = dict(max_digits=14, decimal_places=4)
PAY_CURRENCIES = [("USD", "US dollars"), ("ZWG", "ZiG")]


def uuid_pk():
    return models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)


class Worker(models.Model):
    PERMANENT, CONTRACT, SEASONAL, CASUAL = "permanent", "contract", "seasonal", "casual"
    KIND_CHOICES = [(PERMANENT, "Permanent"), (CONTRACT, "Fixed-term contract"), (SEASONAL, "Seasonal"),
                    (CASUAL, "Casual")]
    MONTHLY, DAILY, PIECE = "monthly", "daily", "piece"
    BASIS_CHOICES = [(MONTHLY, "Monthly salary"), (DAILY, "Per day worked"), (PIECE, "Piece work only")]
    PAY_METHODS = [("cash", "Cash"), ("ecocash", "EcoCash / mobile money"), ("bank", "Bank transfer")]

    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="workers")
    full_name = models.CharField(max_length=120)
    national_id = models.CharField(max_length=30, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    farm = models.ForeignKey(Farm, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    job_title = models.CharField(max_length=80, blank=True)
    grade = models.CharField(max_length=10, blank=True, help_text="NEC grade, e.g. A1")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=PERMANENT)
    pay_basis = models.CharField(max_length=8, choices=BASIS_CHOICES, default=MONTHLY)
    rate = models.DecimalField("Monthly salary or daily rate", default=Decimal("0"), **MONEY)
    currency = models.CharField(max_length=3, choices=PAY_CURRENCIES, default="USD")
    zig_share_pct = models.DecimalField("Part of pay given in ZiG (%)", default=Decimal("0"), max_digits=5,
                                        decimal_places=2)
    nssa_number = models.CharField(max_length=30, blank=True)
    nssa_exempt = models.BooleanField(default=False, help_text="e.g. casual workers below NSSA rules")
    pay_method = models.CharField(max_length=8, choices=PAY_METHODS, default="cash")
    pay_account = models.CharField(max_length=60, blank=True, help_text="EcoCash number or bank account")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["full_name"]
        constraints = [models.CheckConstraint(condition=Q(rate__gte=0), name="erp_worker_rate_ok"),
                       models.CheckConstraint(condition=Q(zig_share_pct__gte=0) & Q(zig_share_pct__lte=100),
                                              name="erp_worker_zig_share_ok")]

    def __str__(self):
        return self.full_name


class Task(models.Model):
    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="labour_tasks")
    name = models.CharField(max_length=80)
    unit = models.CharField(max_length=20, help_text="kg, row, hectare, bale, tree...")
    rate = models.DecimalField("Pay for one unit", **RATE)
    currency = models.CharField(max_length=3, choices=PAY_CURRENCIES, default="USD")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["organisation", "name"], name="erp_task_unique_name"),
                       models.CheckConstraint(condition=Q(rate__gte=0), name="erp_task_rate_ok")]

    def __str__(self):
        return self.name


class PayRun(models.Model):
    DRAFT, APPROVED = "draft", "approved"
    STATUS_CHOICES = [(DRAFT, "Draft"), (APPROVED, "Approved")]

    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="pay_runs")
    name = models.CharField(max_length=80)
    period_start = models.DateField()
    period_end = models.DateField()
    pay_date = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    rate_zwg = models.DecimalField("1 USD = ZiG used", null=True, blank=True, max_digits=18, decimal_places=6)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_end", "-created_at"]
        constraints = [models.CheckConstraint(condition=Q(period_end__gte=models.F("period_start")),
                                              name="erp_payrun_dates_ok")]

    def __str__(self):
        return self.name


class Payslip(models.Model):
    id = uuid_pk()
    pay_run = models.ForeignKey(PayRun, on_delete=models.CASCADE, related_name="payslips")
    worker = models.ForeignKey(Worker, on_delete=models.PROTECT, related_name="payslips")
    currency = models.CharField(max_length=3, choices=PAY_CURRENCIES)
    basic = models.DecimalField(default=0, **MONEY)
    days_worked = models.DecimalField(default=0, max_digits=6, decimal_places=2)
    day_pay = models.DecimalField(default=0, **MONEY)
    piece_pay = models.DecimalField(default=0, **MONEY)
    extra_pay = models.DecimalField("Overtime, bonus, allowances", default=0, **MONEY)
    extra_note = models.CharField(max_length=120, blank=True)
    gross = models.DecimalField(default=0, **MONEY)
    nssa_employee = models.DecimalField(default=0, **MONEY)
    taxable = models.DecimalField(default=0, **MONEY)
    paye = models.DecimalField(default=0, **MONEY)
    aids_levy = models.DecimalField(default=0, **MONEY)
    advances = models.DecimalField(default=0, **MONEY)
    other_deductions = models.DecimalField(default=0, **MONEY)
    other_note = models.CharField(max_length=120, blank=True)
    net = models.DecimalField(default=0, **MONEY)
    net_in_zwg = models.DecimalField("Part of net paid in ZiG (in ZiG)", default=0, **MONEY)
    net_in_usd = models.DecimalField("Part of net paid in US$", default=0, **MONEY)
    nssa_employer = models.DecimalField(default=0, **MONEY)
    rule = models.ForeignKey("PayrollRule", null=True, on_delete=models.SET_NULL, related_name="+")
    warnings = models.JSONField(default=list, blank=True)
    workings = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["worker__full_name"]
        constraints = [models.UniqueConstraint(fields=["pay_run", "worker"], name="erp_one_payslip_per_worker")]


class WorkEntry(models.Model):
    DAY, PIECE = "day", "piece"
    KIND_CHOICES = [(DAY, "Day worked"), (PIECE, "Piece work")]

    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="work_entries")
    worker = models.ForeignKey(Worker, on_delete=models.PROTECT, related_name="work")
    date = models.DateField()
    kind = models.CharField(max_length=6, choices=KIND_CHOICES)
    days = models.DecimalField(default=Decimal("0"), max_digits=4, decimal_places=2)
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(default=Decimal("0"), max_digits=12, decimal_places=3)
    rate = models.DecimalField(default=Decimal("0"), **RATE)
    amount = models.DecimalField("Owed for this work (0 for salaried staff)", default=Decimal("0"), **MONEY)
    currency = models.CharField(max_length=3, choices=PAY_CURRENCIES, default="USD")
    cost_usd = models.DecimalField("Cost in US$ for crop costing", default=Decimal("0"), **MONEY)
    crop_plan = models.ForeignKey(CropPlan, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    field = models.ForeignKey(Field, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    note = models.CharField(max_length=200, blank=True)
    payslip = models.ForeignKey(Payslip, null=True, blank=True, on_delete=models.SET_NULL, related_name="entries")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "worker__full_name"]
        indexes = [models.Index(fields=["organisation", "date"])]


class Advance(models.Model):
    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="advances")
    worker = models.ForeignKey(Worker, on_delete=models.PROTECT, related_name="advances")
    date = models.DateField()
    amount = models.DecimalField(**MONEY)
    currency = models.CharField(max_length=3, choices=PAY_CURRENCIES, default="USD")
    reason = models.CharField(max_length=120, blank=True)
    payslip = models.ForeignKey(Payslip, null=True, blank=True, on_delete=models.SET_NULL, related_name="advance_rows")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="erp_advance_positive")]


class PayrollRule(models.Model):
    """PAYE bands for ONE MONTH of pay, in one currency, from a date.

    bands: [[up_to, rate_pct, deduct], ...] in order; up_to null on the last band.
    Tax = taxable x rate - deduct, using the band the taxable pay falls in
    (the way ZIMRA prints its tables).
    """
    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, null=True, blank=True, on_delete=models.CASCADE,
                                     related_name="payroll_rules", help_text="Empty = Murimi-wide")
    currency = models.CharField(max_length=3, choices=PAY_CURRENCIES)
    effective_from = models.DateField()
    bands = models.JSONField()
    aids_levy_pct = models.DecimalField(default=Decimal("3"), max_digits=5, decimal_places=2)
    nssa_employee_pct = models.DecimalField(default=Decimal("4.5"), max_digits=5, decimal_places=2)
    nssa_employer_pct = models.DecimalField(default=Decimal("4.5"), max_digits=5, decimal_places=2)
    nssa_ceiling = models.DecimalField("Monthly NSSA insurable earnings ceiling", null=True, blank=True, **MONEY)
    working_days = models.PositiveSmallIntegerField("Working days in a month", default=26)
    source = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from", "currency"]
        constraints = [
            models.UniqueConstraint(fields=["organisation", "currency", "effective_from"], name="erp_one_rule_per_day"),
            models.UniqueConstraint(fields=["currency", "effective_from"], condition=Q(organisation__isnull=True),
                                    name="erp_one_global_rule_per_day"),
        ]


class MinimumWage(models.Model):
    id = uuid_pk()
    organisation = models.ForeignKey(Organisation, null=True, blank=True, on_delete=models.CASCADE,
                                     related_name="minimum_wages", help_text="Empty = Murimi-wide")
    sector = models.CharField(max_length=60, default="General agriculture")
    grade = models.CharField(max_length=10)
    monthly_usd = models.DecimalField(**MONEY)
    effective_from = models.DateField()
    source = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["sector", "grade", "-effective_from"]


class LabourSettings(models.Model):
    """One row per business: which NEC sub-sector its minimum wages come from."""
    organisation = models.OneToOneField(Organisation, on_delete=models.CASCADE, primary_key=True,
                                        related_name="labour_settings")
    nec_sector = models.CharField(max_length=60, default="General agriculture")
