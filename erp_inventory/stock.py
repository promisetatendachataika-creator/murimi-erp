"""
The stock rules, in one place. Every function here runs inside ONE database
transaction and locks the batches it changes (select_for_update), so two
people using the last bag at the same moment can't both succeed.

    receive()     a delivery arrives: new batch + a "received" line
    issue()       stock is used (on a field, a crop, a tractor): oldest batches first
    transfer()    stock moves between stores (the batch keeps its cost and age)
    stock_take()  you counted the store: the difference becomes an adjustment
    reverse()     undo a mistake with opposite lines (nothing is ever deleted)
    on_hand()     how much is left, and what it's worth in USD and ZiG
"""
import datetime
import uuid
from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.utils import timezone

from erp_finance.money import RateMissing, q2, rate_for

from .models import Batch, Item, Movement, Store

Q6 = Decimal("0.000001")


class StockError(Exception):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.message, self.field = message, field


def fmt_qty(q):
    q = Decimal(q).normalize()
    return f"{q:,f}"


def qty_unit(q, unit):
    """'12 kg', but '12 × 50 kg bag' so a number in the unit can't be misread."""
    return f"{fmt_qty(q)} {'× ' if unit[:1].isdigit() else ''}{unit}"


# ---------------------------------------------------------------- costs and currencies
def costs_in_usd_and_zwg(org, unit_cost, currency, on_date):
    """Return (unit_cost_usd, unit_cost_zwg or None, zwg_rate or None, other_rate or None)."""
    def maybe(cur):
        try:
            return rate_for(org, cur, on_date).units_per_usd
        except RateMissing:
            return None

    unit_cost = Decimal(unit_cost)
    zwg_rate = maybe("ZWG")
    other_rate = None
    if currency == "USD":
        usd = unit_cost
    elif currency == "ZWG":
        if zwg_rate is None:
            raise StockError(f"There's no ZiG rate on or before {on_date}. Add one under Exchange rates first.", "currency")
        usd = unit_cost / zwg_rate
    else:
        other_rate = maybe(currency)
        if other_rate is None:
            raise StockError(f"There's no {currency} rate on or before {on_date}. Add one under Exchange rates first.", "currency")
        usd = unit_cost / other_rate
    usd = usd.quantize(Q6)
    if currency == "ZWG":
        zwg = unit_cost
    else:
        zwg = (usd * zwg_rate).quantize(Q6) if zwg_rate is not None else None
    return usd, zwg, zwg_rate, other_rate


def _line(org, user, group, kind, date, batch, qty, **extra):
    """One movement line. qty is signed: + into the store, - out of it."""
    return Movement.objects.create(
        organisation=org, group=group, kind=kind, date=date, item=batch.item, store=batch.store, batch=batch,
        quantity=qty, value_usd=q2(qty * batch.unit_cost_usd),
        value_zwg=q2(qty * batch.unit_cost_zwg) if batch.unit_cost_zwg is not None else None,
        created_by=user, **extra)


def _check_qty(qty):
    if qty is None or qty <= 0:
        raise StockError("Enter a quantity above zero.", "quantity")


def _check_date(date):
    if date > timezone.localdate():
        raise StockError("The date can't be in the future.", "date")


def _take_fifo(org, item, store, qty):
    """Lock the open batches (oldest first) and work out how much to take from each."""
    batches = list(Batch.objects.select_for_update()
                   .filter(organisation=org, item=item, store=store, qty_remaining__gt=0)
                   .order_by("received_on", "created_at"))
    available = sum((b.qty_remaining for b in batches), Decimal("0"))
    if available < qty:
        raise StockError(f"Only {qty_unit(available, item.unit)} of {item.name} left in {store.name}.", "quantity")
    plan, left = [], qty
    for b in batches:
        if left <= 0:
            break
        take = min(b.qty_remaining, left)
        plan.append((b, take))
        left -= take
    return plan


# ---------------------------------------------------------------- actions
@transaction.atomic
def receive(org, user, item, store, quantity, unit_cost, currency, date, supplier="", lot_number="",
            expiry=None, reason="", group=None):
    _check_qty(quantity)
    _check_date(date)
    if unit_cost is None or unit_cost < 0:
        raise StockError("Enter what one unit cost (0 if it was free).", "unit_cost")
    usd, zwg, zwg_rate, other_rate = costs_in_usd_and_zwg(org, unit_cost, currency, date)
    batch = Batch.objects.create(
        organisation=org, item=item, store=store, received_on=date, supplier=supplier, lot_number=lot_number,
        expiry=expiry, qty_received=quantity, qty_remaining=quantity, unit_cost=unit_cost, currency=currency,
        unit_cost_usd=usd, unit_cost_zwg=zwg, rate_zwg=zwg_rate, rate_other=other_rate)
    group = group or uuid.uuid4()
    _line(org, user, group, Movement.RECEIPT, date, batch, quantity, reason=reason)
    return group


@transaction.atomic
def issue(org, user, item, store, quantity, date, field=None, crop_plan=None, equipment=None,
          meter_reading=None, rate_per_ha=None, reason="", group=None):
    _check_qty(quantity)
    _check_date(date)
    if crop_plan is not None:
        field = crop_plan.field
    group = group or uuid.uuid4()
    for batch, take in _take_fifo(org, item, store, quantity):
        batch.qty_remaining -= take
        batch.save(update_fields=["qty_remaining"])
        _line(org, user, group, Movement.ISSUE, date, batch, -take, field=field, crop_plan=crop_plan,
              equipment=equipment, meter_reading=meter_reading, rate_per_ha=rate_per_ha, reason=reason)
    return group


@transaction.atomic
def transfer(org, user, item, from_store, to_store, quantity, date, reason="", group=None):
    _check_qty(quantity)
    _check_date(date)
    if from_store == to_store:
        raise StockError("Choose two different stores.", "to_store")
    group = group or uuid.uuid4()
    for batch, take in _take_fifo(org, item, from_store, quantity):
        batch.qty_remaining -= take
        batch.save(update_fields=["qty_remaining"])
        _line(org, user, group, Movement.TRANSFER_OUT, date, batch, -take, reason=reason)
        moved = Batch.objects.create(
            organisation=org, item=item, store=to_store, received_on=batch.received_on, supplier=batch.supplier,
            lot_number=batch.lot_number, expiry=batch.expiry, qty_received=take, qty_remaining=take,
            unit_cost=batch.unit_cost, currency=batch.currency, unit_cost_usd=batch.unit_cost_usd,
            unit_cost_zwg=batch.unit_cost_zwg, rate_zwg=batch.rate_zwg, rate_other=batch.rate_other, source_batch=batch)
        _line(org, user, group, Movement.TRANSFER_IN, date, moved, take, reason=reason)
    return group


@transaction.atomic
def stock_take(org, user, item, store, counted, date, reason, unit_cost_usd=None, group=None):
    if counted is None or counted < 0:
        raise StockError("Enter what you counted (0 if there's none).", "counted")
    if not reason:
        raise StockError("Say why the count is different, e.g. 'spilt', 'stolen', 'not recorded'.", "reason")
    _check_date(date)
    have = Batch.objects.filter(organisation=org, item=item, store=store).aggregate(t=Sum("qty_remaining"))["t"] or Decimal("0")
    diff = counted - have
    if diff == 0:
        raise StockError(f"The count matches the records ({qty_unit(have, item.unit)}). Nothing to change.", "counted")
    group = group or uuid.uuid4()
    if diff < 0:
        for batch, take in _take_fifo(org, item, store, -diff):
            batch.qty_remaining -= take
            batch.save(update_fields=["qty_remaining"])
            _line(org, user, group, Movement.ADJUST_OUT, date, batch, -take, reason=reason)
        return group, diff
    # Found more than expected: value it like the newest batch (or the cost given).
    last = Batch.objects.filter(organisation=org, item=item).order_by("-received_on", "-created_at").first()
    usd = Decimal(unit_cost_usd) if unit_cost_usd is not None else (last.unit_cost_usd if last else Decimal("0"))
    try:
        zwg_rate = rate_for(org, "ZWG", date).units_per_usd
    except RateMissing:
        zwg_rate = None
    batch = Batch.objects.create(
        organisation=org, item=item, store=store, received_on=date, supplier="Stock take", qty_received=diff,
        qty_remaining=diff, unit_cost=usd, currency="USD", unit_cost_usd=usd,
        unit_cost_zwg=(usd * zwg_rate).quantize(Q6) if zwg_rate else None, rate_zwg=zwg_rate)
    _line(org, user, group, Movement.ADJUST_IN, date, batch, diff, reason=reason)
    return group, diff


@transaction.atomic
def reverse(org, user, group, reason):
    if not reason:
        raise StockError("Say why you are undoing this.", "reason")
    lines = list(Movement.objects.select_for_update().filter(organisation=org, group=group).select_related("batch"))
    if not lines:
        raise StockError("We couldn't find that stock record.")
    if any(l.reversal_of for l in lines):
        raise StockError("This is already an undo. Record the correct action again instead.")
    if Movement.objects.filter(organisation=org, reversal_of=group).exists():
        raise StockError("This has already been undone.")
    new_group = uuid.uuid4()
    today = timezone.localdate()
    for l in lines:
        batch = Batch.objects.select_for_update().get(pk=l.batch_id)
        if l.quantity > 0:
            # Stock came IN on this line, so it must still be untouched to take it back out.
            if batch.qty_remaining < l.quantity:
                raise StockError(f"Some of the {batch.item.name} from this record has already been used or moved, "
                                 "so it can't be undone. Use a stock take instead.")
            batch.qty_remaining -= l.quantity
        else:
            batch.qty_remaining += -l.quantity
        batch.save(update_fields=["qty_remaining"])
        Movement.objects.create(
            organisation=org, group=new_group, kind=l.kind, date=today, item=l.item, store=l.store, batch=batch,
            quantity=-l.quantity, value_usd=-l.value_usd, value_zwg=-l.value_zwg if l.value_zwg is not None else None,
            field=l.field, crop_plan=l.crop_plan, equipment=l.equipment, reason=f"Undo: {reason}"[:200],
            reversal_of=group, created_by=user)
    return new_group


# ---------------------------------------------------------------- what's in stock
def on_hand(org, store=None, item=None, category=None):
    """One row per item per store with quantity and value (USD, ZiG at purchase rates)."""
    qs = Batch.objects.filter(organisation=org, qty_remaining__gt=0, item__is_active=True)
    if store is not None:
        qs = qs.filter(store=store)
    if item is not None:
        qs = qs.filter(item=item)
    if category:
        qs = qs.filter(item__category=category)
    money = DecimalField(max_digits=24, decimal_places=6)
    return (qs.values("item", "item__name", "item__unit", "item__category", "item__reorder_level", "store", "store__name")
            .annotate(qty=Sum("qty_remaining"),
                      usd=Sum(ExpressionWrapper(F("qty_remaining") * F("unit_cost_usd"), output_field=money)),
                      zwg=Sum(ExpressionWrapper(F("qty_remaining") * F("unit_cost_zwg"), output_field=money)))
            .order_by("item__category", "item__name", "store__name"))


def item_totals(org):
    """Quantity of each item across all stores (for low-stock warnings)."""
    return {r["item"]: r["t"] for r in Batch.objects.filter(organisation=org).values("item").annotate(t=Sum("qty_remaining"))}


def expiring(org, within_days=30):
    soon = timezone.localdate() + datetime.timedelta(days=within_days)
    return (Batch.objects.filter(organisation=org, qty_remaining__gt=0, expiry__isnull=False, expiry__lte=soon)
            .select_related("item", "store").order_by("expiry"))
