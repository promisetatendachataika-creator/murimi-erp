"""
The payroll rules, in one place.

For each worker in a pay run:

    gross    = basic (monthly staff) + day pay + piece pay + extra pay (overtime, bonus...)
    NSSA     = 4.5% of gross, but only up to the monthly ceiling   (employer pays 4.5% more)
    taxable  = gross - NSSA                                        (NSSA is tax-deductible)
    PAYE     = taxable x band rate - band deduct                   (ZIMRA's table layout)
    AIDS     = 3% of PAYE
    net      = gross - NSSA - PAYE - AIDS - advances - other deductions

The rates come from PayrollRule rows (newest one on or before the pay run's last
day; a business's own row beats Murimi's). A pay run that is not one whole
calendar month scales the bands and ceiling: a 14-day run uses 14/30.4375 of them.
"""
import calendar
import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from erp_finance.money import RateMissing, q2, rate_for

from .models import Advance, LabourSettings, MinimumWage, PayrollRule, PayRun, Payslip, Worker, WorkEntry

DAYS_PER_MONTH = Decimal("30.4375")
HUNDRED = Decimal("100")


class PayrollError(Exception):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.message, self.field = message, field


# ---------------------------------------------------------------- looking things up
def rule_for(org, currency, on):
    qs = PayrollRule.objects.filter(currency=currency, effective_from__lte=on)
    rule = qs.filter(organisation=org).order_by("-effective_from").first() or \
        qs.filter(organisation__isnull=True).order_by("-effective_from").first()
    if rule is None:
        raise PayrollError(f"There are no {currency} tax tables for {on:%d %b %Y}. Add them under Labour → Pay rules.")
    return rule


def sector_for(org):
    s = LabourSettings.objects.filter(organisation=org).first()
    return s.nec_sector if s else "General agriculture"


def minimum_wage(org, grade, on, sector=None):
    if not grade:
        return None
    sector = sector or sector_for(org)
    qs = MinimumWage.objects.filter(sector=sector, grade__iexact=grade, effective_from__lte=on)
    row = qs.filter(organisation=org).order_by("-effective_from").first() or \
        qs.filter(organisation__isnull=True).order_by("-effective_from").first()
    return row


def zwg_rate(org, on):
    try:
        return rate_for(org, "ZWG", on).units_per_usd
    except RateMissing:
        return None


def to_currency(org, amount, frm, to, on):
    """Convert pay between USD and ZiG at that day's rate."""
    if frm == to or not amount:
        return q2(amount)
    rate = zwg_rate(org, on)
    if rate is None:
        raise PayrollError(f"There's no ZiG rate on or before {on:%d %b %Y}. Add one under Exchange rates.")
    return q2(amount * rate) if to == "ZWG" else q2(amount / rate)


# ---------------------------------------------------------------- the maths
def period_scale(start, end):
    """1 for a whole calendar month, otherwise days / 30.4375."""
    if start.day == 1 and end.day == calendar.monthrange(end.year, end.month)[1] and \
            (start.year, start.month) == (end.year, end.month):
        return Decimal("1")
    days = (end - start).days + 1
    return (Decimal(days) / DAYS_PER_MONTH).quantize(Decimal("0.0001"), ROUND_HALF_UP)


def paye(bands, taxable, scale=Decimal("1")):
    """Return (tax, the band used). bands: [[up_to, rate_pct, deduct], ...]."""
    taxable = Decimal(taxable)
    if taxable <= 0:
        return Decimal("0.00"), bands[0]
    for band in bands:
        up_to, pct, deduct = band
        if up_to is None or taxable <= Decimal(str(up_to)) * scale:
            tax = taxable * Decimal(str(pct)) / HUNDRED - Decimal(str(deduct)) * scale
            return max(Decimal("0.00"), q2(tax)), band
    return Decimal("0.00"), bands[-1]


def nssa_ceiling(rule, org, on, scale):
    if rule.nssa_ceiling is not None:
        return q2(rule.nssa_ceiling * scale)
    if rule.currency == "ZWG":  # use the US$ ceiling at the pay run's rate
        usd = PayrollRule.objects.filter(currency="USD", effective_from__lte=on, nssa_ceiling__isnull=False) \
            .filter(Q(organisation=org) | Q(organisation__isnull=True)).order_by("-effective_from").first()
        rate = zwg_rate(org, on)
        if usd and rate:
            return q2(usd.nssa_ceiling * rate * scale)
    return None


def calculate(slip, rule, scale, org, on, rate_zwg):
    """Fill in a Payslip's deductions and net from its earnings. Doesn't save."""
    w = slip.worker
    slip.gross = q2(slip.basic + slip.day_pay + slip.piece_pay + slip.extra_pay)
    ceiling = nssa_ceiling(rule, org, on, scale)
    if w.nssa_exempt:
        insurable = Decimal("0")
    else:
        insurable = min(slip.gross, ceiling) if ceiling is not None else slip.gross
    slip.nssa_employee = q2(insurable * rule.nssa_employee_pct / HUNDRED)
    slip.nssa_employer = q2(insurable * rule.nssa_employer_pct / HUNDRED)
    slip.taxable = q2(slip.gross - slip.nssa_employee)
    slip.paye, band = paye(rule.bands, slip.taxable, scale)
    slip.aids_levy = q2(slip.paye * rule.aids_levy_pct / HUNDRED)
    slip.net = q2(slip.gross - slip.nssa_employee - slip.paye - slip.aids_levy - slip.advances - slip.other_deductions)
    share = w.zig_share_pct / HUNDRED if slip.currency == "USD" else Decimal("0")
    if share and rate_zwg:
        zig_part_usd = q2(slip.net * share)
        slip.net_in_zwg, slip.net_in_usd = q2(zig_part_usd * rate_zwg), q2(slip.net - zig_part_usd)
    elif slip.currency == "ZWG":
        slip.net_in_zwg, slip.net_in_usd = slip.net, Decimal("0")
    else:
        slip.net_in_zwg, slip.net_in_usd = Decimal("0"), slip.net
    slip.rule = rule
    slip.workings = {
        "scale": str(scale), "nssa_ceiling": str(ceiling) if ceiling is not None else None,
        "nssa_pct": str(rule.nssa_employee_pct), "band": {"up_to": band[0], "rate": band[1], "deduct": band[2]},
        "aids_pct": str(rule.aids_levy_pct), "rule_from": rule.effective_from.isoformat(),
        "rule_owner": "yours" if rule.organisation_id else "Murimi", "zig_share_pct": str(w.zig_share_pct),
        "rate_zwg": str(rate_zwg) if rate_zwg else None,
    }
    if slip.net < 0:
        slip.warnings = slip.warnings + ["Deductions are bigger than the pay. Lower the advance or other deductions."]
    return slip


# ---------------------------------------------------------------- pay runs
def employed_days(worker, start, end):
    a = max(start, worker.start_date) if worker.start_date else start
    b = min(end, worker.end_date) if worker.end_date else end
    return max(0, (b - a).days + 1)


def unpaid_entries(org, start, end):
    return WorkEntry.objects.filter(organisation=org, payslip__isnull=True, date__gte=start, date__lte=end)


@transaction.atomic
def build(run, lock=False):
    """(Re)calculate every payslip of a draft pay run. With lock=True, also mark the work
    entries and advances as paid by these payslips (used when approving)."""
    run = PayRun.objects.select_for_update().get(pk=run.pk)
    if run.status != PayRun.DRAFT:
        raise PayrollError("This pay run is approved. Reopen it first to change it.")
    org, start, end = run.organisation, run.period_start, run.period_end
    scale = period_scale(start, end)
    rate_zwg = zwg_rate(org, run.pay_date if run.pay_date <= timezone.localdate() else timezone.localdate())
    run.rate_zwg = rate_zwg
    run.save(update_fields=["rate_zwg"])
    kept = {s.worker_id: s for s in run.payslips.all()}  # keep hand-entered extras and deductions
    entries = list(unpaid_entries(org, start, end).select_for_update())
    by_worker = {}
    for e in entries:
        by_worker.setdefault(e.worker_id, []).append(e)
    workers = Worker.objects.filter(organisation=org).filter(
        Q(pk__in=by_worker.keys()) | Q(is_active=True, pay_basis=Worker.MONTHLY))
    sector = sector_for(org)
    rules = {}
    seen = set()
    for w in workers:
        mine = by_worker.get(w.id, [])
        basic = Decimal("0")
        warnings = []
        if w.pay_basis == Worker.MONTHLY:
            days = employed_days(w, start, end)
            if days == 0 and not mine:
                continue
            full = (end - start).days + 1
            basic = q2(w.rate * scale * Decimal(days) / Decimal(full))
            if days < full:
                warnings.append(f"Worked here {days} of {full} days in this period, so the salary is part-paid.")
        old = kept.get(w.id)
        extra = old.extra_pay if old else Decimal("0")
        other = old.other_deductions if old else Decimal("0")
        if not mine and not basic and not extra:
            continue
        slip = old or Payslip(pay_run=run, worker=w)
        slip.currency = w.currency
        slip.basic = basic
        slip.days_worked = sum((e.days for e in mine), Decimal("0"))
        slip.day_pay = q2(sum((e.amount for e in mine if e.kind == WorkEntry.DAY), Decimal("0")))
        slip.piece_pay = q2(sum((e.amount for e in mine if e.kind == WorkEntry.PIECE), Decimal("0")))
        slip.extra_pay, slip.other_deductions = extra, other
        # Advances not yet recovered, oldest first, as long as they fit in the pay.
        room = slip.basic + slip.day_pay + slip.piece_pay + slip.extra_pay - other
        taken, adv_rows = Decimal("0"), []
        for a in Advance.objects.select_for_update().filter(worker=w, payslip__isnull=True, date__lte=end).order_by("date"):
            amt = to_currency(org, a.amount, a.currency, w.currency, a.date)
            if taken + amt <= room * Decimal("0.75"):
                taken += amt
                adv_rows.append(a)
            else:
                warnings.append(f"An advance of {a.currency} {a.amount} is too big for this pay; it waits for the next pay run.")
        slip.advances = q2(taken)
        if w.currency not in rules:
            rules[w.currency] = rule_for(org, w.currency, end)
        rule = rules[w.currency]
        # Minimum wage check (monthly-equivalent pay, in US$).
        mw = minimum_wage(org, w.grade, end, sector)
        if mw and w.pay_basis in (Worker.MONTHLY, Worker.DAILY):
            month_pay = w.rate if w.pay_basis == Worker.MONTHLY else w.rate * rule.working_days
            try:
                month_usd = to_currency(org, month_pay, w.currency, "USD", end)
            except PayrollError:
                month_usd = None
            if month_usd is not None and month_usd < mw.monthly_usd:
                warnings.append(f"Below the NEC minimum for grade {mw.grade} ({mw.sector}): "
                                f"US${mw.monthly_usd} a month; this worker gets about US${month_usd}.")
        slip.warnings = warnings
        calculate(slip, rule, scale, org, end, rate_zwg)
        slip.save()
        seen.add(w.id)
        if lock:
            WorkEntry.objects.filter(pk__in=[e.pk for e in mine]).update(payslip=slip)
            Advance.objects.filter(pk__in=[a.pk for a in adv_rows]).update(payslip=slip)
    run.payslips.exclude(worker_id__in=seen).delete()
    return run


@transaction.atomic
def approve(run, user):
    run = build(run, lock=True)
    if not run.payslips.exists():
        raise PayrollError("There is nobody to pay in this period. Record work or add monthly staff first.")
    if run.payslips.filter(net__lt=0).exists():
        raise PayrollError("Some payslips have deductions bigger than the pay. Fix them before approving.")
    run.status, run.approved_by, run.approved_at = PayRun.APPROVED, user, timezone.now()
    run.save(update_fields=["status", "approved_by", "approved_at"])
    return run


@transaction.atomic
def reopen(run):
    run = PayRun.objects.select_for_update().get(pk=run.pk)
    if run.status != PayRun.APPROVED:
        raise PayrollError("This pay run is already a draft.")
    WorkEntry.objects.filter(payslip__pay_run=run).update(payslip=None)
    Advance.objects.filter(payslip__pay_run=run).update(payslip=None)
    run.status, run.approved_by, run.approved_at = PayRun.DRAFT, None, None
    run.save(update_fields=["status", "approved_by", "approved_at"])
    return run


def totals(run):
    """What to pay out and what to pay to ZIMRA and NSSA, per currency."""
    out = {}
    for cur in ("USD", "ZWG"):
        agg = run.payslips.filter(currency=cur).aggregate(
            gross=Sum("gross"), net=Sum("net"), paye=Sum("paye"), aids=Sum("aids_levy"),
            nssa_e=Sum("nssa_employee"), nssa_r=Sum("nssa_employer"), adv=Sum("advances"),
            usd_part=Sum("net_in_usd"), zwg_part=Sum("net_in_zwg"))
        if agg["gross"] is None:
            continue
        out[cur] = {k: q2(v or 0) for k, v in agg.items()}
        out[cur]["people"] = run.payslips.filter(currency=cur).count()
        out[cur]["to_zimra"] = q2(out[cur]["paye"] + out[cur]["aids"])
        out[cur]["to_nssa"] = q2(out[cur]["nssa_e"] + out[cur]["nssa_r"])
        out[cur]["cost"] = q2(out[cur]["gross"] + out[cur]["nssa_r"])
    return out
