"""
Fetch today's ZiG and rand rates from the internet, so nobody has to type them.

Source: ExchangeRate-API's free "open access" service (no key, updated once a day):
    https://open.er-api.com/v6/latest/USD
Its terms ask us to show "Rates by Exchange Rate API" with a link wherever the
rates are shown (the Exchange rates page does this).

Fetched rates are saved as Murimi-wide rates (no organisation) with source
"online". A farm's own rate for the same day still wins, and a rate typed in
by Murimi staff is never overwritten.

Change the address with MURIMI_RATES_URL, or switch fetching off with
MURIMI_RATES_AUTO=False in .env.
"""
import datetime
import json
import logging
import urllib.request
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from .models import ExchangeRate

log = logging.getLogger(__name__)
DEFAULT_URL = "https://open.er-api.com/v6/latest/USD"
ATTRIBUTION = {"text": "Rates by Exchange Rate API", "url": "https://www.exchangerate-api.com"}
# Refuse obviously broken numbers instead of saving them.
SANE = {"ZWG": (Decimal("1"), Decimal("10000")), "ZAR": (Decimal("5"), Decimal("100"))}


class FetchFailed(Exception):
    pass


def _download(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "MurimiFarmERP/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_latest(timeout=8):
    """Download and save today's rates. Returns the list of ExchangeRate rows saved or updated."""
    url = getattr(settings, "MURIMI_RATES_URL", "") or DEFAULT_URL
    try:
        data = _download(url, timeout)
    except Exception as e:  # no network, timeout, bad JSON...
        raise FetchFailed("Couldn't reach the online rates service. Check the internet connection, "
                          "or type the rate in yourself.") from e
    if data.get("result") != "success" or not isinstance(data.get("rates"), dict):
        raise FetchFailed("The online rates service didn't send rates today. Type the rate in yourself.")
    try:
        when = datetime.datetime.fromtimestamp(int(data["time_last_update_unix"]), tz=datetime.timezone.utc)
        on = when.astimezone(ZoneInfo(settings.TIME_ZONE)).date()
    except (KeyError, TypeError, ValueError):
        on = timezone.localdate()
    on = min(on, timezone.localdate())
    saved = []
    with transaction.atomic():
        for cur in ("ZWG", "ZAR"):
            raw = data["rates"].get(cur)
            try:
                value = Decimal(str(raw)).quantize(Decimal("0.000001"))
            except (InvalidOperation, TypeError):
                continue
            low, high = SANE[cur]
            if not (low <= value <= high):
                log.warning("Ignoring odd %s rate from %s: %s", cur, url, raw)
                continue
            existing = ExchangeRate.objects.filter(organisation__isnull=True, date=on, currency=cur).first()
            if existing and existing.source != ExchangeRate.ONLINE:
                continue  # typed in by Murimi staff: keep theirs
            note = f"From exchangerate-api.com, {on:%d %b %Y}"
            if existing:
                if existing.units_per_usd != value:
                    existing.units_per_usd, existing.note = value, note
                    existing.save(update_fields=["units_per_usd", "note"])
                saved.append(existing)
            else:
                saved.append(ExchangeRate.objects.create(organisation=None, date=on, currency=cur, units_per_usd=value,
                                                         source=ExchangeRate.ONLINE, note=note))
    if not saved and not ExchangeRate.objects.filter(organisation__isnull=True, date=on).exists():
        raise FetchFailed("The online service had no usable ZiG or rand rate today.")
    return saved


def fetch_if_due():
    """Called when someone opens the dashboard: fetch at most every 3 hours, and only
    if there is no Murimi-wide ZiG rate for today yet. Never raises."""
    if not getattr(settings, "MURIMI_RATES_AUTO", True):
        return
    today = timezone.localdate()
    if ExchangeRate.objects.filter(organisation__isnull=True, currency="ZWG", date=today).exists():
        return
    if not cache.add("murimi-rates-fetch", True, 60 * 60 * 3):
        return
    try:
        fetch_latest(timeout=5)
    except FetchFailed as e:
        log.info("Automatic rate fetch failed: %s", e)
