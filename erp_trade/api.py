"""JSON API for sales, purchases, VAT, harvests and reports. Addresses are in erp_trade/urls.py."""
import datetime
from collections import defaultdict
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from erp_core import audit
from erp_core.api import dec
from erp_core.models import CropPlan, Season
from erp_core.permissions import allowed
from erp_core.tenancy import Resource, org_view
from erp_finance.money import q2
from erp_inventory import stock
from erp_inventory.models import Item, Movement, Store
from murimi_common import clean
from murimi_common.choices import CURRENCY_CODES
from murimi_common.http import body, error

from . import fiscal, trade
from .models import Contact, Document, Harvest, Line, Payment

PURCHASE_CATEGORIES = [
    ("inputs", "Seed, fertiliser and chemicals"), ("fuel", "Fuel and oil"), ("repairs", "Repairs and spares"),
    ("contractors", "Contractors (ploughing, spraying, harvesting)"), ("transport", "Transport and haulage"),
    ("packaging", "Packaging"), ("utilities", "Electricity and water"), ("rent", "Rent and land"),
    ("professional", "Accounting, legal, levies"), ("equipment", "Equipment (long-term)"), ("other", "Other"),
]
CAT_LABEL = dict(PURCHASE_CATEGORIES)


def _err(e):
    return error(e.message, errors={e.field: e.message} if e.field else None)


def _need(m, perm, msg="Your role can't do this. Ask the owner or accountant."):
    return None if allowed(m, perm) else error(msg, status=403)


# ---------------------------------------------------------------- contacts
class ContactResource(Resource):
    model = Contact
    label = "contact"
    edit_permission = "edit_purchases"

    def queryset(self, m, request):
        qs = super().queryset(m, request)
        kind = request.GET.get("kind")
        if kind in ("customer", "supplier"):
            qs = qs.filter(kind__in=[kind, Contact.BOTH])
        return qs

    def describe(self, c):
        return c.name

    def to_dict(self, c):
        return {"id": str(c.id), "kind": c.kind, "kind_label": c.get_kind_display(), "name": c.name, "tin": c.tin,
                "vat_number": c.vat_number, "phone": c.phone, "email": c.email, "address": c.address, "notes": c.notes,
                "is_active": c.is_active}

    def clean(self, data, m, instance, errors):
        email = clean.text(data, "email", errors, max_len=120)
        if email and "@" not in email:
            errors["email"] = "Enter a valid email address."
        return {"kind": clean.choice(data, "kind", errors, [k for k, _ in Contact.KIND_CHOICES], default=Contact.CUSTOMER),
                "name": clean.text(data, "name", errors, required=True, max_len=120, label="the name"),
                "tin": clean.text(data, "tin", errors, max_len=20), "vat_number": clean.text(data, "vat_number", errors, max_len=20),
                "phone": clean.text(data, "phone", errors, max_len=20), "email": email,
                "address": clean.text(data, "address", errors, max_len=200), "notes": clean.text(data, "notes", errors, max_len=2000)}


contacts_res = ContactResource()


@require_http_methods(["GET"])
@org_view
def options(request, m):
    org = m.organisation
    today = timezone.localdate()
    return JsonResponse({
        "vat_registered": bool(org.vat_number), "vat_pct_today": dec(trade.vat_pct_on(today)),
        "vat_categories": [{"value": v, "label": l} for v, l in Line.VAT_CHOICES if v != Line.NONE],
        "purchase_categories": [{"value": v, "label": l} for v, l in PURCHASE_CATEGORIES],
        "methods": [{"value": v, "label": l} for v, l in Payment.METHODS], "currencies": CURRENCY_CODES,
        "fiscal_device": fiscal.get_device(org).name,
        "can": {k: allowed(m, k) for k in ("view_trade", "edit_sales", "edit_purchases", "void_trade", "record_harvest",
                                           "view_reports", "edit_stock_setup")},
    })


# ---------------------------------------------------------------- documents
def doc_dict(d, full=False):
    paid = trade.paid(d)
    out = {"id": str(d.id), "kind": d.kind, "kind_label": d.get_kind_display(), "number": d.number,
           "contact": str(d.contact_id), "contact_name": d.contact.name, "date": d.date.isoformat(),
           "due_date": d.due_date.isoformat() if d.due_date else "", "currency": d.currency,
           "units_per_usd": f"{d.units_per_usd.normalize():f}", "prices_include_vat": d.prices_include_vat,
           "vat_registered": d.vat_registered, "net": dec(d.net), "vat": dec(d.vat), "total": dec(d.total),
           "total_usd": dec(trade.usd(d.total, d)), "paid": dec(paid),
           "balance": dec(Decimal("0") if d.status == Document.VOID else d.total - paid),
           "status": d.status, "status_label": d.get_status_display(), "void_reason": d.void_reason,
           "reference": d.reference, "fiscal_status": d.fiscal_status, "fiscal_status_label": d.get_fiscal_status_display(),
           "fiscal_receipt_no": d.fiscal_receipt_no, "fiscal_code": d.fiscal_code, "fiscal_device": d.fiscal_device,
           "overdue": bool(d.status == Document.ISSUED and d.due_date and d.due_date < timezone.localdate() and d.total > paid)}
    if full:
        c = d.contact
        out.update({"notes": d.notes, "contact_detail": {"name": c.name, "tin": c.tin, "vat_number": c.vat_number,
                                                          "address": c.address, "phone": c.phone, "email": c.email},
                    "lines": [{"id": str(ln.id), "description": ln.description, "category": ln.category,
                               "category_label": CAT_LABEL.get(ln.category, ""), "quantity": dec(ln.quantity, 3),
                               "unit": ln.unit, "unit_price": f"{ln.unit_price.normalize():f}", "vat_category": ln.vat_category,
                               "vat_label": ln.get_vat_category_display(), "vat_pct": dec(ln.vat_pct), "net": dec(ln.net),
                               "vat": dec(ln.vat), "total": dec(ln.total), "item": str(ln.item_id) if ln.item_id else "",
                               "store": str(ln.store_id) if ln.store_id else "", "crop_plan": str(ln.crop_plan_id) if ln.crop_plan_id else "",
                               "crop": ln.crop_plan.crop if ln.crop_plan_id else ""} for ln in d.lines.select_related("crop_plan")],
                    "payments": [{"id": str(p.id), "date": p.date.isoformat(), "amount": dec(p.amount), "method": p.method,
                                  "method_label": p.get_method_display(), "reference": p.reference} for p in d.payments.all()],
                    "created_by": d.created_by.full_name if d.created_by_id else ""})
    return out


def _clean_lines(raw, m, kind, errors):
    org = m.organisation
    if not isinstance(raw, list) or not raw:
        errors["lines"] = "Add at least one line."
        return []
    if len(raw) > 60:
        errors["lines"] = "At most 60 lines."
        return []
    out = []
    for i, r in enumerate(raw):
        e = {}
        item = store = plan = None
        if r.get("item"):
            item = Item.objects.filter(organisation=org, pk=clean.uuid_value(r.get("item"))).first()
            if item is None:
                e["item"] = "Choose one of your stock items."
        if r.get("store"):
            store = Store.objects.filter(organisation=org, pk=clean.uuid_value(r.get("store"))).first()
            if store is None:
                e["store"] = "Choose one of your stores."
        if bool(item) != bool(store) and not e:
            e["store" if item else "item"] = "Choose both the stock item and the store, or neither."
        if r.get("crop_plan"):
            plan = CropPlan.objects.filter(season__organisation=org, pk=clean.uuid_value(r.get("crop_plan"))).first()
            if plan is None:
                e["crop_plan"] = "Choose one of your crop plans."
        qty = clean.decimal(r, "quantity", e, required=True, positive=True, places=3)
        price = clean.decimal(r, "unit_price", e, required=True, min_value=Decimal("0"), places=4)
        desc = clean.text(r, "description", e, max_len=200) or (item.name if item else "")
        if not desc:
            e["description"] = "Say what this line is for."
        category = ""
        if kind == Document.PURCHASE:
            category = clean.choice(r, "category", e, list(CAT_LABEL), default="inputs" if item else "other")
        vat_cat = clean.choice(r, "vat_category", e, [Line.STANDARD, Line.ZERO, Line.EXEMPT], default=Line.STANDARD)
        if e:
            errors["lines"] = f"Line {i + 1}: {next(iter(e.values()))}"
            return []
        out.append(dict(position=i, description=desc, category=category, quantity=qty, unit=clean.text(r, "unit", {}, max_len=20) or (item.unit if item else ""),
                        unit_price=price, vat_category=vat_cat, item=item, store=store, crop_plan=plan))
    return out


def _clean_doc(data, m, errors, kind):
    org = m.organisation
    contact = Contact.objects.filter(organisation=org, pk=clean.uuid_value(data.get("contact"))).first() if data.get("contact") else None
    if contact is None:
        errors["contact"] = "Choose the customer." if kind == Document.SALE else "Choose the supplier."
    date = clean.date(data, "date", errors, required=True)
    if date and date > timezone.localdate():
        errors["date"] = "The date can't be in the future."
    due = clean.date(data, "due_date", errors)
    if date and due and due < date:
        errors["due_date"] = "The due date can't be before the date."
    cur = clean.choice(data, "currency", errors, CURRENCY_CODES, default="USD")
    values = {"contact": contact, "date": date, "due_date": due, "currency": cur,
              "prices_include_vat": clean.boolean(data, "prices_include_vat"),
              "reference": clean.text(data, "reference", errors, max_len=60), "notes": clean.text(data, "notes", errors, max_len=2000)}
    if kind == Document.PURCHASE:
        values["number"] = clean.text(data, "number", errors, max_len=40)
    lines = _clean_lines(data.get("lines"), m, kind, errors)
    if not errors:
        try:
            values["units_per_usd"] = trade.units_per_usd(org, cur, date)
        except trade.TradeError as e:
            errors["currency"] = e.message
    return values, lines


def _save_lines(doc, lines):
    doc.lines.all().delete()
    Line.objects.bulk_create([Line(document=doc, **ln) for ln in lines])


@require_http_methods(["GET", "POST"])
@org_view
def documents(request, m):
    org = m.organisation
    if request.method == "GET":
        if not allowed(m, "view_trade") and not allowed(m, "edit_purchases"):
            return error("Only the owner, manager or accountant can see sales and purchases.", status=403)
        qs = Document.objects.filter(organisation=org).select_related("contact")
        kind = request.GET.get("kind")
        if kind in (Document.SALE, Document.PURCHASE):
            qs = qs.filter(kind=kind)
        if not allowed(m, "view_trade"):
            qs = qs.filter(kind=Document.PURCHASE)
        for key, f in (("date_from", "date__gte"), ("date_to", "date__lte")):
            v = clean.date(request.GET, key, {})
            if v:
                qs = qs.filter(**{f: v})
        if request.GET.get("status") in ("draft", "issued", "void"):
            qs = qs.filter(status=request.GET["status"])
        contact = clean.uuid_value(request.GET.get("contact"))
        if contact:
            qs = qs.filter(contact_id=contact)
        rows = [doc_dict(d) for d in qs.prefetch_related("payments")[:300]]
        if request.GET.get("unpaid") == "1":
            rows = [r for r in rows if r["status"] == "issued" and Decimal(r["balance"]) > 0]
        return JsonResponse({"results": rows})
    data = body(request)
    kind = data.get("kind")
    if kind not in (Document.SALE, Document.PURCHASE):
        return error("Say whether this is a sale or a purchase.")
    denied = _need(m, "edit_sales" if kind == Document.SALE else "edit_purchases")
    if denied:
        return denied
    did = clean.uuid_value(data.get("id"))
    if did and Document.objects.filter(organisation=org, pk=did).exists():  # sent twice (offline phone)
        return JsonResponse(doc_dict(Document.objects.get(pk=did), full=True))
    errors = {}
    values, lines = _clean_doc(data, m, errors, kind)
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    try:
        with transaction.atomic():
            doc = Document(organisation=org, kind=kind, created_by=request.user, vat_registered=bool(org.vat_number), **values)
            if did:
                doc.pk = did
            if kind == Document.PURCHASE and data.get("fiscal_receipt_no"):
                doc.fiscal_status, doc.fiscal_receipt_no = Document.FISCAL_MANUAL, clean.text(data, "fiscal_receipt_no", {}, max_len=60)
                doc.fiscal_code = clean.text(data, "fiscal_code", {}, max_len=200)
            doc.save()
            _save_lines(doc, lines)
            if kind == Document.PURCHASE:
                trade.record_purchase(doc, request.user)
                summary = f"Recorded purchase {doc.number or ''} from {doc.contact.name}: {doc.currency} {doc.total}"
            else:
                trade.recalc(doc)
                summary = f"Started a sales invoice for {doc.contact.name}: {doc.currency} {doc.total}"
            audit.record(request, org, "create", doc, summary)
    except trade.TradeError as e:
        return _err(e)
    return JsonResponse(doc_dict(doc, full=True), status=201)


def _doc(m, pk):
    return Document.objects.filter(organisation=m.organisation, pk=clean.uuid_value(pk)).select_related("contact").first() \
        if clean.uuid_value(pk) else None


@require_http_methods(["GET", "PATCH", "DELETE"])
@org_view
def document(request, m, pk):
    d = _doc(m, pk)
    if d is None or (d.kind == Document.SALE and not allowed(m, "view_trade")) or \
            (d.kind == Document.PURCHASE and not (allowed(m, "view_trade") or allowed(m, "edit_purchases"))):
        return error("We couldn't find that document.", status=404)
    if request.method == "GET":
        return JsonResponse(doc_dict(d, full=True))
    denied = _need(m, "edit_sales" if d.kind == Document.SALE else "edit_purchases")
    if denied:
        return denied
    if d.kind != Document.SALE or d.status != Document.DRAFT:
        return error("Only draft sales invoices can be changed. Void it and record it again instead.", status=409)
    if request.method == "DELETE":
        audit.record(request, m.organisation, "delete", d, f"Deleted draft invoice for {d.contact.name}")
        d.delete()
        return JsonResponse({"ok": True})
    data = {**doc_dict(d, full=True), **body(request)}
    errors = {}
    values, lines = _clean_doc(data, m, errors, d.kind)
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    with transaction.atomic():
        for k, v in values.items():
            setattr(d, k, v)
        d.vat_registered = bool(m.organisation.vat_number)
        d.save()
        _save_lines(d, lines)
        trade.recalc(d)
        audit.record(request, m.organisation, "update", d, f"Changed draft invoice for {d.contact.name}")
    return JsonResponse(doc_dict(d, full=True))


@require_http_methods(["POST"])
@org_view
def document_action(request, m, pk, action):
    if action not in ("issue", "void", "fiscalise"):
        return error("Unknown action.", status=404)
    d = _doc(m, pk)
    if d is None or not (allowed(m, "view_trade") or (d.kind == Document.PURCHASE and allowed(m, "edit_purchases"))):
        return error("We couldn't find that document.", status=404)
    data = body(request)
    try:
        if action == "issue":
            denied = _need(m, "edit_sales")
            if denied:
                return denied
            d = trade.issue(d, request.user)
            audit.record(request, m.organisation, "update", d, f"Issued invoice {d.number} to {d.contact.name}: {d.currency} {d.total}")
        elif action == "void":
            denied = _need(m, "void_trade", "Only the owner or accountant can void documents.")
            if denied:
                return denied
            reason = clean.text(data, "reason", {}, max_len=200)
            d = trade.void(d, request.user, reason)
            audit.record(request, m.organisation, "update", d, f"Voided {d}: {reason}")
        else:
            denied = _need(m, "edit_sales" if d.kind == Document.SALE else "edit_purchases")
            if denied:
                return denied
            if d.status != Document.ISSUED:
                return error("Only issued documents can be fiscalised.", status=409)
            try:
                result = fiscal.get_device(m.organisation).submit(d, data.get("fiscal_receipt_no"), data.get("fiscal_code"),
                                                                  data.get("fiscal_device"))
            except fiscal.FiscalError as e:
                return error(str(e), errors={"fiscal_receipt_no": str(e)})
            for k, v in result.items():
                setattr(d, k, v)
            d.save(update_fields=list(result))
            audit.record(request, m.organisation, "update", d, f"Fiscal details for {d}: {d.fiscal_receipt_no}")
    except trade.TradeError as e:
        return _err(e)
    d.refresh_from_db()
    return JsonResponse(doc_dict(d, full=True))


@require_http_methods(["POST"])
@org_view
def payments(request, m, pk):
    d = _doc(m, pk)
    if d is None or not allowed(m, "view_trade"):
        return error("We couldn't find that document.", status=404)
    denied = _need(m, "edit_sales" if d.kind == Document.SALE else "edit_purchases")
    if denied:
        return denied
    if d.status != Document.ISSUED:
        return error("Payments can only be added to issued documents.", status=409)
    data, errors = body(request), {}
    date = clean.date(data, "date", errors, required=True)
    if date and date > timezone.localdate():
        errors["date"] = "The date can't be in the future."
    amount = clean.decimal(data, "amount", errors, required=True, positive=True)
    if amount and amount > trade.balance(d):
        errors["amount"] = f"Only {d.currency} {trade.balance(d)} is still owed."
    values = {"date": date, "amount": amount, "method": clean.choice(data, "method", errors, [k for k, _ in Payment.METHODS], default="cash"),
              "reference": clean.text(data, "reference", errors, max_len=60)}
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    p = Payment.objects.create(organisation=m.organisation, document=d, created_by=request.user, **values)
    audit.record(request, m.organisation, "create", p, f"Payment of {d.currency} {amount} on {d}")
    return JsonResponse(doc_dict(d, full=True), status=201)


@require_http_methods(["DELETE"])
@org_view
def payment(request, m, pk):
    p = Payment.objects.filter(organisation=m.organisation, pk=clean.uuid_value(pk)).select_related("document").first() \
        if clean.uuid_value(pk) else None
    if p is None:
        return error("We couldn't find that payment.", status=404)
    denied = _need(m, "void_trade", "Only the owner or accountant can remove payments.")
    if denied:
        return denied
    audit.record(request, m.organisation, "delete", p, f"Removed payment of {p.document.currency} {p.amount} on {p.document}")
    p.delete()
    return JsonResponse({"ok": True})


# ---------------------------------------------------------------- harvests
def harvest_dict(h):
    p = h.crop_plan
    return {"id": str(h.id), "crop_plan": str(p.id), "crop": p.crop, "field": p.field.name, "date": h.date.isoformat(),
            "tonnes": dec(h.tonnes, 3), "grade": h.grade, "item_name": h.item.name if h.item_id else "",
            "store_name": h.store.name if h.store_id else "", "quantity": dec(h.quantity, 3) if h.quantity else None,
            "unit": h.item.unit if h.item_id else "", "note": h.note, "by": h.created_by.full_name if h.created_by_id else ""}


@require_http_methods(["GET", "POST"])
@org_view
def harvests(request, m):
    org = m.organisation
    if request.method == "GET":
        qs = Harvest.objects.filter(organisation=org).select_related("crop_plan__field", "item", "store", "created_by")
        season = clean.uuid_value(request.GET.get("season"))
        if season:
            qs = qs.filter(crop_plan__season_id=season)
        return JsonResponse({"results": [harvest_dict(h) for h in qs[:300]]})
    denied = _need(m, "record_harvest", "Your role can't record harvests.")
    if denied:
        return denied
    data, errors = body(request), {}
    hid = clean.uuid_value(data.get("id"))
    if hid:
        old = Harvest.objects.filter(organisation=org, pk=hid).select_related("crop_plan__field", "item", "store", "created_by").first()
        if old:  # sent twice from a phone
            return JsonResponse(harvest_dict(old))
    plan = CropPlan.objects.filter(season__organisation=org, pk=clean.uuid_value(data.get("crop_plan"))).select_related("field").first() \
        if data.get("crop_plan") else None
    if plan is None:
        errors["crop_plan"] = "Choose the crop."
    date = clean.date(data, "date", errors, required=True)
    if date and date > timezone.localdate():
        errors["date"] = "The date can't be in the future."
    tonnes = clean.decimal(data, "tonnes", errors, required=True, positive=True, places=3)
    item = store = None
    qty = None
    if data.get("item") or data.get("store"):
        item = Item.objects.filter(organisation=org, pk=clean.uuid_value(data.get("item"))).first()
        store = Store.objects.filter(organisation=org, pk=clean.uuid_value(data.get("store"))).first()
        if item is None:
            errors["item"] = "Choose the stock item for this produce."
        if store is None:
            errors["store"] = "Choose the store it goes into."
        qty = clean.decimal(data, "quantity", errors, positive=True, places=3)
        if item and qty is None and tonnes:
            per = {"tonne": Decimal("1"), "kg": Decimal("1000"), "50 kg bag": Decimal("20"), "25 kg bag": Decimal("40"),
                   "10 kg bag": Decimal("100")}.get(item.unit)
            if per is None:
                errors["quantity"] = f"How many {item.unit} went into the store?"
            else:
                qty = tonnes * per
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    try:
        with transaction.atomic():
            h = Harvest(organisation=org, crop_plan=plan, date=date, tonnes=tonnes, item=item, store=store, quantity=qty,
                        grade=clean.text(data, "grade", {}, max_len=40), note=clean.text(data, "note", {}, max_len=200),
                        created_by=request.user)
            if hid:
                h.pk = hid
            if item:
                # Produce goes in at no cost: what it cost to grow is already counted on the crop.
                h.movement_group = stock.receive(org, request.user, item, store, qty, Decimal("0"), "USD", date,
                                                 supplier=f"Harvest: {plan.crop}, {plan.field.name}"[:120], reason="Harvest")
            h.save()
            audit.record(request, org, "create", h, f"Harvested {tonnes} t of {plan.crop} from {plan.field.name}")
    except stock.StockError as e:
        return error(e.message)
    return JsonResponse(harvest_dict(h), status=201)


@require_http_methods(["DELETE"])
@org_view
def harvest(request, m, pk):
    denied = _need(m, "record_harvest", "Your role can't change harvests.")
    if denied:
        return denied
    h = Harvest.objects.filter(organisation=m.organisation, pk=clean.uuid_value(pk)).select_related("crop_plan__field").first() \
        if clean.uuid_value(pk) else None
    if h is None:
        return error("We couldn't find that harvest.", status=404)
    try:
        with transaction.atomic():
            if h.movement_group:
                stock.reverse(m.organisation, request.user, h.movement_group, "Harvest record removed")
            audit.record(request, m.organisation, "delete", h, f"Removed harvest of {h.tonnes} t {h.crop_plan.crop}")
            h.delete()
    except stock.StockError as e:
        return error(e.message, status=409)
    return JsonResponse({"ok": True})


# ---------------------------------------------------------------- reports
def _range(request, errors):
    today = timezone.localdate()
    start = clean.date(request.GET, "date_from", errors) or today.replace(day=1)
    end = clean.date(request.GET, "date_to", errors) or today
    if end < start:
        errors["date_to"] = "The end can't be before the start."
    return start, end


def _live(org, kind, start, end):
    return Document.objects.filter(organisation=org, kind=kind, status=Document.ISSUED, date__gte=start, date__lte=end)


@require_http_methods(["GET"])
@org_view
def vat_report(request, m):
    denied = _need(m, "view_reports", "Only the owner, manager or accountant can see reports.")
    if denied:
        return denied
    org, errors = m.organisation, {}
    start, end = _range(request, errors)
    if errors:
        return error("Please fix the dates.", errors=errors)
    out = {}

    def bucket(cur):
        return out.setdefault(cur, {"sales": {c: {"net": Decimal(0), "vat": Decimal(0)} for c in ("standard", "zero", "exempt", "none")},
                                    "input_claimable": Decimal(0), "input_not_claimable": Decimal(0), "purchases_net": Decimal(0),
                                    "missing_fiscal": []})
    for ln in Line.objects.filter(document__in=_live(org, Document.SALE, start, end)).select_related("document"):
        b = bucket(ln.document.currency)["sales"][ln.vat_category]
        b["net"] += ln.net
        b["vat"] += ln.vat
    for d in _live(org, Document.PURCHASE, start, end).select_related("contact"):
        b = bucket(d.currency)
        b["purchases_net"] += d.net
        if not d.vat:
            continue
        if d.vat_registered and d.fiscal_status != Document.FISCAL_NONE:
            b["input_claimable"] += d.vat
        else:
            b["input_not_claimable"] += d.vat
            if d.vat_registered:
                b["missing_fiscal"].append({"id": str(d.id), "number": d.number, "supplier": d.contact.name,
                                            "date": d.date.isoformat(), "vat": dec(d.vat)})
    result = {}
    for cur, b in out.items():
        output_vat = sum((v["vat"] for v in b["sales"].values()), Decimal(0))
        result[cur] = {"sales": {k: {"net": dec(v["net"]), "vat": dec(v["vat"])} for k, v in b["sales"].items()},
                       "output_vat": dec(output_vat), "input_claimable": dec(b["input_claimable"]),
                       "input_not_claimable": dec(b["input_not_claimable"]), "purchases_net": dec(b["purchases_net"]),
                       "payable": dec(output_vat - b["input_claimable"]), "missing_fiscal": b["missing_fiscal"]}
    return JsonResponse({"date_from": start.isoformat(), "date_to": end.isoformat(), "vat_registered": bool(org.vat_number),
                         "vat_number": org.vat_number, "tin": org.tin, "by_currency": result})


def _labour_by_plan(org, season_id):
    from erp_labour.api import labour_by_plan
    return labour_by_plan(org, season_id)


@require_http_methods(["GET"])
@org_view
def crops_report(request, m):
    denied = _need(m, "view_reports", "Only the owner, manager or accountant can see reports.")
    if denied:
        return denied
    org = m.organisation
    season = Season.objects.filter(organisation=org, pk=clean.uuid_value(request.GET.get("season"))).first() \
        if request.GET.get("season") else None
    if season is None:
        season = Season.objects.filter(organisation=org, status="active").first() or Season.objects.filter(organisation=org).first()
    if season is None:
        return JsonResponse({"season": None, "plans": [], "totals": {}})
    plans = list(CropPlan.objects.filter(season=season).exclude(status="abandoned").select_related("field__farm"))
    ids = [p.id for p in plans]
    inputs = defaultdict(Decimal)
    for r in Movement.objects.filter(organisation=org, crop_plan_id__in=ids).values("crop_plan").annotate(t=Sum("value_usd")):
        inputs[r["crop_plan"]] = -r["t"]
    labour = {k: v for k, v in _labour_by_plan(org, season.id).items()}
    revenue, other = defaultdict(Decimal), defaultdict(Decimal)
    for ln in Line.objects.filter(crop_plan_id__in=ids, document__status=Document.ISSUED).select_related("document"):
        if ln.document.kind == Document.SALE:
            revenue[ln.crop_plan_id] += trade.usd(ln.net, ln.document)
        elif not ln.item_id:  # stock purchases count when the stock is USED, not when bought
            other[ln.crop_plan_id] += trade.usd(ln.net, ln.document)
    harvested = {r["crop_plan"]: r["t"] for r in Harvest.objects.filter(crop_plan_id__in=ids).values("crop_plan").annotate(t=Sum("tonnes"))}
    from erp_finance.money import RateMissing, rate_for
    try:
        zwg = rate_for(org, "ZWG", timezone.localdate()).units_per_usd
    except RateMissing:
        zwg = None
    rows, T = [], defaultdict(Decimal)
    for p in plans:
        ha = p.planted_ha
        lab = Decimal(labour.get(str(p.id), 0) or 0)
        cost = inputs[p.id] + lab + other[p.id]
        t = harvested.get(p.id) or Decimal(0)
        budget = None
        if p.budget_amount is not None:
            budget = p.budget_amount if p.budget_currency == "USD" else (p.budget_amount / zwg if p.budget_currency == "ZWG" and zwg else None)
        row = {"id": str(p.id), "crop": p.crop, "field": p.field.name, "farm": p.field.farm.name, "ha": dec(ha),
               "target_t_per_ha": dec(p.target_yield_t_per_ha) if p.target_yield_t_per_ha else None,
               "harvested_t": dec(t, 3), "t_per_ha": dec(t / ha if ha else 0),
               "inputs_usd": dec(inputs[p.id]), "labour_usd": dec(lab), "other_usd": dec(other[p.id]), "cost_usd": dec(cost),
               "cost_per_ha": dec(cost / ha if ha else 0), "cost_per_t": dec(cost / t) if t else None,
               "revenue_usd": dec(revenue[p.id]), "profit_usd": dec(revenue[p.id] - cost),
               "profit_per_ha": dec((revenue[p.id] - cost) / ha if ha else 0),
               "budget_usd": dec(budget) if budget is not None else None}
        rows.append(row)
        for k in ("inputs_usd", "labour_usd", "other_usd", "cost_usd", "revenue_usd", "profit_usd", "harvested_t"):
            T[k] += Decimal(row[k])
        T["ha"] += ha
    return JsonResponse({"season": {"id": str(season.id), "name": season.name},
                         "seasons": [{"id": str(s.id), "name": s.name} for s in Season.objects.filter(organisation=org)],
                         "plans": rows, "totals": {k: dec(v) for k, v in T.items()}})


@require_http_methods(["GET"])
@org_view
def pnl_report(request, m):
    denied = _need(m, "view_reports", "Only the owner, manager or accountant can see reports.")
    if denied:
        return denied
    org, errors = m.organisation, {}
    start, end = _range(request, errors)
    if errors:
        return error("Please fix the dates.", errors=errors)
    months = defaultdict(lambda: {"income": Decimal(0), "costs": Decimal(0)})
    income = defaultdict(Decimal)
    for ln in Line.objects.filter(document__in=_live(org, Document.SALE, start, end)).select_related("document", "crop_plan"):
        v = trade.usd(ln.net, ln.document)
        income[ln.crop_plan.crop if ln.crop_plan_id else "Other sales"] += v
        months[ln.document.date.strftime("%Y-%m")]["income"] += v
    costs = defaultdict(Decimal)
    sold_groups = set(Line.objects.filter(document__organisation=org).exclude(movement_group=None).values_list("movement_group", flat=True))
    for mv in Movement.objects.filter(organisation=org, date__gte=start, date__lte=end,
                                      kind__in=[Movement.ISSUE, Movement.ADJUST_OUT, Movement.ADJUST_IN]).select_related("item"):
        if mv.group in sold_groups or (mv.reversal_of and mv.reversal_of in sold_groups):
            continue
        label = "Stock-take differences" if mv.kind != Movement.ISSUE else f"Used: {mv.item.get_category_display()}"
        costs[label] += -mv.value_usd
        months[mv.date.strftime("%Y-%m")]["costs"] += -mv.value_usd
    for ln in Line.objects.filter(document__in=_live(org, Document.PURCHASE, start, end), item__isnull=True).select_related("document"):
        v = trade.usd(ln.net, ln.document)
        costs[CAT_LABEL.get(ln.category, "Other")] += v
        months[ln.document.date.strftime("%Y-%m")]["costs"] += v
    from erp_labour.models import PayRun
    for run in PayRun.objects.filter(organisation=org, status="approved", period_end__gte=start, period_end__lte=end):
        for s in run.payslips.all():
            v = s.gross + s.nssa_employer
            if s.currency == "ZWG":
                v = q2(v / run.rate_zwg) if run.rate_zwg else Decimal(0)
            costs["Wages and NSSA"] += v
            months[run.period_end.strftime("%Y-%m")]["costs"] += v
    ti, tc = sum(income.values(), Decimal(0)), sum(costs.values(), Decimal(0))
    return JsonResponse({
        "date_from": start.isoformat(), "date_to": end.isoformat(),
        "income": [{"label": k, "usd": dec(v)} for k, v in sorted(income.items(), key=lambda x: -x[1])],
        "costs": [{"label": k, "usd": dec(v)} for k, v in sorted(costs.items(), key=lambda x: -x[1])],
        "total_income": dec(ti), "total_costs": dec(tc), "profit": dec(ti - tc),
        "months": [{"month": k, "income": dec(v["income"]), "costs": dec(v["costs"])} for k, v in sorted(months.items())],
        "note": "Stock bought counts as a cost when it is USED (not when bought). Long-term equipment purchases are listed as costs; your accountant may spread them over years.",
    })


@require_http_methods(["GET"])
@org_view
def owing_report(request, m):
    denied = _need(m, "view_reports", "Only the owner, manager or accountant can see reports.")
    if denied:
        return denied
    today = timezone.localdate()
    out = {}
    for kind in (Document.SALE, Document.PURCHASE):
        rows = []
        for d in Document.objects.filter(organisation=m.organisation, kind=kind, status=Document.ISSUED).select_related("contact").prefetch_related("payments"):
            bal = trade.balance(d)
            if bal <= 0:
                continue
            days = (today - (d.due_date or d.date)).days
            rows.append({"id": str(d.id), "number": d.number, "contact": d.contact.name, "date": d.date.isoformat(),
                         "due_date": d.due_date.isoformat() if d.due_date else "", "currency": d.currency, "balance": dec(bal),
                         "balance_usd": dec(trade.usd(bal, d)), "days_late": max(0, days),
                         "bucket": "not due" if days <= 0 else "1-30" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+"})
        rows.sort(key=lambda r: -r["days_late"])
        buckets = defaultdict(Decimal)
        for r in rows:
            buckets[r["bucket"]] += Decimal(r["balance_usd"])
        out["customers_owe_us" if kind == Document.SALE else "we_owe_suppliers"] = {
            "rows": rows, "total_usd": dec(sum(buckets.values(), Decimal(0))), "buckets": {k: dec(v) for k, v in buckets.items()}}
    return JsonResponse(out)
