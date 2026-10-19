"""JSON API for stock and fuel. Addresses are in erp_inventory/urls.py."""
import datetime
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from erp_core import audit
from erp_core.api import dec
from erp_core.models import CropPlan, Farm, Field, Season
from erp_core.permissions import allowed
from erp_core.tenancy import Resource, org_view
from erp_finance.money import RateMissing, rate_for
from murimi_common import clean
from murimi_common.choices import CURRENCY_CODES
from murimi_common.http import body, error

from . import fuel as fuel_rules
from . import stock
from .models import Batch, Equipment, Item, Movement, Store


# ---------------------------------------------------------------- set-up lists
class StoreResource(Resource):
    model = Store
    label = "store"
    edit_permission = "edit_stock_setup"

    def queryset(self, m, request):
        return super().queryset(m, request).select_related("farm")

    def to_dict(self, s):
        return {"id": str(s.id), "name": s.name, "kind": s.kind, "kind_label": s.get_kind_display(),
                "farm": str(s.farm_id) if s.farm_id else "", "farm_name": s.farm.name if s.farm_id else "",
                "capacity": dec(s.capacity, 3), "is_active": s.is_active}

    def clean(self, data, m, instance, errors):
        farm = None
        if data.get("farm"):
            farm = Farm.objects.filter(organisation=m.organisation, pk=clean.uuid_value(data.get("farm"))).first()
            if farm is None:
                errors["farm"] = "Choose one of your farms."
        return {"name": clean.text(data, "name", errors, required=True, max_len=80, label="the store name"),
                "kind": clean.choice(data, "kind", errors, [k for k, _ in Store.KIND_CHOICES], default=Store.STORE),
                "farm": farm,
                "capacity": clean.decimal(data, "capacity", errors, positive=True, places=3)}


class ItemResource(Resource):
    model = Item
    label = "item"
    edit_permission = "edit_stock_setup"

    def to_dict(self, i):
        return {"id": str(i.id), "name": i.name, "category": i.category, "category_label": i.get_category_display(),
                "unit": i.unit, "reorder_level": dec(i.reorder_level, 3), "active_ingredient": i.active_ingredient,
                "reentry_hours": i.reentry_hours, "preharvest_days": i.preharvest_days, "notes": i.notes,
                "is_active": i.is_active}

    def clean(self, data, m, instance, errors):
        def whole(key):
            v = clean.decimal(data, key, errors, min_value=Decimal("0"), max_value=Decimal("10000"), places=0)
            return int(v) if v is not None else None
        return {"name": clean.text(data, "name", errors, required=True, max_len=120, label="the item name"),
                "category": clean.choice(data, "category", errors, [c for c, _ in Item.CATEGORY_CHOICES]),
                "unit": clean.choice(data, "unit", errors, Item.UNITS),
                "reorder_level": clean.decimal(data, "reorder_level", errors, min_value=Decimal("0"), places=3),
                "active_ingredient": clean.text(data, "active_ingredient", errors, max_len=120),
                "reentry_hours": whole("reentry_hours"), "preharvest_days": whole("preharvest_days"),
                "notes": clean.text(data, "notes", errors, max_len=2000)}


class EquipmentResource(Resource):
    model = Equipment
    label = "machine"
    edit_permission = "edit_stock_setup"

    def to_dict(self, e):
        return {"id": str(e.id), "name": e.name, "kind": e.kind, "kind_label": e.get_kind_display(),
                "registration": e.registration, "meter": e.meter, "meter_label": e.get_meter_display(),
                "is_active": e.is_active}

    def clean(self, data, m, instance, errors):
        return {"name": clean.text(data, "name", errors, required=True, max_len=80, label="the machine name"),
                "kind": clean.choice(data, "kind", errors, [k for k, _ in Equipment.KIND_CHOICES], default="tractor"),
                "registration": clean.text(data, "registration", errors, max_len=30),
                "meter": clean.choice(data, "meter", errors, [k for k, _ in Equipment.METER_CHOICES], default="hours")}


stores_res, items_res, equipment_res = StoreResource(), ItemResource(), EquipmentResource()


@require_http_methods(["GET"])
@org_view
def options(request, m):
    """Choices for the stock forms."""
    return JsonResponse({
        "categories": [{"value": v, "label": l} for v, l in Item.CATEGORY_CHOICES],
        "units": Item.UNITS,
        "store_kinds": [{"value": v, "label": l} for v, l in Store.KIND_CHOICES],
        "equipment_kinds": [{"value": v, "label": l} for v, l in Equipment.KIND_CHOICES],
        "meters": [{"value": v, "label": l} for v, l in Equipment.METER_CHOICES],
    })


# ---------------------------------------------------------------- reading stock
def _today_zwg_rate(org):
    try:
        return rate_for(org, "ZWG", timezone.localdate()).units_per_usd
    except RateMissing:
        return None


@require_http_methods(["GET"])
@org_view
def on_hand(request, m):
    org = m.organisation
    store = Store.objects.filter(organisation=org, pk=clean.uuid_value(request.GET.get("store"))).first()
    rows = stock.on_hand(org, store=store, category=request.GET.get("category") or None)
    rate = _today_zwg_rate(org)
    totals = stock.item_totals(org)
    out = []
    for r in rows:
        usd = r["usd"] or Decimal("0")
        level = r["item__reorder_level"]
        out.append({"item": str(r["item"]), "item_name": r["item__name"], "unit": r["item__unit"],
                    "category": r["item__category"], "store": str(r["store"]), "store_name": r["store__name"],
                    "qty": dec(r["qty"], 3), "value_usd": dec(usd), "value_zwg_bought": dec(r["zwg"]) if r["zwg"] is not None else None,
                    "value_zwg_today": dec(usd * rate) if rate else None,
                    "low": bool(level is not None and totals.get(r["item"], 0) <= level)})
    total_usd = sum((Decimal(r["value_usd"]) for r in out), Decimal("0"))
    # Items with nothing left still matter if they have a warning level.
    empty = (Item.objects.filter(organisation=org, is_active=True, reorder_level__isnull=False)
             .exclude(pk__in=[r["item"] for r in rows]).order_by("name"))
    return JsonResponse({"results": out, "total_usd": dec(total_usd), "total_zwg_today": dec(total_usd * rate) if rate else None,
                         "zwg_rate_today": dec(rate, 4) if rate else None,
                         "out_of_stock": [{"item": str(i.id), "item_name": i.name, "unit": i.unit} for i in empty]})


@require_http_methods(["GET"])
@org_view
def batches(request, m):
    org = m.organisation
    qs = Batch.objects.filter(organisation=org).select_related("item", "store")
    item = clean.uuid_value(request.GET.get("item"))
    if item:
        qs = qs.filter(item_id=item)
    store = clean.uuid_value(request.GET.get("store"))
    if store:
        qs = qs.filter(store_id=store)
    if request.GET.get("open", "1") == "1":
        qs = qs.filter(qty_remaining__gt=0)
    return JsonResponse({"results": [{
        "id": str(b.id), "item_name": b.item.name, "unit": b.item.unit, "store_name": b.store.name,
        "received_on": b.received_on.isoformat(), "supplier": b.supplier, "lot_number": b.lot_number,
        "expiry": b.expiry.isoformat() if b.expiry else None, "qty_received": dec(b.qty_received, 3),
        "qty_remaining": dec(b.qty_remaining, 3), "unit_cost": dec(b.unit_cost, 4), "currency": b.currency,
        "unit_cost_usd": dec(b.unit_cost_usd, 4), "unit_cost_zwg": dec(b.unit_cost_zwg, 4) if b.unit_cost_zwg is not None else None,
        "rate_zwg": dec(b.rate_zwg, 4) if b.rate_zwg else None,
    } for b in qs.order_by("received_on", "created_at")[:500]]})


def movement_groups(org, qs, limit=150):
    """Turn movement lines into one row per action (an issue from 3 batches is one row)."""
    reversed_groups = set(Movement.objects.filter(organisation=org, reversal_of__isnull=False).values_list("reversal_of", flat=True))
    groups = {}
    for l in qs.select_related("item", "store", "field__farm", "crop_plan", "equipment", "created_by"):
        g = groups.get(l.group)
        if g is None:
            if len(groups) >= limit:
                break
            g = groups[l.group] = {
                "group": str(l.group), "kind": l.kind, "kind_label": l.get_kind_display(), "date": l.date.isoformat(),
                "item_name": l.item.name, "unit": l.item.unit, "category": l.item.category, "stores": [],
                "qty": Decimal("0"), "value_usd": Decimal("0"),
                "field": f"{l.field.farm.name} / {l.field.name}" if l.field_id else "",
                "crop": l.crop_plan.crop if l.crop_plan_id else "",
                "equipment": l.equipment.name if l.equipment_id else "", "meter_reading": dec(l.meter_reading, 1),
                "reason": l.reason, "by": l.created_by.full_name if l.created_by_id else "",
                "is_reversal": bool(l.reversal_of), "reversed": l.group in reversed_groups,
                "at": l.created_at.isoformat()}
            if l.kind in (Movement.TRANSFER_OUT, Movement.TRANSFER_IN):
                g["kind"], g["kind_label"] = "transfer", "Moved between stores"
        if l.store.name not in g["stores"]:
            g["stores"].append(l.store.name)
        if l.kind != Movement.TRANSFER_IN:
            g["qty"] += l.quantity
            g["value_usd"] += l.value_usd
    out = []
    for g in groups.values():
        g["qty"], g["value_usd"] = dec(g["qty"], 3), dec(g["value_usd"])
        out.append(g)
    return out


@require_http_methods(["GET"])
@org_view
def movements(request, m):
    org = m.organisation
    qs = Movement.objects.filter(organisation=org)
    for key, field in (("item", "item_id"), ("store", "store_id"), ("field", "field_id"), ("equipment", "equipment_id"),
                       ("crop_plan", "crop_plan_id")):
        v = clean.uuid_value(request.GET.get(key))
        if v:
            qs = qs.filter(**{field: v})
    if request.GET.get("kind"):
        qs = qs.filter(kind=request.GET["kind"])
    return JsonResponse({"results": movement_groups(org, qs.order_by("-date", "-created_at"))})


# ---------------------------------------------------------------- doing things to stock
def _get(model, m, value, errors, key, label, **filters):
    obj = model.objects.filter(organisation=m.organisation, pk=clean.uuid_value(value), **filters).first()
    if obj is None:
        errors[key] = f"Choose {label}."
    return obj


def _stock_error(e):
    return error(e.message, errors={e.field: e.message} if e.field else None)


def _done(request, m, group, summary, changes):
    audit.record(request, m.organisation, "create", Movement(pk=group), summary, changes)
    lines = Movement.objects.filter(organisation=m.organisation, group=group).order_by("-date", "-created_at")
    rows = movement_groups(m.organisation, lines)
    return JsonResponse(rows[0] if rows else {"group": str(group)}, status=201)


def _group_id(data):
    """Ids made on a phone while offline are kept, so sending the same record twice saves it once."""
    g = clean.uuid_value(data.get("id"))
    return g


def _already(m, data):
    """If a phone sends the same record again (weak network, retry), answer with the first one."""
    g = _group_id(data)
    if g and Movement.objects.filter(organisation=m.organisation, group=g).exists():
        return JsonResponse(movement_groups(m.organisation, Movement.objects.filter(group=g))[0])
    return None


@require_http_methods(["POST"])
@org_view
def receive(request, m):
    if not allowed(m, "receive_stock"):
        return error("Only the owner, manager or storekeeper can receive stock.", status=403)
    data, errors = body(request), {}
    item = _get(Item, m, data.get("item"), errors, "item", "an item", is_active=True)
    store = _get(Store, m, data.get("store"), errors, "store", "a store", is_active=True)
    qty = clean.decimal(data, "quantity", errors, required=True, positive=True, places=3)
    cost = clean.decimal(data, "unit_cost", errors, required=True, min_value=Decimal("0"), places=4)
    currency = clean.choice(data, "currency", errors, CURRENCY_CODES)
    date = clean.date(data, "date", errors, required=True)
    expiry = clean.date(data, "expiry", errors)
    supplier = clean.text(data, "supplier", errors, max_len=120)
    lot = clean.text(data, "lot_number", errors, max_len=60)
    reason = clean.text(data, "reason", errors, max_len=200)
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    group = _group_id(data)
    if group and Movement.objects.filter(group=group).exists():
        return JsonResponse(movement_groups(m.organisation, Movement.objects.filter(group=group))[0])
    try:
        group = stock.receive(m.organisation, request.user, item, store, qty, cost, currency, date, supplier, lot, expiry,
                              reason, group=group)
    except stock.StockError as e:
        return _stock_error(e)
    return _done(request, m, group, f"Received {stock.qty_unit(qty, item.unit)} {item.name} into {store.name}",
                 {"unit_cost": f"{currency} {cost}", "supplier": supplier})


@require_http_methods(["POST"])
@org_view
def issue(request, m):
    if not allowed(m, "issue_stock"):
        return error("Your role can't record stock being used.", status=403)
    data, errors = body(request), {}
    item = _get(Item, m, data.get("item"), errors, "item", "an item")
    store = _get(Store, m, data.get("store"), errors, "store", "a store")
    qty = clean.decimal(data, "quantity", errors, required=True, positive=True, places=3)
    date = clean.date(data, "date", errors, required=True)
    plan = field = equipment = None
    if data.get("crop_plan"):
        plan = CropPlan.objects.filter(organisation=m.organisation, pk=clean.uuid_value(data.get("crop_plan"))).select_related("field", "season").first()
        if plan is None:
            errors["crop_plan"] = "Choose one of this season's crop plans."
        elif plan.season.status == Season.CLOSED:
            errors["crop_plan"] = "That season is closed."
    elif data.get("field"):
        field = _get(Field, m, data.get("field"), errors, "field", "a field")
    if data.get("equipment"):
        equipment = _get(Equipment, m, data.get("equipment"), errors, "equipment", "a machine")
    meter = clean.decimal(data, "meter_reading", errors, min_value=Decimal("0"), max_value=Decimal("99999999"), places=1)
    rate = clean.decimal(data, "rate_per_ha", errors, positive=True, places=3)
    reason = clean.text(data, "reason", errors, max_len=200)
    if item and item.category == Item.FUEL and equipment is None and plan is None and field is None and not reason:
        errors["equipment"] = "Say which machine the fuel went into (or write a reason)."
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    group = _group_id(data)
    if group and Movement.objects.filter(group=group).exists():
        return JsonResponse(movement_groups(m.organisation, Movement.objects.filter(group=group))[0])
    try:
        group = stock.issue(m.organisation, request.user, item, store, qty, date, field=field, crop_plan=plan,
                            equipment=equipment, meter_reading=meter, rate_per_ha=rate, reason=reason, group=group)
    except stock.StockError as e:
        return _stock_error(e)
    where = equipment.name if equipment else (f"{plan.crop} on {plan.field.name}" if plan else (field.name if field else store.name))
    resp = _done(request, m, group, f"Used {stock.qty_unit(qty, item.unit)} {item.name} ({where})", {"meter_reading": meter})
    if item.category == Item.CHEMICAL and (item.reentry_hours or item.preharvest_days):
        import json
        data_out = json.loads(resp.content)
        if item.reentry_hours:
            data_out["safe_reentry"] = f"Keep people out of the field for {item.reentry_hours} hours after spraying."
        if item.preharvest_days:
            data_out["earliest_harvest"] = (date + datetime.timedelta(days=item.preharvest_days)).isoformat()
        return JsonResponse(data_out, status=201)
    return resp


@require_http_methods(["POST"])
@org_view
def transfer(request, m):
    if not allowed(m, "count_stock"):
        return error("Only the owner, manager or storekeeper can move stock between stores.", status=403)
    data, errors = body(request), {}
    again = _already(m, data)
    if again:
        return again
    item = _get(Item, m, data.get("item"), errors, "item", "an item")
    src = _get(Store, m, data.get("from_store"), errors, "from_store", "the store it leaves")
    dst = _get(Store, m, data.get("to_store"), errors, "to_store", "the store it goes to", is_active=True)
    qty = clean.decimal(data, "quantity", errors, required=True, positive=True, places=3)
    date = clean.date(data, "date", errors, required=True)
    reason = clean.text(data, "reason", errors, max_len=200)
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    try:
        group = stock.transfer(m.organisation, request.user, item, src, dst, qty, date, reason, group=_group_id(data))
    except stock.StockError as e:
        return _stock_error(e)
    return _done(request, m, group, f"Moved {stock.qty_unit(qty, item.unit)} {item.name} from {src.name} to {dst.name}", {})


@require_http_methods(["POST"])
@org_view
def stock_take(request, m):
    if not allowed(m, "count_stock"):
        return error("Only the owner, manager or storekeeper can do a stock take.", status=403)
    data, errors = body(request), {}
    again = _already(m, data)
    if again:
        return again
    item = _get(Item, m, data.get("item"), errors, "item", "an item")
    store = _get(Store, m, data.get("store"), errors, "store", "a store")
    counted = clean.decimal(data, "counted", errors, required=True, min_value=Decimal("0"), places=3)
    date = clean.date(data, "date", errors, required=True)
    reason = clean.text(data, "reason", errors, max_len=200)
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    try:
        group, diff = stock.stock_take(m.organisation, request.user, item, store, counted, date, reason, group=_group_id(data))
    except stock.StockError as e:
        return _stock_error(e)
    word = "more" if diff > 0 else "less"
    return _done(request, m, group, f"Stock take: {stock.qty_unit(abs(diff), item.unit)} {word} {item.name} in {store.name}",
                 {"counted": counted, "reason": reason})


@require_http_methods(["POST"])
@org_view
def reverse(request, m, group):
    if not allowed(m, "undo_stock"):
        return error("Only the owner or farm manager can undo stock records.", status=403)
    g = clean.uuid_value(group)
    errors = {}
    reason = clean.text(body(request), "reason", errors, required=True, max_len=180, label="why")
    if errors:
        return error("Say why you are undoing this.", errors=errors)
    try:
        new = stock.reverse(m.organisation, request.user, g, reason)
    except stock.StockError as e:
        return _stock_error(e)
    first = Movement.objects.filter(group=new).select_related("item").first()
    return _done(request, m, new, f"Undid a stock record ({first.item.name}): {reason}", {"undone": str(g)})


# ---------------------------------------------------------------- fuel, warnings, costs
@require_http_methods(["GET"])
@org_view
def fuel(request, m):
    org = m.organisation
    machines = []
    for e in Equipment.objects.filter(organisation=org, is_active=True):
        a = fuel_rules.analyse(org, e)
        machines.append({"id": str(e.id), "name": e.name, "kind_label": e.get_kind_display(), "meter": e.meter,
                         "unit": a["unit"], "usual_use": dec(a["usual_use"]), "total_litres": dec(a["total_litres"], 1),
                         "total_usd": dec(a["total_usd"]), "last_reading": dec(a["last_reading"], 1), "flags": a["flags"],
                         "fills": [{"group": f["group"], "date": f["date"].isoformat(), "litres": dec(f["litres"], 1),
                                    "value_usd": dec(f["value_usd"]), "reading": dec(f["reading"], 1),
                                    "use": dec(f["use"]), "flags": f["flags"], "item": f["item"]}
                                   for f in reversed(a["fills"][-30:])]})
    tanks = []
    for s in Store.objects.filter(organisation=org, kind=Store.TANK, is_active=True):
        litres = Batch.objects.filter(store=s, item__category=Item.FUEL).aggregate(t=Sum("qty_remaining"))["t"] or Decimal("0")
        tanks.append({"id": str(s.id), "name": s.name, "litres": dec(litres, 1), "capacity": dec(s.capacity, 1),
                      "pct": float(litres / s.capacity * 100) if s.capacity else None})
    return JsonResponse({"machines": machines, "tanks": tanks})


@require_http_methods(["GET"])
@org_view
def alerts(request, m):
    org = m.organisation
    totals = stock.item_totals(org)
    low = [{"item": str(i.id), "name": i.name, "unit": i.unit, "qty": dec(totals.get(i.id, Decimal("0")), 3),
            "reorder_level": dec(i.reorder_level, 3)}
           for i in Item.objects.filter(organisation=org, is_active=True, reorder_level__isnull=False)
           if totals.get(i.id, Decimal("0")) <= i.reorder_level]
    today = timezone.localdate()
    exp = [{"batch": str(b.id), "name": b.item.name, "store": b.store.name, "qty": dec(b.qty_remaining, 3), "unit": b.item.unit,
            "expiry": b.expiry.isoformat(), "expired": b.expiry < today, "days": (b.expiry - today).days}
           for b in stock.expiring(org)]
    fuel_flags = sum(fuel_rules.analyse(org, e)["flags"] for e in Equipment.objects.filter(organisation=org, is_active=True))
    return JsonResponse({"low_stock": low, "expiring": exp, "fuel_flags": fuel_flags})


@require_http_methods(["GET"])
@org_view
def plan_costs(request, m):
    """What has been used on each crop plan, in USD, for budget against actual."""
    org = m.organisation
    qs = Movement.objects.filter(organisation=org, crop_plan__isnull=False)
    season = clean.uuid_value(request.GET.get("season"))
    if season:
        qs = qs.filter(crop_plan__season_id=season)
    rows = qs.values("crop_plan", "item__category").annotate(usd=Sum("value_usd"))
    out = {}
    for r in rows:
        p = out.setdefault(str(r["crop_plan"]), {"inputs_usd": Decimal("0"), "by_category": {}})
        p["inputs_usd"] += -r["usd"]
        p["by_category"][r["item__category"]] = dec(-r["usd"])
    from erp_labour.api import labour_by_plan
    for plan_id, usd in labour_by_plan(org, season).items():
        out.setdefault(plan_id, {"inputs_usd": Decimal("0"), "by_category": {}})["labour_usd"] = dec(usd)
    rate = _today_zwg_rate(org)
    for p in out.values():
        p["inputs_usd"] = dec(p["inputs_usd"])
        p.setdefault("labour_usd", "0.00")
    return JsonResponse({"plans": out, "zwg_rate_today": dec(rate, 4) if rate else None})
