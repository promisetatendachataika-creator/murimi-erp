import datetime
from decimal import Decimal

from django.test import TestCase

from erp_core.models import Organisation
from erp_core.tests import Base
from murimi_common.testing import ApiClient, make_user

from .models import ExchangeRate
from .money import RateMissing, convert, q2


class MoneyTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Test Farm")

    def test_rounding_is_half_up_and_decimal(self):
        self.assertEqual(q2(Decimal("2.345")), Decimal("2.35"))
        self.assertEqual(q2(Decimal("2.344")), Decimal("2.34"))
        self.assertEqual(q2(Decimal("0.1") + Decimal("0.2")), Decimal("0.30"))

    def test_conversion_uses_newest_rate_and_org_rate_wins(self):
        d = datetime.date
        ExchangeRate.objects.create(date=d(2026, 10, 1), currency="ZWG", units_per_usd=Decimal("26.50"))
        ExchangeRate.objects.create(date=d(2026, 10, 10), currency="ZWG", units_per_usd=Decimal("26.80"))
        c = convert(Decimal("100"), "USD", "ZWG", d(2026, 10, 12), self.org)
        self.assertEqual(c.result, Decimal("2680.00"))
        c = convert(Decimal("100"), "USD", "ZWG", d(2026, 10, 5), self.org)  # older rate for an older date
        self.assertEqual(c.result, Decimal("2650.00"))
        ExchangeRate.objects.create(organisation=self.org, date=d(2026, 10, 10), currency="ZWG",
                                    units_per_usd=Decimal("27.00"), source="bank")
        c = convert(Decimal("100"), "USD", "ZWG", d(2026, 10, 12), self.org)
        self.assertEqual(c.result, Decimal("2700.00"))
        self.assertFalse(c.as_dict()["rates_used"][0]["murimi_wide"])
        # ZWG -> ZAR goes through USD.
        ExchangeRate.objects.create(date=d(2026, 10, 10), currency="ZAR", units_per_usd=Decimal("17.50"))
        c = convert(Decimal("2700"), "ZWG", "ZAR", d(2026, 10, 12), self.org)
        self.assertEqual(c.result, Decimal("1750.00"))
        self.assertEqual(len(c.rates), 2)

    def test_missing_rate_is_explained(self):
        with self.assertRaises(RateMissing):
            convert(Decimal("1"), "USD", "ZWG", datetime.date(2026, 1, 1), self.org)


class RateApiTests(Base):
    def test_add_list_convert_delete(self):
        r = self.api.post(self.base + "rates/", {"date": "2026-10-15", "currency": "ZWG", "units_per_usd": "26.85",
                                                 "source": "bank"})
        self.assertEqual(r.status_code, 201, r.content)
        rid = r.json()["id"]
        r = self.api.post(self.base + "rates/", {"date": "2026-10-15", "currency": "ZWG", "units_per_usd": "27"})
        self.assertEqual(r.status_code, 400)  # one per day
        r = self.api.get(self.base + "convert/", {"amount": "250", "from": "USD", "to": "ZWG", "date": "2026-10-16"})
        self.assertEqual(r.json()["result"], "6712.50")
        r = self.api.get(self.base + "convert/", {"amount": "250", "from": "USD", "to": "ZAR", "date": "2026-10-16"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.api.delete(self.base + f"rates/{rid}/").status_code, 200)

    def test_future_and_bad_rates_and_roles(self):
        r = self.api.post(self.base + "rates/", {"date": "2099-01-01", "currency": "ZWG", "units_per_usd": "0"})
        self.assertEqual(set(r.json()["errors"]), {"date", "units_per_usd"})
        viewer = make_user("0771000009", "Viewer Person")
        self.api.post(self.base + "members/", {"login": "0771000009", "role": "viewer"})
        v = ApiClient(viewer)
        r = v.post(self.base + "rates/", {"date": "2026-10-15", "currency": "ZWG", "units_per_usd": "26"})
        self.assertEqual(r.status_code, 403)
        g = ExchangeRate.objects.create(date=datetime.date(2026, 10, 1), currency="ZWG", units_per_usd=Decimal("26"))
        self.assertEqual(self.api.delete(self.base + f"rates/{g.id}/").status_code, 404)  # Murimi-wide: staff only


class FetchTests(Base):
    """Online rates, with the internet replaced by a fake answer."""

    def fake(self, zwg=26.6072, zar=16.4652, result="success"):
        import time
        return {"result": result, "time_last_update_unix": int(time.time()), "rates": {"USD": 1, "ZWG": zwg, "ZAR": zar}}

    def test_fetch_saves_murimi_wide_rates_and_never_overwrites_staff_rates(self):
        from unittest import mock
        from django.utils import timezone
        from . import fetch
        today = timezone.localdate()
        ExchangeRate.objects.create(date=today, currency="ZAR", units_per_usd=Decimal("17"), source="official")
        with mock.patch.object(fetch, "_download", return_value=self.fake()):
            saved = fetch.fetch_latest()
        self.assertEqual([r.currency for r in saved], ["ZWG"])
        z = ExchangeRate.objects.get(organisation__isnull=True, date=today, currency="ZWG")
        self.assertEqual((z.units_per_usd, z.source), (Decimal("26.607200"), "online"))
        self.assertEqual(ExchangeRate.objects.get(organisation__isnull=True, currency="ZAR").units_per_usd, Decimal("17"))
        # Fetching again later in the day updates the online rate instead of failing.
        with mock.patch.object(fetch, "_download", return_value=self.fake(zwg=26.7)):
            fetch.fetch_latest()
        self.assertEqual(ExchangeRate.objects.filter(currency="ZWG").count(), 1)
        self.assertEqual(ExchangeRate.objects.get(currency="ZWG").units_per_usd, Decimal("26.700000"))

    def test_bad_answers_are_refused(self):
        from unittest import mock
        from . import fetch
        with mock.patch.object(fetch, "_download", return_value=self.fake(zwg=0.0001, zar="x")):
            with self.assertRaises(fetch.FetchFailed):
                fetch.fetch_latest()
        with mock.patch.object(fetch, "_download", side_effect=OSError("no network")):
            with self.assertRaises(fetch.FetchFailed):
                fetch.fetch_latest()
        self.assertFalse(ExchangeRate.objects.exists())

    def test_button_needs_rate_permission_and_reports_failures(self):
        from unittest import mock
        from . import fetch
        url = self.base + "rates/fetch/"
        with mock.patch.object(fetch, "_download", return_value=self.fake()):
            r = self.api.post(url, {})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(r.json()["results"]), 2)
        with mock.patch.object(fetch, "_download", side_effect=OSError):
            r = self.api.post(url, {})
        self.assertEqual(r.status_code, 502)
        self.assertIn("internet", r.json()["error"])
        # Users can't save an "online" rate by hand.
        r = self.api.post(self.base + "rates/", {"date": "2026-10-01", "currency": "ZWG",
                                                                       "units_per_usd": "26", "source": "online"})
        self.assertEqual(r.status_code, 400)
