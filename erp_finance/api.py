"""Exchange rates and a currency converter."""
import datetime
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from erp_core import audit
from erp_core.permissions import allowed
from erp_core.tenancy import org_view
from murimi_common import clean
from murimi_common.choices import CURRENCY_CODES
from murimi_common.http import body, error

from . import fetch
from .models import ExchangeRate
from .money import RateMissing, convert


def rate_dict(r):
    return {"id": str(r.id), "date": r.date.isoformat(), "currency": r.currency,
            "units_per_usd": f"{r.units_per_usd:f}", "source": r.source, "source_label": r.get_source_display(),
            "note": r.note, "murimi_wide": r.organisation_id is None,
            "created_by": r.created_by.full_name if r.created_by else ""}


@require_http_methods(["GET", "POST"])
@org_view
def rates(request, m):
    org = m.organisation
    if request.method == "GET":
        qs = (ExchangeRate.objects.filter(Q(organisation=org) | Q(organisation__isnull=True))
              .select_related("created_by"))
        cur = request.GET.get("currency")
        if cur in ("ZWG", "ZAR"):
            qs = qs.filter(currency=cur)
        return JsonResponse({"results": [rate_dict(r) for r in qs[:200]],
                             "sources": [{"value": v, "label": l} for v, l in ExchangeRate.SOURCE_CHOICES if v != ExchangeRate.ONLINE],
                             "online": {**fetch.ATTRIBUTION, "auto": getattr(settings, "MURIMI_RATES_AUTO", True)}})
    if not allowed(m, "edit_rates"):
        return error("Only the owner, manager or accountant can add exchange rates.", status=403)
    data = body(request)
    errors = {}
    date = clean.date(data, "date", errors, required=True)
    if date and date > timezone.localdate():
        errors["date"] = "Rates can't be in the future."
    values = {
        "date": date,
        "currency": clean.choice(data, "currency", errors, ["ZWG", "ZAR"]),
        "units_per_usd": clean.decimal(data, "units_per_usd", errors, required=True, positive=True,
                                       max_value=Decimal("1000000000"), places=6),
        "source": clean.choice(data, "source", errors, [s for s, _ in ExchangeRate.SOURCE_CHOICES if s != ExchangeRate.ONLINE],
                               default="official"),
        "note": clean.text(data, "note", errors, max_len=200),
    }
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    try:
        with transaction.atomic():
            r = ExchangeRate.objects.create(organisation=org, created_by=request.user, **values)
    except IntegrityError:
        return error("You already have a rate for that currency on that day. Delete it first to replace it.",
                     errors={"date": "Already has a rate."})
    audit.record(request, org, "create", r, f"Added rate 1 USD = {r.units_per_usd:f} {r.currency} on {r.date}", values)
    return JsonResponse(rate_dict(r), status=201)


@require_http_methods(["DELETE"])
@org_view
def rate(request, m, pk):
    if not allowed(m, "edit_rates"):
        return error("Only the owner, manager or accountant can remove exchange rates.", status=403)
    r = ExchangeRate.objects.filter(organisation=m.organisation, pk=clean.uuid_value(pk)).first()
    if r is None:
        return error("We couldn't find that rate (Murimi-wide rates can't be removed here).", status=404)
    audit.record(request, m.organisation, "delete", r, f"Removed rate {r.currency} on {r.date}")
    r.delete()
    return JsonResponse({"ok": True})


@require_http_methods(["GET"])
@org_view
def convert_view(request, m):
    errors = {}
    amount = clean.decimal(request.GET, "amount", errors, required=True, min_value=Decimal("0"), places=6)
    frm = clean.choice(request.GET, "from", errors, CURRENCY_CODES)
    to = clean.choice(request.GET, "to", errors, CURRENCY_CODES)
    on = clean.date(request.GET, "date", errors) or timezone.localdate()
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    try:
        return JsonResponse(convert(amount, frm, to, on, m.organisation).as_dict())
    except RateMissing as e:
        return error(str(e), status=409, missing_currency=e.currency)


@require_http_methods(["GET"])
@org_view
def latest(request, m):
    """Today's rates for the dashboard (fetches today's online rate first if it's missing)."""
    from .money import rate_for
    fetch.fetch_if_due()
    today = timezone.localdate()
    out = []
    for cur in ("ZWG", "ZAR"):
        try:
            r = rate_for(m.organisation, cur, today)
            out.append({**rate_dict(r), "age_days": (today - r.date).days})
        except RateMissing:
            out.append({"currency": cur, "missing": True})
    return JsonResponse({"results": out, "today": today.isoformat()})


@require_http_methods(["POST"])
@org_view
def fetch_now(request, m):
    """The "Get today's rate online" button."""
    if not allowed(m, "edit_rates"):
        return error("Only the owner, manager or accountant can fetch exchange rates.", status=403)
    try:
        saved = fetch.fetch_latest()
    except fetch.FetchFailed as e:
        return error(str(e), status=502)
    if saved:
        audit.record(request, m.organisation, "create", saved[0],
                     "Fetched online rates: " + ", ".join(f"1 USD = {r.units_per_usd:f} {r.currency}" for r in saved))
    return JsonResponse({"results": [rate_dict(r) for r in saved]})
