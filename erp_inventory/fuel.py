"""
Fuel use per machine, and warnings that may mean theft or a fault.

When a tractor is filled up, the litres put in replace what it used since the
last fill. So for each fill with a meter reading:

    use per hour (or per km) = litres put in now / (reading now - reading at the last fill)

Warnings:
  - "Meter went backwards": a reading lower than the one before (typing mistake or a tampered meter).
  - "No meter reading": the machine has a meter but none was written down.
  - "Much higher than usual": this fill's use is 30% or more above the machine's usual (median) use.
"""
from decimal import Decimal
from statistics import median

from django.db.models import Sum

from .models import Equipment, Item, Movement

HIGH = Decimal("1.30")


def fills_for(org, equipment):
    """One row per fuel issue to this machine (several batch lines of one issue are added together)."""
    rows = {}
    qs = (Movement.objects.filter(organisation=org, equipment=equipment, kind=Movement.ISSUE, reversal_of__isnull=True,
                                  item__category=Item.FUEL)
          .exclude(group__in=Movement.objects.filter(organisation=org, reversal_of__isnull=False).values("reversal_of"))
          .order_by("date", "created_at"))
    for m in qs:
        r = rows.setdefault(m.group, {"group": str(m.group), "date": m.date, "litres": Decimal("0"),
                                      "value_usd": Decimal("0"), "reading": m.meter_reading, "item": m.item.name,
                                      "created_at": m.created_at})
        r["litres"] += -m.quantity
        r["value_usd"] += -m.value_usd
    return sorted(rows.values(), key=lambda r: (r["date"], r["created_at"]))


def analyse(org, equipment):
    fills = fills_for(org, equipment)
    last_reading, rates = None, []
    for f in fills:
        f["use"] = None
        f["flags"] = []
        if equipment.meter != Equipment.NONE:
            if f["reading"] is None:
                f["flags"].append("No meter reading")
            elif last_reading is not None:
                delta = f["reading"] - last_reading
                if delta < 0:
                    f["flags"].append("Meter went backwards")
                elif delta > 0:
                    f["use"] = (f["litres"] / delta).quantize(Decimal("0.01"))
                    rates.append(f["use"])
            if f["reading"] is not None and (last_reading is None or f["reading"] >= last_reading):
                last_reading = f["reading"]
    usual = median(rates) if len(rates) >= 3 else None
    if usual:
        for f in fills:
            if f["use"] is not None and f["use"] >= usual * HIGH:
                pct = int((f["use"] / usual - 1) * 100)
                f["flags"].append(f"Much higher than usual (+{pct}%)")
    unit = "L/hour" if equipment.meter == Equipment.HOURS else "L/km" if equipment.meter == Equipment.KM else ""
    return {
        "equipment": equipment,
        "fills": fills,
        "usual_use": usual.quantize(Decimal("0.01")) if usual else None,
        "unit": unit,
        "total_litres": sum((f["litres"] for f in fills), Decimal("0")),
        "total_usd": sum((f["value_usd"] for f in fills), Decimal("0")),
        "last_reading": last_reading,
        "flags": sum(len(f["flags"]) for f in fills),
    }
