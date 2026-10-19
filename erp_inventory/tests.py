import datetime
from decimal import Decimal

from django.utils import timezone

from erp_core.tests import Base
from murimi_common.testing import ApiClient, make_user

from .models import Batch, Movement

D = lambda s: Decimal(s)  # noqa: E731


class StockBase(Base):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.d = lambda n: (self.today - datetime.timedelta(days=n)).isoformat()
        self.store = self.make("stores/", {"name": "Main store"})
        self.shed = self.make("stores/", {"name": "Chemical shed"})
        self.tank = self.make("stores/", {"name": "Diesel tank", "kind": "tank", "capacity": "5000"})
        self.fert = self.make("items/", {"name": "Compound D", "category": "fertiliser", "unit": "50 kg bag", "reorder_level": "10"})
        self.diesel = self.make("items/", {"name": "Diesel", "category": "fuel", "unit": "L"})
        self.tractor = self.make("equipment/", {"name": "Tractor 2", "kind": "tractor", "meter": "hours"})
        self.api.post(self.base + "rates/", {"date": self.d(30), "currency": "ZWG", "units_per_usd": "26.85"})

    def make(self, path, data):
        r = self.api.post(self.base + path, data)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()["id"]

    def receive(self, item, store, qty, cost, currency="USD", days_ago=20, **kw):
        r = self.api.post(self.base + "stock/receive/", {"item": item, "store": store, "quantity": qty, "unit_cost": cost,
                                                       "currency": currency, "date": self.d(days_ago), **kw})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def issue(self, item, store, qty, days_ago=5, **kw):
        return self.api.post(self.base + "stock/issue/", {"item": item, "store": store, "quantity": qty,
                                                        "date": self.d(days_ago), **kw})

    def on_hand(self):
        return {(r["item_name"], r["store_name"]): r for r in self.api.get(self.base + "stock/").json()["results"]}


class ReceiveIssueTests(StockBase):
    def test_zig_purchase_is_valued_in_usd_at_that_days_rate(self):
        self.receive(self.fert, self.store, "10", "805.50", currency="ZWG")  # ZiG 805.50 a bag at 26.85 = US$30
        b = Batch.objects.get()
        self.assertEqual(b.unit_cost_usd, D("30.000000"))
        self.assertEqual(b.rate_zwg, D("26.850000"))
        row = self.on_hand()[("Compound D", "Main store")]
        self.assertEqual((row["qty"], row["value_usd"], row["value_zwg_bought"]), ("10.000", "300.00", "8055.00"))
        # A new rate later changes "today's ZiG" value but never the cost that was paid.
        self.api.post(self.base + "rates/", {"date": self.d(0), "currency": "ZWG", "units_per_usd": "30"})
        row = self.on_hand()[("Compound D", "Main store")]
        self.assertEqual((row["value_usd"], row["value_zwg_bought"], row["value_zwg_today"]), ("300.00", "8055.00", "9000.00"))

    def test_fifo_takes_oldest_batch_first_and_costs_follow(self):
        self.receive(self.fert, self.store, "10", "30", days_ago=20)
        self.receive(self.fert, self.store, "10", "36", days_ago=10)
        r = self.issue(self.fert, self.store, "14")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["value_usd"], "-444.00")  # 10 x 30 + 4 x 36
        row = self.on_hand()[("Compound D", "Main store")]
        self.assertEqual((row["qty"], row["value_usd"]), ("6.000", "216.00"))

    def test_cannot_use_more_than_is_there(self):
        self.receive(self.fert, self.store, "3", "30")
        r = self.issue(self.fert, self.store, "5")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Only 3 × 50 kg bag of Compound D left", r.json()["error"])
        self.assertEqual(Movement.objects.filter(kind="issue").count(), 0)

    def test_zig_purchase_needs_a_rate_and_dates_are_checked(self):
        r = self.api.post(self.base + "stock/receive/", {"item": self.fert, "store": self.store, "quantity": "1",
                                                       "unit_cost": "100", "currency": "ZWG", "date": self.d(60)})
        self.assertIn("no ZiG rate", r.json()["error"])
        tomorrow = (self.today + datetime.timedelta(days=1)).isoformat()
        r = self.api.post(self.base + "stock/receive/", {"item": self.fert, "store": self.store, "quantity": "1",
                                                       "unit_cost": "1", "currency": "USD", "date": tomorrow})
        self.assertIn("future", r.json()["error"])

    def test_inputs_used_on_a_crop_plan_are_costed(self):
        farm = self.add_farm()
        field = self.add_field(farm, size="20")
        season = self.add_season()
        plan = self.api.post(self.base + "crop-plans/", {"season": season, "field": field, "crop": "Maize",
                                                         "planted_ha": "20", "budget_amount": "20000"}).json()["id"]
        self.receive(self.fert, self.store, "40", "30")
        self.issue(self.fert, self.store, "16", crop_plan=plan, rate_per_ha="0.8")
        costs = self.api.get(self.base + "stock/plan-costs/", {"season": season}).json()["plans"]
        self.assertEqual(costs[plan]["inputs_usd"], "480.00")
        self.assertEqual(costs[plan]["by_category"]["fertiliser"], "480.00")
        mv = Movement.objects.get(kind="issue")
        self.assertEqual(str(mv.field_id), field)  # the field comes from the plan

    def test_chemical_safety_intervals_are_returned(self):
        chem = self.make("items/", {"name": "Karate", "category": "chemical", "unit": "L", "reentry_hours": "24",
                                    "preharvest_days": "14", "active_ingredient": "lambda-cyhalothrin"})
        self.receive(chem, self.shed, "5", "22")
        r = self.issue(chem, self.shed, "1", days_ago=0)
        body = r.json()
        self.assertIn("24 hours", body["safe_reentry"])
        self.assertEqual(body["earliest_harvest"], (self.today + datetime.timedelta(days=14)).isoformat())

    def test_same_offline_id_is_saved_once(self):
        self.receive(self.fert, self.store, "10", "30")
        gid = "11111111-2222-4333-8444-555555555555"
        for _ in range(2):
            r = self.issue(self.fert, self.store, "2", id=gid)
            self.assertIn(r.status_code, (200, 201))
        self.assertEqual(self.on_hand()[("Compound D", "Main store")]["qty"], "8.000")


class TransferCountUndoTests(StockBase):
    def test_transfer_keeps_cost_and_age(self):
        self.receive(self.fert, self.store, "10", "30", days_ago=20)
        r = self.api.post(self.base + "stock/transfer/", {"item": self.fert, "from_store": self.store,
                                                        "to_store": self.shed, "quantity": "4", "date": self.d(3)})
        self.assertEqual(r.status_code, 201, r.content)
        oh = self.on_hand()
        self.assertEqual(oh[("Compound D", "Main store")]["qty"], "6.000")
        self.assertEqual(oh[("Compound D", "Chemical shed")]["value_usd"], "120.00")
        moved = Batch.objects.get(store_id=self.shed)
        self.assertEqual(moved.received_on, self.today - datetime.timedelta(days=20))

    def test_stock_take_down_and_up(self):
        self.receive(self.fert, self.store, "10", "30")
        r = self.api.post(self.base + "stock/count/", {"item": self.fert, "store": self.store, "counted": "8",
                                                     "date": self.d(1), "reason": "Two bags burst"})
        self.assertEqual(r.json()["value_usd"], "-60.00")
        r = self.api.post(self.base + "stock/count/", {"item": self.fert, "store": self.store, "counted": "8",
                                                     "date": self.d(1), "reason": "x"})
        self.assertIn("matches", r.json()["error"])
        r = self.api.post(self.base + "stock/count/", {"item": self.fert, "store": self.store, "counted": "9",
                                                     "date": self.d(1), "reason": "Found one behind the door"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.on_hand()[("Compound D", "Main store")]["qty"], "9.000")
        r = self.api.post(self.base + "stock/count/", {"item": self.fert, "store": self.store, "counted": "1", "date": self.d(1)})
        self.assertIn("reason", r.json()["errors"])

    def test_undo_issue_and_receipt_rules(self):
        rec = self.receive(self.fert, self.store, "10", "30")
        iss = self.issue(self.fert, self.store, "4").json()
        # The delivery can't be undone once some of it was used...
        r = self.api.post(self.base + f"stock/movements/{rec['group']}/undo/", {"reason": "wrong price"})
        self.assertIn("already been used", r.json()["error"])
        # ...but the use can, and then the delivery can.
        r = self.api.post(self.base + f"stock/movements/{iss['group']}/undo/", {"reason": "wrong field"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.on_hand()[("Compound D", "Main store")]["qty"], "10.000")
        r = self.api.post(self.base + f"stock/movements/{iss['group']}/undo/", {"reason": "again"})
        self.assertIn("already been undone", r.json()["error"])
        r = self.api.post(self.base + f"stock/movements/{rec['group']}/undo/", {"reason": "wrong price"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.on_hand(), {})
        rows = self.api.get(self.base + "stock/movements/").json()["results"]
        self.assertTrue(any(x["reversed"] for x in rows))
        self.assertEqual(Movement.objects.count(), 4)  # nothing deleted: 2 records + 2 undos


class RolesAndAlertsTests(StockBase):
    def test_supervisor_can_use_stock_but_not_receive_or_undo(self):
        self.receive(self.fert, self.store, "10", "30")
        make_user("0771000005", "Farai Supervisor")
        self.api.post(self.base + "members/", {"login": "0771000005", "role": "supervisor"})
        from accounts.models import User
        s = ApiClient(User.objects.get(phone="+263771000005"))
        self.assertEqual(s.post(self.base + "stock/issue/", {"item": self.fert, "store": self.store, "quantity": "1",
                                                            "date": self.d(1), "reason": "Nursery"}).status_code, 201)
        r = s.post(self.base + "stock/receive/", {"item": self.fert, "store": self.store, "quantity": "1",
                                                 "unit_cost": "1", "currency": "USD", "date": self.d(1)})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(s.post(self.base + "items/", {"name": "X", "category": "seed", "unit": "kg"}).status_code, 403)

    def test_other_business_cannot_see_stock(self):
        self.receive(self.fert, self.store, "10", "30")
        other = ApiClient(make_user("0779999998", "Other Farmer"))
        other.post("/api/erp/orgs/", {"name": "Other Estates"})
        for url in ("stock/", "stock/movements/", "fuel/", "items/"):
            self.assertEqual(other.get(self.base + url).status_code, 404)

    def test_low_stock_and_expiry_alerts(self):
        self.receive(self.fert, self.store, "8", "30")  # below the warning level of 10
        chem = self.make("items/", {"name": "Dimethoate", "category": "chemical", "unit": "L"})
        self.receive(chem, self.shed, "2", "15", expiry=(self.today + datetime.timedelta(days=10)).isoformat())
        a = self.api.get(self.base + "stock/alerts/").json()
        self.assertEqual(a["low_stock"][0]["name"], "Compound D")
        self.assertEqual(a["expiring"][0]["days"], 10)


class FuelTests(StockBase):
    def test_use_per_hour_and_warnings(self):
        self.receive(self.diesel, self.tank, "2000", "1.45", days_ago=25)
        fills = [("100", "1000", 20), ("60", "1010", 18), ("62", "1020", 16), ("58", "1030", 14), ("110", "1040", 12),
                 ("50", "1035", 10), ("40", None, 8)]
        for litres, reading, ago in fills:
            r = self.issue(self.diesel, self.tank, litres, days_ago=ago, equipment=self.tractor,
                           **({"meter_reading": reading} if reading else {}))
            self.assertEqual(r.status_code, 201, r.content)
        f = self.api.get(self.base + "fuel/").json()
        t = f["machines"][0]
        self.assertEqual(t["usual_use"], "6.10")  # median of 6.0, 6.2, 5.8, 11.0 litres per hour
        flags = {fill["reading"]: fill["flags"] for fill in t["fills"]}
        self.assertTrue(any("Much higher" in x for x in flags["1040.0"]))
        self.assertIn("Meter went backwards", flags["1035.0"])
        self.assertIn("No meter reading", flags[None])
        self.assertEqual(t["flags"], 3)
        self.assertEqual(f["tanks"][0]["litres"], "1520.0")
        self.assertEqual(self.api.get(self.base + "stock/alerts/").json()["fuel_flags"], 3)

    def test_fuel_needs_a_machine(self):
        self.receive(self.diesel, self.tank, "100", "1.45")
        r = self.issue(self.diesel, self.tank, "10")
        self.assertIn("equipment", r.json()["errors"])
