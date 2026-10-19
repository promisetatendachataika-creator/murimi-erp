"""
Money rules for Murimi Farm ERP.

1. Money is ALWAYS Decimal, never float. Floats can't hold 0.10 exactly, so
   sums drift by fractions of a cent and books stop balancing.
2. Amounts are rounded to cents with ROUND_HALF_UP (2.345 -> 2.35), the way
   people round money by hand.
3. Converting goes through US dollars: ZWG -> USD -> ZAR. The rate used is
   returned with the answer, so it can be stored with the transaction.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q

from murimi_common.choices import CURRENCY_CODES

CENT = Decimal("0.01")


def q2(amount):
    """Round to cents."""
    return Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)


class RateMissing(Exception):
    def __init__(self, currency, on_date):
        super().__init__(f"No {currency} rate on or before {on_date}. Add one under Exchange rates.")
        self.currency, self.on_date = currency, on_date


def rate_for(organisation, currency, on_date):
    """The newest rate on or before `on_date`. The organisation's own rate wins over Murimi's on the same day."""
    from .models import ExchangeRate
    if currency == "USD":
        return None
    candidates = (ExchangeRate.objects
                  .filter(currency=currency, date__lte=on_date)
                  .filter(Q(organisation=organisation) | Q(organisation__isnull=True))
                  .order_by("-date"))
    best = None
    for r in candidates[:10]:  # same-day rates: prefer the organisation's own
        if best is None:
            best = r
        elif r.date == best.date and r.organisation_id and not best.organisation_id:
            best = r
        elif r.date < best.date:
            break
    if best is None:
        raise RateMissing(currency, on_date)
    return best


@dataclass
class Conversion:
    amount: Decimal
    currency: str
    result: Decimal
    to: str
    rates: list  # the ExchangeRate rows used (0, 1 or 2)

    def as_dict(self):
        return {
            "amount": f"{self.amount:f}", "currency": self.currency,
            "result": f"{self.result:f}", "to": self.to,
            "rates_used": [{"id": str(r.id), "date": r.date.isoformat(), "currency": r.currency,
                            "units_per_usd": f"{r.units_per_usd:f}", "source": r.source,
                            "murimi_wide": r.organisation_id is None} for r in self.rates],
        }


def convert(amount, currency, to, on_date, organisation):
    if currency not in CURRENCY_CODES or to not in CURRENCY_CODES:
        raise ValueError("Unknown currency.")
    amount = Decimal(amount)
    if currency == to:
        return Conversion(amount, currency, q2(amount), to, [])
    rates = []
    usd = amount
    if currency != "USD":
        r = rate_for(organisation, currency, on_date)
        rates.append(r)
        usd = amount / r.units_per_usd
    result = usd
    if to != "USD":
        r = rate_for(organisation, to, on_date)
        rates.append(r)
        result = usd * r.units_per_usd
    return Conversion(amount, currency, q2(result), to, rates)
