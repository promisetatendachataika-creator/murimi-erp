"""JSON API for labour and payroll. Addresses are in erp_labour/urls.py.

Pay is private: people without "view_pay" (supervisors, storekeepers, viewers)
see workers' names and record work, but never see rates, amounts or payslips.
"""
import csv
import datetime
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from erp_core import audit
from erp_core.api import dec
from erp_core.models import CropPlan, Farm, Field
from erp_core.permissions import allowed
from erp_core.tenancy import Resource, org_view
from erp_finance.money import q2
from murimi_common import clean
from murimi_common.http import body, error

from . import payroll
from .models import (Advance, LabourSettings, MinimumWage, PayrollRule, PayRun, Payslip, Task, Worker,
                     WorkEntry)

CURS = ["USD", "ZWG"]


def _pay_error(e):
    return error(e.message, errors={e.field: e.message} if e.field else None)


def _get(model, m, value, errors, key, label, **filters):
    obj = model.objects.filter(organisation=m.organisation, pk=clean.uuid_value(value), **filters).first() \
        if clean.uuid_value(value) else None
    if obj is None:
        errors[key] = f"Choose {label}."
    return obj


# ---------------------------------------------------------------- workers and piece rates
class WorkerResource(Resource):
    model = Worker
    label = "worker"
    edit_permission = "edit_workers"

    show_pay = True

    def queryset(self, m, request):
        return super().queryset(m, request).select_related("farm")

    def describe(self, w):
        return w.full_name

    def to_dict(self, w):
        d = {"id": str(w.id), "full_name": w.full_name, "job_title": w.job_title, "grade": w.grade,
             "kind": w.kind, "kind_label": w.get_kind_display(), "pay_basis": w.pay_basis,
             "pay_basis_label": w.get_pay_basis_display(), "farm": str(w.farm_id) if w.farm_id else "",
             "farm_name": w.farm.name if w.farm_id else "", "phone": w.phone, "is_active": w.is_active,
             "start_date": w.start_date.isoformat() if w.start_date else "",
             "end_date": w.end_date.isoformat() if w.end_date else ""}
        if self.show_pay:
            d.update({"rate": dec(w.rate), "currency": w.currency, "zig_share_pct": dec(w.zig_share_pct),
                      "national_id": w.national_id, "nssa_number": w.nssa_number, "nssa_exempt": w.nssa_exempt,
                      "pay_method": w.pay_method, "pay_account": w.pay_account, "notes": w.notes})
        return d

    def clean(self, data, m, instance, errors):
        farm = None
        if data.get("farm"):
            farm = _get(Farm, m, data.get("farm"), errors, "farm", "one of your farms")
        basis = clean.choice(data, "pay_basis", errors, [k for k, _ in Worker.BASIS_CHOICES], default=Worker.MONTHLY)
        rate = clean.decimal(data, "rate", errors, min_value=Decimal("0"), max_value=Decimal("100000000")) or Decimal("0")
        if basis in (Worker.MONTHLY, Worker.DAILY) and rate <= 0:
            errors["rate"] = "Enter the monthly salary or the daily rate."
        start = clean.date(data, "start_date", errors)
        end = clean.date(data, "end_date", errors)
        if start and end and end < start:
            errors["end_date"] = "The last day can't be before the first day."
        share = clean.decimal(data, "zig_share_pct", errors, min_value=Decimal("0"), max_value=Decimal("100")) or Decimal("0")
        return {
            "full_name": clean.text(data, "full_name", errors, required=True, max_len=120, label="the name"),
            "national_id": clean.text(data, "national_id", errors, max_len=30),
            "phone": clean.text(data, "phone", errors, max_len=20),
            "farm": farm, "job_title": clean.text(data, "job_title", errors, max_len=80),
            "grade": clean.text(data, "grade", errors, max_len=10).upper(),
            "kind": clean.choice(data, "kind", errors, [k for k, _ in Worker.KIND_CHOICES], default=Worker.PERMANENT),
            "pay_basis": basis, "rate": rate,
            "currency": clean.choice(data, "currency", errors, CURS, default="USD"),
            "zig_share_pct": share,
            "nssa_number": clean.text(data, "nssa_number", errors, max_len=30),
            "nssa_exempt": clean.boolean(data, "nssa_exempt"),
            "pay_method": clean.choice(data, "pay_method", errors, [k for k, _ in Worker.PAY_METHODS], default="cash"),
            "pay_account": clean.text(data, "pay_account", errors, max_len=60),
            "start_date": start, "end_date": end,
            "notes": clean.text(data, "notes", errors, max_len=2000),
        }


class TaskResource(Resource):
    model = Task
    label = "piece rate"
    edit_permission = "edit_workers"

    def describe(self, t):
        return t.name

    def to_dict(self, t):
        return {"id": str(t.id), "name": t.name, "unit": t.unit, "rate": f"{t.rate.normalize():f}",
                "currency": t.currency, "is_active": t.is_active}

    def clean(self, data, m, instance, errors):
        return {"name": clean.text(data, "name", errors, required=True, max_len=80, label="the task"),
                "unit": clean.text(data, "unit", errors, required=True, max_len=20, label="the unit"),
                "rate": clean.decimal(data, "rate", errors, required=True, min_value=Decimal("0"), places=4),
                "currency": clean.choice(data, "currency", errors, CURS, default="USD")}


tasks_res = TaskResource()
_workers_full, _workers_names = WorkerResource(), WorkerResource()
_workers_names.show_pay = False  # what supervisors and storekeepers get: names, no pay
_W = {(True, "c"): _workers_full.collection_view(), (True, "i"): _workers_full.item_view(),
      (False, "c"): _workers_names.collection_view(), (False, "i"): _workers_names.item_view()}


def workers(request, org_id, pk=None):
    from erp_core.tenancy import get_membership
    m = get_membership(request.user, org_id) if request.user.is_authenticated else None
    view = _W[(allowed(m, "view_pay"), "c" if pk is None else "i")]
    return view(request, org_id) if pk is None else view(request, org_id, pk)


@require_http_methods(["GET"])
@org_view
def options(request, m):
    s = LabourSettings.objects.filter(organisation=m.organisation).first()
    sectors = sorted(set(MinimumWage.objects.filter(Q(organisation=m.organisation) | Q(organisation__isnull=True))
                         .values_list("sector", flat=True)))
    return JsonResponse({
        "kinds": [{"value": v, "label": l} for v, l in Worker.KIND_CHOICES],
        "bases": [{"value": v, "label": l} for v, l in Worker.BASIS_CHOICES],
        "pay_methods": [{"value": v, "label": l} for v, l in Worker.PAY_METHODS],
        "currencies": CURS, "sectors": sectors,
        "nec_sector": s.nec_sector if s else "General agriculture",
        "can": {"view_pay": allowed(m, "view_pay"), "edit_workers": allowed(m, "edit_workers"),
                "record_work": allowed(m, "record_work"), "run_payroll": allowed(m, "run_payroll"),
                "approve_payroll": allowed(m, "approve_payroll"), "edit_rules": allowed(m, "edit_payroll_rules")},
    })


# ---------------------------------------------------------------- work register
def entry_dict(e, pay):
    d = {"id": str(e.id), "date": e.date.isoformat(), "worker": str(e.worker_id), "worker_name": e.worker.full_name,
         "kind": e.kind, "days": dec(e.days), "task": str(e.task_id) if e.task_id else "",
         "task_name": e.task.name if e.task_id else "", "unit": e.task.unit if e.task_id else "",
         "quantity": dec(e.quantity, 3), "crop_plan": str(e.crop_plan_id) if e.crop_plan_id else "",
         "crop": e.crop_plan.crop if e.crop_plan_id else "",
         "field_name": f"{e.field.farm.name} / {e.field.name}" if e.field_id else "",
         "note": e.note, "paid": e.payslip_id is not None, "by": e.created_by.full_name if e.created_by_id else ""}
    if pay:
        d.update({"rate": dec(e.rate, 4), "amount": dec(e.amount), "currency": e.currency, "cost_usd": dec(e.cost_usd)})
    return d


def _make_entry(m, user, data, errors):
    """Validate one work entry. Returns an unsaved WorkEntry or None."""
    org = m.organisation
    worker = _get(Worker, m, data.get("worker"), errors, "worker", "a worker")
    date = clean.date(data, "date", errors, required=True)
    if date and date > timezone.localdate():
        errors["date"] = "The date can't be in the future."
    kind = clean.choice(data, "kind", errors, [WorkEntry.DAY, WorkEntry.PIECE], default=WorkEntry.DAY)
    plan = field = task = None
    if data.get("crop_plan"):
        plan = CropPlan.objects.filter(season__organisation=org, pk=clean.uuid_value(data.get("crop_plan"))).select_related("field").first()
        if plan is None:
            errors["crop_plan"] = "Choose one of your crop plans."
        else:
            field = plan.field
    elif data.get("field"):
        field = Field.objects.filter(farm__organisation=org, pk=clean.uuid_value(data.get("field"))).first()
        if field is None:
            errors["field"] = "Choose one of your fields."
    days = qty = Decimal("0")
    if kind == WorkEntry.DAY:
        days = clean.decimal(data, "days", errors, positive=True, max_value=Decimal("1.5")) if data.get("days") not in (None, "") else Decimal("1")
    else:
        task = _get(Task, m, data.get("task"), errors, "task", "a piece-work task")
        qty = clean.decimal(data, "quantity", errors, required=True, positive=True, places=3, max_value=Decimal("1000000"))
    note = clean.text(data, "note", errors, max_len=200)
    if errors:
        return None
    if kind == WorkEntry.DAY and worker.pay_basis == Worker.PIECE:
        errors["kind"] = f"{worker.full_name} is paid by the piece. Record piece work instead."
        return None
    if worker.start_date and date < worker.start_date or worker.end_date and date > worker.end_date:
        errors["date"] = f"{worker.full_name} didn't work here on that date."
        return None
    e = WorkEntry(organisation=org, worker=worker, date=date, kind=kind, days=days or Decimal("0"), task=task,
                  quantity=qty or Decimal("0"), crop_plan=plan, field=field, note=note, created_by=user,
                  currency=worker.currency)
    try:
        if kind == WorkEntry.PIECE:
            e.rate = task.rate
            amount = q2(task.rate * qty)
            e.amount = payroll.to_currency(org, amount, task.currency, worker.currency, date)
            e.cost_usd = payroll.to_currency(org, amount, task.currency, "USD", date)
        elif worker.pay_basis == Worker.DAILY:
            e.rate = worker.rate
            e.amount = q2(worker.rate * days)
            e.cost_usd = payroll.to_currency(org, e.amount, worker.currency, "USD", date)
        else:  # salaried: the day is for attendance and crop costing; the salary pays it
            rule_days = payroll.rule_for(org, worker.currency, date).working_days
            e.rate = Decimal("0")
            e.amount = Decimal("0")
            e.cost_usd = payroll.to_currency(org, q2(worker.rate / rule_days * days), worker.currency, "USD", date)
    except payroll.PayrollError as ex:
        errors["date"] = ex.message
        return None
    return e


@require_http_methods(["GET", "POST"])
@org_view
def work(request, m):
    org, pay = m.organisation, allowed(m, "view_pay")
    if request.method == "GET":
        qs = WorkEntry.objects.filter(organisation=org).select_related("worker", "task", "crop_plan", "field__farm", "created_by")
        for key, f in (("date_from", "date__gte"), ("date_to", "date__lte")):
            v = clean.date(request.GET, key, {})
            if v:
                qs = qs.filter(**{f: v})
        for key in ("worker", "crop_plan"):
            v = clean.uuid_value(request.GET.get(key))
            if v:
                qs = qs.filter(**{key: v})
        if request.GET.get("unpaid") == "1":
            qs = qs.filter(payslip__isnull=True)
        rows = list(qs[:500])
        return JsonResponse({"results": [entry_dict(e, pay) for e in rows],
                             "total_cost_usd": dec(sum((e.cost_usd for e in rows), Decimal("0"))) if pay else None})
    if not allowed(m, "record_work"):
        return error("Your role can't record work.", status=403)
    data = body(request)
    batch = data.get("entries") if isinstance(data.get("entries"), list) else [data]
    if not batch or len(batch) > 300:
        return error("Send between 1 and 300 entries at a time.")
    shared = {k: data.get(k) for k in ("date", "kind", "task", "crop_plan", "field", "note") if data.get(k) not in (None, "")}
    made, all_errors, existing = [], {}, []
    for i, raw in enumerate(batch):
        item = {**shared, **raw} if raw is not data else raw
        eid = clean.uuid_value(item.get("id"))
        if eid:
            old = WorkEntry.objects.filter(organisation=org, pk=eid).first()
            if old:  # sent twice from an offline phone: keep the first
                existing.append(old)
                continue
        errs = {}
        e = _make_entry(m, request.user, item, errs)
        if errs:
            all_errors[str(i)] = errs
            continue
        if eid:
            e.pk = eid
        made.append(e)
    if all_errors:
        first = next(iter(all_errors.values()))
        return error("Please fix the highlighted fields." if len(batch) == 1 else
                     f"{len(all_errors)} of {len(batch)} lines have a problem: {next(iter(first.values()))}",
                     errors=first if len(batch) == 1 else None, lines=all_errors)
    with transaction.atomic():
        for e in made:
            e.save()
        if made:
            who = made[0].worker.full_name if len(made) == 1 else f"{len(made)} workers"
            what = f"{made[0].task.name}" if made[0].kind == WorkEntry.PIECE else "days worked"
            audit.record(request, org, "create", made[0], f"Recorded {what} for {who} on {made[0].date}",
                         {"entries": len(made)})
    rows = WorkEntry.objects.filter(pk__in=[e.pk for e in made + existing]).select_related(
        "worker", "task", "crop_plan", "field__farm", "created_by")
    return JsonResponse({"results": [entry_dict(e, allowed(m, "view_pay")) for e in rows]}, status=201 if made else 200)


@require_http_methods(["DELETE"])
@org_view
def work_item(request, m, pk):
    if not allowed(m, "record_work"):
        return error("Your role can't change work records.", status=403)
    e = WorkEntry.objects.filter(organisation=m.organisation, pk=clean.uuid_value(pk)).select_related("worker").first()
    if e is None:
        return error("We couldn't find that work record.", status=404)
    if e.payslip_id:
        return error("This work is already in an approved pay run, so it can't be removed.", status=409)
    audit.record(request, m.organisation, "delete", e, f"Removed work by {e.worker.full_name} on {e.date}")
    e.delete()
    return JsonResponse({"ok": True})


# ---------------------------------------------------------------- advances
def advance_dict(a):
    return {"id": str(a.id), "worker": str(a.worker_id), "worker_name": a.worker.full_name, "date": a.date.isoformat(),
            "amount": dec(a.amount), "currency": a.currency, "reason": a.reason,
            "recovered": a.payslip_id is not None,
            "pay_run": a.payslip.pay_run.name if a.payslip_id else ""}


@require_http_methods(["GET", "POST"])
@org_view
def advances(request, m):
    if not allowed(m, "view_pay"):
        return error("Only the owner, manager or accountant can see advances.", status=403)
    org = m.organisation
    if request.method == "GET":
        qs = Advance.objects.filter(organisation=org).select_related("worker", "payslip__pay_run")
        return JsonResponse({"results": [advance_dict(a) for a in qs[:300]]})
    if not allowed(m, "run_payroll"):
        return error("Your role can't give advances.", status=403)
    data, errors = body(request), {}
    worker = _get(Worker, m, data.get("worker"), errors, "worker", "a worker")
    date = clean.date(data, "date", errors, required=True)
    if date and date > timezone.localdate():
        errors["date"] = "The date can't be in the future."
    values = {"worker": worker, "date": date,
              "amount": clean.decimal(data, "amount", errors, required=True, positive=True),
              "currency": clean.choice(data, "currency", errors, CURS, default="USD"),
              "reason": clean.text(data, "reason", errors, max_len=120)}
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    a = Advance.objects.create(organisation=org, created_by=request.user, **values)
    audit.record(request, org, "create", a, f"Advance of {a.currency} {a.amount} to {worker.full_name}")
    return JsonResponse(advance_dict(a), status=201)


@require_http_methods(["DELETE"])
@org_view
def advance_item(request, m, pk):
    if not allowed(m, "run_payroll"):
        return error("Your role can't change advances.", status=403)
    a = Advance.objects.filter(organisation=m.organisation, pk=clean.uuid_value(pk)).select_related("worker").first()
    if a is None:
        return error("We couldn't find that advance.", status=404)
    if a.payslip_id:
        return error("This advance was already taken off an approved pay run.", status=409)
    audit.record(request, m.organisation, "delete", a, f"Removed advance to {a.worker.full_name} ({a.currency} {a.amount})")
    a.delete()
    return JsonResponse({"ok": True})


# ---------------------------------------------------------------- pay runs
def slip_dict(s):
    w = s.worker
    return {"id": str(s.id), "worker": str(w.id), "worker_name": w.full_name, "job_title": w.job_title, "grade": w.grade,
            "nssa_number": w.nssa_number, "national_id": w.national_id, "pay_method": w.get_pay_method_display(),
            "pay_account": w.pay_account, "pay_basis": w.pay_basis, "currency": s.currency,
            **{k: dec(getattr(s, k)) for k in ("basic", "days_worked", "day_pay", "piece_pay", "extra_pay", "gross",
                                                "nssa_employee", "taxable", "paye", "aids_levy", "advances",
                                                "other_deductions", "net", "net_in_zwg", "net_in_usd", "nssa_employer")},
            "extra_note": s.extra_note, "other_note": s.other_note, "warnings": s.warnings, "workings": s.workings}


def run_dict(r, slips=False):
    d = {"id": str(r.id), "name": r.name, "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat(),
         "pay_date": r.pay_date.isoformat(), "status": r.status, "status_label": r.get_status_display(), "note": r.note,
         "rate_zwg": dec(r.rate_zwg, 4) if r.rate_zwg else None,
         "approved_by": r.approved_by.full_name if r.approved_by_id else "",
         "approved_at": r.approved_at.isoformat() if r.approved_at else None,
         "people": r.payslips.count(), "totals": {c: {k: dec(v) if not isinstance(v, int) else v for k, v in t.items()}
                                                   for c, t in payroll.totals(r).items()}}
    if slips:
        d["payslips"] = [slip_dict(s) for s in r.payslips.select_related("worker")]
        d["warnings"] = sum(len(s.warnings) for s in r.payslips.all())
    return d


def _run(m, pk):
    return PayRun.objects.filter(organisation=m.organisation, pk=clean.uuid_value(pk)).first() if clean.uuid_value(pk) else None


@require_http_methods(["GET", "POST"])
@org_view
def pay_runs(request, m):
    if not allowed(m, "view_pay"):
        return error("Only the owner, manager or accountant can see pay runs.", status=403)
    org = m.organisation
    if request.method == "GET":
        return JsonResponse({"results": [run_dict(r) for r in PayRun.objects.filter(organisation=org).select_related("approved_by")[:60]]})
    if not allowed(m, "run_payroll"):
        return error("Your role can't start pay runs.", status=403)
    data, errors = body(request), {}
    start = clean.date(data, "period_start", errors, required=True)
    end = clean.date(data, "period_end", errors, required=True)
    pay_date = clean.date(data, "pay_date", errors) or end
    if start and end and end < start:
        errors["period_end"] = "The last day can't be before the first day."
    if start and end and (end - start).days > 31:
        errors["period_end"] = "A pay run can cover at most one month."
    name = clean.text(data, "name", errors, max_len=80) or (f"{start:%B %Y}" if start else "")
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    clash = PayRun.objects.filter(organisation=org, status=PayRun.DRAFT, period_start__lte=end, period_end__gte=start).first()
    if clash:
        return error(f"The draft pay run \"{clash.name}\" already covers some of these days. Open it instead.",
                     errors={"period_start": "Overlaps a draft pay run."})
    with transaction.atomic():
        run = PayRun.objects.create(organisation=org, name=name, period_start=start, period_end=end, pay_date=pay_date,
                                    note=clean.text(data, "note", errors, max_len=200), created_by=request.user)
        try:
            payroll.build(run)
        except payroll.PayrollError as e:
            transaction.set_rollback(True)
            return _pay_error(e)
        audit.record(request, org, "create", run, f"Started pay run {run.name}")
    return JsonResponse(run_dict(run, slips=True), status=201)


@require_http_methods(["GET", "DELETE"])
@org_view
def pay_run(request, m, pk):
    if not allowed(m, "view_pay"):
        return error("Only the owner, manager or accountant can see pay runs.", status=403)
    run = _run(m, pk)
    if run is None:
        return error("We couldn't find that pay run.", status=404)
    if request.method == "GET":
        return JsonResponse(run_dict(run, slips=True))
    if not allowed(m, "run_payroll"):
        return error("Your role can't delete pay runs.", status=403)
    if run.status != PayRun.DRAFT:
        return error("Approved pay runs can't be deleted. Reopen it first if it was a mistake.", status=409)
    audit.record(request, m.organisation, "delete", run, f"Deleted draft pay run {run.name}")
    run.delete()
    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
@org_view
def pay_run_action(request, m, pk, action):
    if action not in ("recalculate", "approve", "reopen"):
        return error("Unknown action.", status=404)
    run = _run(m, pk)
    if run is None:
        return error("We couldn't find that pay run.", status=404)
    need = {"recalculate": "run_payroll", "approve": "approve_payroll", "reopen": "approve_payroll"}[action]
    if not allowed(m, need):
        return error("Only the owner or accountant can approve or reopen pay runs." if need == "approve_payroll"
                     else "Your role can't change pay runs.", status=403)
    data = body(request)
    try:
        if action == "recalculate":
            run = payroll.build(run)
        elif action == "approve":
            run = payroll.approve(run, request.user)
            t = payroll.totals(run)
            audit.record(request, m.organisation, "update", run, f"Approved pay run {run.name}",
                         {c: f"net {v['net']}, PAYE+AIDS {v['to_zimra']}, NSSA {v['to_nssa']}" for c, v in t.items()})
        else:
            reason = clean.text(data, "reason", {}, max_len=200)
            if not reason:
                return error("Say why you are reopening this pay run.", errors={"reason": "Please give a reason."})
            run = payroll.reopen(run)
            audit.record(request, m.organisation, "update", run, f"Reopened pay run {run.name}: {reason}")
    except payroll.PayrollError as e:
        return _pay_error(e)
    return JsonResponse(run_dict(run, slips=True))


@require_http_methods(["PATCH"])
@org_view
def payslip(request, m, pk):
    if not allowed(m, "run_payroll"):
        return error("Your role can't change payslips.", status=403)
    s = Payslip.objects.filter(pay_run__organisation=m.organisation, pk=clean.uuid_value(pk)).select_related("pay_run", "worker").first() \
        if clean.uuid_value(pk) else None
    if s is None:
        return error("We couldn't find that payslip.", status=404)
    if s.pay_run.status != PayRun.DRAFT:
        return error("This pay run is approved. Reopen it first to change it.", status=409)
    data, errors = body(request), {}
    values = {"extra_pay": clean.decimal(data, "extra_pay", errors, min_value=Decimal("0")) or Decimal("0"),
              "extra_note": clean.text(data, "extra_note", errors, max_len=120),
              "other_deductions": clean.decimal(data, "other_deductions", errors, min_value=Decimal("0")) or Decimal("0"),
              "other_note": clean.text(data, "other_note", errors, max_len=120)}
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    for k, v in values.items():
        setattr(s, k, v)
    s.save(update_fields=list(values))
    audit.record(request, m.organisation, "update", s, f"Changed {s.worker.full_name}'s payslip in {s.pay_run.name}",
                 {k: str(v) for k, v in values.items()})
    try:
        run = payroll.build(s.pay_run)
    except payroll.PayrollError as e:
        return _pay_error(e)
    return JsonResponse(run_dict(run, slips=True))


@require_http_methods(["GET"])
@org_view
def pay_run_csv(request, m, pk):
    if not allowed(m, "view_pay"):
        return error("Only the owner, manager or accountant can see pay runs.", status=403)
    run = _run(m, pk)
    if run is None:
        return error("We couldn't find that pay run.", status=404)
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="payroll-{run.period_start:%Y-%m}-{run.status}.csv"'
    w = csv.writer(resp)
    cols = ["basic", "days_worked", "day_pay", "piece_pay", "extra_pay", "gross", "nssa_employee", "taxable", "paye",
            "aids_levy", "advances", "other_deductions", "net", "net_in_usd", "net_in_zwg", "nssa_employer"]
    w.writerow(["Name", "National ID", "NSSA number", "Grade", "Currency", *[c.replace("_", " ").title() for c in cols],
                "Pay method", "Account"])
    for s in run.payslips.select_related("worker"):
        w.writerow([s.worker.full_name, s.worker.national_id, s.worker.nssa_number, s.worker.grade, s.currency,
                    *[getattr(s, c) for c in cols], s.worker.get_pay_method_display(), s.worker.pay_account])
    return resp


# ---------------------------------------------------------------- pay rules and minimum wages
def rule_dict(r):
    return {"id": str(r.id), "currency": r.currency, "effective_from": r.effective_from.isoformat(), "bands": r.bands,
            "aids_levy_pct": dec(r.aids_levy_pct), "nssa_employee_pct": dec(r.nssa_employee_pct),
            "nssa_employer_pct": dec(r.nssa_employer_pct), "nssa_ceiling": dec(r.nssa_ceiling) if r.nssa_ceiling is not None else None,
            "working_days": r.working_days, "source": r.source, "murimi_wide": r.organisation_id is None}


def _bands(raw, errors):
    """Accept [[up_to, rate, deduct], ...] with the last up_to empty."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= 12:
        errors["bands"] = "Give between 1 and 12 tax bands."
        return None
    out, last = [], Decimal("-1")
    for i, b in enumerate(raw):
        try:
            up_to = None if b[0] in (None, "") else Decimal(str(b[0]))
            pct, deduct = Decimal(str(b[1])), Decimal(str(b[2] or 0))
        except Exception:
            errors["bands"] = f"Band {i + 1} isn't complete."
            return None
        if (up_to is None) != (i == len(raw) - 1):
            errors["bands"] = "Only the last band has no upper limit."
            return None
        if up_to is not None and up_to <= last or not 0 <= pct <= 100 or deduct < 0:
            errors["bands"] = f"Band {i + 1}: limits must go up, rates must be 0-100%."
            return None
        last = up_to if up_to is not None else last
        out.append([float(up_to) if up_to is not None else None, float(pct), float(deduct)])
    return out


@require_http_methods(["GET", "POST"])
@org_view
def rules(request, m):
    org = m.organisation
    if request.method == "GET":
        qs = PayrollRule.objects.filter(Q(organisation=org) | Q(organisation__isnull=True))
        mins = MinimumWage.objects.filter(Q(organisation=org) | Q(organisation__isnull=True))
        today = timezone.localdate()
        current = {}
        for cur in CURS:
            try:
                current[cur] = str(payroll.rule_for(org, cur, today).id)
            except payroll.PayrollError:
                current[cur] = None
        return JsonResponse({"rules": [rule_dict(r) for r in qs], "current": current,
                             "minimum_wages": [{"id": str(x.id), "sector": x.sector, "grade": x.grade,
                                                "monthly_usd": dec(x.monthly_usd), "effective_from": x.effective_from.isoformat(),
                                                "source": x.source, "murimi_wide": x.organisation_id is None} for x in mins],
                             "nec_sector": payroll.sector_for(org)})
    if not allowed(m, "edit_payroll_rules"):
        return error("Only the owner or accountant can change pay rules.", status=403)
    data, errors = body(request), {}
    kind = data.get("type", "rule")
    if kind == "sector":
        sector = clean.text(data, "nec_sector", errors, required=True, max_len=60, label="the NEC sub-sector")
        if errors:
            return error("Please fix the highlighted fields.", errors=errors)
        LabourSettings.objects.update_or_create(organisation=org, defaults={"nec_sector": sector})
        audit.record(request, org, "update", org, f"Minimum wages now from NEC sub-sector {sector}")
        return JsonResponse({"nec_sector": sector})
    if kind == "minimum":
        values = {"sector": clean.text(data, "sector", errors, required=True, max_len=60, label="the sub-sector"),
                  "grade": clean.text(data, "grade", errors, required=True, max_len=10, label="the grade").upper(),
                  "monthly_usd": clean.decimal(data, "monthly_usd", errors, required=True, positive=True),
                  "effective_from": clean.date(data, "effective_from", errors, required=True),
                  "source": clean.text(data, "source", errors, max_len=200)}
        if errors:
            return error("Please fix the highlighted fields.", errors=errors)
        x = MinimumWage.objects.create(organisation=org, **values)
        audit.record(request, org, "create", x, f"Minimum wage {x.sector} {x.grade}: US${x.monthly_usd} from {x.effective_from}")
        return JsonResponse({"id": str(x.id)}, status=201)
    values = {"currency": clean.choice(data, "currency", errors, CURS),
              "effective_from": clean.date(data, "effective_from", errors, required=True),
              "bands": _bands(data.get("bands"), errors),
              "aids_levy_pct": clean.decimal(data, "aids_levy_pct", errors, required=True, min_value=Decimal("0"), max_value=Decimal("100")),
              "nssa_employee_pct": clean.decimal(data, "nssa_employee_pct", errors, required=True, min_value=Decimal("0"), max_value=Decimal("100")),
              "nssa_employer_pct": clean.decimal(data, "nssa_employer_pct", errors, required=True, min_value=Decimal("0"), max_value=Decimal("100")),
              "nssa_ceiling": clean.decimal(data, "nssa_ceiling", errors, positive=True),
              "working_days": int(clean.decimal(data, "working_days", errors, min_value=Decimal("1"), max_value=Decimal("31"), places=0) or 26),
              "source": clean.text(data, "source", errors, max_len=200)}
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    try:
        with transaction.atomic():
            r = PayrollRule.objects.create(organisation=org, **values)
    except IntegrityError:
        return error("You already have rules for that currency starting that day.", errors={"effective_from": "Already used."})
    audit.record(request, org, "create", r, f"New {r.currency} pay rules from {r.effective_from}")
    return JsonResponse(rule_dict(r), status=201)


# ---------------------------------------------------------------- dashboard numbers
@require_http_methods(["GET"])
@org_view
def summary(request, m):
    org, today = m.organisation, timezone.localdate()
    start = today.replace(day=1)
    out = {"workers": Worker.objects.filter(organisation=org, is_active=True).count(),
           "worked_today": WorkEntry.objects.filter(organisation=org, date=today).values("worker").distinct().count()}
    if allowed(m, "view_pay"):
        out["cost_this_month_usd"] = dec(WorkEntry.objects.filter(organisation=org, date__gte=start)
                                         .aggregate(t=Sum("cost_usd"))["t"] or 0)
        out["unpaid_entries"] = WorkEntry.objects.filter(organisation=org, payslip__isnull=True, amount__gt=0).count()
        out["draft_runs"] = PayRun.objects.filter(organisation=org, status=PayRun.DRAFT).count()
        out["below_minimum"] = []
        for w in Worker.objects.filter(organisation=org, is_active=True).exclude(grade=""):
            mw = payroll.minimum_wage(org, w.grade, today)
            if not mw or w.pay_basis == Worker.PIECE:
                continue
            try:
                days = payroll.rule_for(org, w.currency, today).working_days
                month = w.rate if w.pay_basis == Worker.MONTHLY else w.rate * days
                usd = payroll.to_currency(org, month, w.currency, "USD", today)
            except payroll.PayrollError:
                continue
            if usd < mw.monthly_usd:
                out["below_minimum"].append({"worker": w.full_name, "grade": w.grade, "pay_usd": dec(usd),
                                             "minimum_usd": dec(mw.monthly_usd)})
    return JsonResponse(out)


def labour_by_plan(org, season=None):
    """US$ labour cost per crop plan (used by the Seasons page)."""
    qs = WorkEntry.objects.filter(organisation=org, crop_plan__isnull=False)
    if season:
        qs = qs.filter(crop_plan__season_id=season)
    return {str(r["crop_plan"]): r["t"] for r in qs.values("crop_plan").annotate(t=Sum("cost_usd"))}
