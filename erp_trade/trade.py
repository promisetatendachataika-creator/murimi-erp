"""
The rules for sales and purchases, in one place.

Line maths (per line, rounded to cents):
    amount = quantity x unit price
    prices without VAT:  net = amount,            VAT = net x rate
    prices with VAT:     net = amount / (1+rate), VAT = amount - net
Only "standard" lines carry VAT. Zero-rated, exempt and "not registered" lines have none.

Every document keeps "1 USD = X" for its own currency on its own day, so reports in US$
never move when new rates are added.
"""
import uuid
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from erp_finance.money import RateMissing, q2, rate_for
from erp_inventory import stock

from .models import Document, Line, Payment, VatRate

HUNDRED = Decimal("100")


class TradeError(Exception):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.message, self.field = message, field


def vat_pct_on(date):
    r = VatRate.objects.filter(effective_from__lte=date).order_by("-effective_from").first()
    return r.standard_pct if r else Decimal("15.5")


def units_per_usd(org, currency, date):
    if currency == "USD":
        return Decimal("1")
    try:
        return rate_for(org, currency, date).units_per_usd
    except RateMissing:
        raise TradeError(f"There's no {currency} rate on or before {date:%d %b %Y}. Add one under Exchange rates.", "currency")


def usd(amount, doc):
    return q2(Decimal(amount) / doc.units_per_usd)


def line_amounts(qty, price, category, pct, includes_vat):
    amount = q2(Decimal(qty) * Decimal(price))
    if category != Line.STANDARD:
        return amount, Decimal("0.00"), amount
    rate = Decimal(pct) / HUNDRED
    if includes_vat:
        net = q2(amount / (1 + rate))
        return net, amount - net, amount
    vat = q2(amount * rate)
    return amount, vat, amount + vat


def recalc(doc):
    """Work out every line and the document totals from quantities and prices. Saves."""
    pct = vat_pct_on(doc.date)
    net = vat = total = Decimal("0")
    for ln in doc.lines.all():
        if not doc.vat_registered:
            ln.vat_category = Line.NONE
        elif ln.vat_category == Line.NONE:
            ln.vat_category = Line.STANDARD
        ln.vat_pct = pct if ln.vat_category == Line.STANDARD else Decimal("0")
        ln.net, ln.vat, ln.total = line_amounts(ln.quantity, ln.unit_price, ln.vat_category, pct, doc.prices_include_vat)
        ln.save(update_fields=["vat_category", "vat_pct", "net", "vat", "total"])
        net, vat, total = net + ln.net, vat + ln.vat, total + ln.total
    doc.net, doc.vat, doc.total = net, vat, total
    doc.save(update_fields=["net", "vat", "total"])
    return doc


def paid(doc):
    return doc.payments.aggregate(t=Sum("amount"))["t"] or Decimal("0")


def balance(doc):
    return Decimal("0") if doc.status == Document.VOID else q2(doc.total - paid(doc))


def _next_number(org):
    from erp_core.models import Organisation
    Organisation.objects.select_for_update().get(pk=org.pk)  # one number at a time per business
    n = 0
    for num in Document.objects.filter(organisation=org, kind=Document.SALE).exclude(number="").values_list("number", flat=True):
        try:
            n = max(n, int(str(num).split("-")[-1]))
        except ValueError:
            pass
    return f"INV-{n + 1:05d}"


def _stock_out(doc, user):
    for ln in doc.lines.select_related("item", "store"):
        if ln.item_id and ln.store_id:
            ln.movement_group = stock.issue(doc.organisation, user, ln.item, ln.store, ln.quantity, doc.date,
                                            crop_plan=None, reason=f"Sold on {doc.number} to {doc.contact.name}"[:200])
            ln.save(update_fields=["movement_group"])


def _stock_in(doc, user):
    for ln in doc.lines.select_related("item", "store"):
        if ln.item_id and ln.store_id:
            unit_cost = (ln.net / ln.quantity).quantize(Decimal("0.0001"))
            ln.movement_group = stock.receive(doc.organisation, user, ln.item, ln.store, ln.quantity, unit_cost,
                                              doc.currency, doc.date, supplier=doc.contact.name[:120],
                                              reason=f"Purchase {doc.number}"[:200])
            ln.save(update_fields=["movement_group"])


@transaction.atomic
def issue(doc, user):
    doc = Document.objects.select_for_update().get(pk=doc.pk)
    if doc.kind != Document.SALE or doc.status != Document.DRAFT:
        raise TradeError("Only draft sales invoices can be issued.")
    if not doc.lines.exists():
        raise TradeError("Add at least one line first.")
    recalc(doc)
    if doc.total <= 0:
        raise TradeError("The invoice total must be above zero.")
    doc.number = _next_number(doc.organisation)
    doc.status, doc.issued_at = Document.ISSUED, timezone.now()
    doc.save(update_fields=["number", "status", "issued_at"])
    try:
        _stock_out(doc, user)
    except stock.StockError as e:
        raise TradeError(e.message, "lines")
    return doc


@transaction.atomic
def record_purchase(doc, user):
    """A purchase is final when saved: totals, then stock into the store."""
    recalc(doc)
    doc.status, doc.issued_at = Document.ISSUED, timezone.now()
    doc.save(update_fields=["status", "issued_at"])
    try:
        _stock_in(doc, user)
    except stock.StockError as e:
        raise TradeError(e.message, "lines")
    return doc


@transaction.atomic
def void(doc, user, reason):
    doc = Document.objects.select_for_update().get(pk=doc.pk)
    if doc.status == Document.VOID:
        raise TradeError("This is already void.")
    if not reason:
        raise TradeError("Say why this is being voided.", "reason")
    if doc.payments.exists():
        raise TradeError("Remove the payments on this document first.")
    if doc.kind == Document.SALE and doc.fiscal_status != Document.FISCAL_NONE and not reason.lower().startswith("credit note"):
        raise TradeError("This invoice was fiscalised. Put a credit note through your fiscal device, then void it here "
                         "with a reason starting \"Credit note\" and its number.", "reason")
    for ln in doc.lines.exclude(movement_group=None):
        try:
            stock.reverse(doc.organisation, user, ln.movement_group, f"{doc} voided: {reason}"[:150])
        except stock.StockError as e:
            raise TradeError(e.message)
    doc.status, doc.void_reason = Document.VOID, reason[:200]
    doc.save(update_fields=["status", "void_reason"])
    return doc
