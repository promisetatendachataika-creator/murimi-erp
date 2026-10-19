"""Sales, purchases, VAT, harvests and reports. Figures worked out by hand first."""
import datetime
from decimal import Decimal

from erp_core.tests import Base
from erp_finance.models import ExchangeRate
from erp_inventory.models import Batch
from murimi_common.testing import ApiClient, make_user

from .models import Document


class TradeBase(Base):
    def setUp(self):
        super().setUp()
        self.T = self.base + "trade/"
        r = self.api.patch(self.base, {"vat_number": "220123456", "tin": "2000123456"})
        self.assertEqual(r.status_code, 200, r.content)
        ExchangeRate.objects.create(date=datetime.date(2026, 9, 1), currency="ZWG", units_per_usd=Decimal("26.85"))
        self.farm = self.add_farm()
        self.field = self.add_field(self.farm, size="20")
        self.season = self.add_season()
        self.plan = self.api.post(self.base + "crop-plans/", {"season": self.season, "field": self.field, "crop": "Maize",
                                                                "planted_ha": "20", "target_yield_t_per_ha": "8",
                                                                "budget_amount": "30000", "budget_currency": "USD"}).json()["id"]

    def contact(self, name, kind="customer"):
        from .models import Contact
        old = Contact.objects.filter(name=name).first()
        if old:
            return str(old.id)
        r = self.api.post(self.T + "contacts/", {"name": name, "kind": kind})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()["id"]

    def sale(self, lines, **kw):
        r = self.api.post(self.T + "documents/", {"kind": "sale", "contact": kw.pop("contact", None) or self.contact("GMB Marondera"),
                                                  "date": "2026-10-10", "currency": "USD", "lines": lines, **kw})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def act(self, doc, action, **data):
        return self.api.post(self.T + f"documents/{doc['id']}/{action}/", data)


class VatTests(TradeBase):
    def test_line_maths_standard_inclusive_exempt_and_old_rate(self):
        d = self.sale([{"description": "Maize grain", "quantity": "10", "unit": "tonne", "unit_price": "100"}])
        self.assertEqual((d["net"], d["vat"], d["total"]), ("1000.00", "155.00", "1155.00"))
        d = self.sale([{"description": "Maize grain", "quantity": "1", "unit_price": "1155"}], prices_include_vat=True)
        self.assertEqual((d["net"], d["vat"]), ("1000.00", "155.00"))
        d = self.sale([{"description": "Green maize", "quantity": "2", "unit_price": "50", "vat_category": "exempt"},
                       {"description": "Delivery", "quantity": "1", "unit_price": "20"}])
        self.assertEqual((d["net"], d["vat"], d["total"]), ("120.00", "3.10", "123.10"))
        d = self.sale([{"description": "Old sale", "quantity": "1", "unit_price": "100"}], date="2025-12-31")
        self.assertEqual(d["vat"], "15.00")  # 15% before 2026

    def test_not_registered_charges_no_vat(self):
        self.api.patch(self.base, {"vat_number": ""})
        d = self.sale([{"description": "Maize", "quantity": "1", "unit_price": "100"}])
        self.assertEqual((d["vat"], d["total"], d["vat_registered"]), ("0.00", "100.00", False))

    def test_vat_report_only_claims_with_fiscal_invoice(self):
        s = self.sale([{"description": "Maize", "quantity": "10", "unit_price": "100"},
                       {"description": "Soya", "quantity": "1", "unit_price": "500", "vat_category": "exempt"}])
        self.assertEqual(self.act(s, "issue").status_code, 200)
        sup = self.contact("Windmill", "supplier")
        for fiscal_no in ("FR-1001", ""):
            r = self.api.post(self.T + "documents/", {"kind": "purchase", "contact": sup, "date": "2026-10-05", "currency": "USD",
                                                       "number": "WM-" + (fiscal_no or "x"), "fiscal_receipt_no": fiscal_no,
                                                       "lines": [{"description": "Tractor service", "category": "repairs",
                                                                  "quantity": "1", "unit_price": "200"}]})
            self.assertEqual(r.status_code, 201, r.content)
        rep = self.api.get(self.T + "reports/vat/", {"date_from": "2026-10-01", "date_to": "2026-10-31"}).json()["by_currency"]["USD"]
        # output 155.00; input 31.00 claimable (fiscal) + 31.00 not claimable; payable 124.00
        self.assertEqual((rep["output_vat"], rep["input_claimable"], rep["input_not_claimable"], rep["payable"]),
                         ("155.00", "31.00", "31.00", "124.00"))
        self.assertEqual(rep["sales"]["exempt"]["net"], "500.00")
        self.assertEqual(len(rep["missing_fiscal"]), 1)


class FlowTests(TradeBase):
    def test_issue_numbers_stock_out_payments_and_void(self):
        store = self.api.post(self.base + "stores/", {"name": "Grain shed", "kind": "store"}).json()["id"]
        item = self.api.post(self.base + "items/", {"name": "Maize grain", "category": "other", "unit": "tonne"}).json()["id"]
        h = self.api.post(self.T + "harvests/", {"crop_plan": self.plan, "date": "2026-10-01", "tonnes": "30", "item": item, "store": store})
        self.assertEqual(h.status_code, 201, h.content)
        self.assertEqual(h.json()["quantity"], "30.000")
        s1 = self.sale([{"description": "Maize grain", "quantity": "12", "unit_price": "290", "item": item, "store": store,
                         "crop_plan": self.plan, "vat_category": "exempt"}], due_date="2026-10-17")
        self.assertEqual(s1["status"], "draft")
        r = self.act(s1, "issue")
        self.assertEqual((r.status_code, r.json()["number"]), (200, "INV-00001"), r.content)
        self.assertEqual(sum(b.qty_remaining for b in Batch.objects.all()), Decimal("18"))
        s2 = self.sale([{"description": "Maize grain", "quantity": "50", "unit_price": "290", "item": item, "store": store}])
        r = self.act(s2, "issue")
        self.assertEqual(r.status_code, 400)  # only 18 t left
        self.assertIn("Only 18", r.json()["error"])
        self.assertEqual(Document.objects.get(pk=s2["id"]).number, "")  # nothing half-done
        # payments
        p = self.api.post(self.T + f"documents/{s1['id']}/payments/", {"date": "2026-10-12", "amount": "5000", "method": "bank"})
        self.assertEqual(p.status_code, 400)
        p = self.api.post(self.T + f"documents/{s1['id']}/payments/", {"date": "2026-10-12", "amount": "3000", "method": "bank"})
        self.assertEqual(p.json()["balance"], "480.00")
        owing = self.api.get(self.T + "reports/owing/").json()["customers_owe_us"]
        self.assertEqual(owing["total_usd"], "480.00")
        # void needs payments removed, and a fiscalised invoice needs a credit note
        self.assertEqual(self.act(s1, "void", reason="wrong price").status_code, 400)
        self.api.delete(self.T + f"payments/{p.json()['payments'][0]['id']}/")
        self.assertEqual(self.act(s1, "fiscalise", fiscal_receipt_no="00045/0001", fiscal_code="ABCD-1234").status_code, 200)
        self.assertEqual(self.act(s1, "void", reason="wrong price").status_code, 400)
        r = self.act(s1, "void", reason="Credit note 00045/0002, wrong price")
        self.assertEqual(r.json()["status"], "void")
        self.assertEqual(sum(b.qty_remaining for b in Batch.objects.all()), Decimal("30"))  # stock back

    def test_purchase_in_zig_puts_stock_in_at_net_cost(self):
        sup = self.contact("Farm & City", "supplier")
        store = self.api.post(self.base + "stores/", {"name": "Main store", "kind": "store"}).json()["id"]
        item = self.api.post(self.base + "items/", {"name": "Compound D", "category": "fertiliser", "unit": "50 kg bag"}).json()["id"]
        r = self.api.post(self.T + "documents/", {"kind": "purchase", "contact": sup, "date": "2026-10-02", "currency": "ZWG",
                                                   "number": "FC-889", "prices_include_vat": True,
                                                   "lines": [{"quantity": "10", "unit_price": "830.2425", "item": item, "store": store}]})
        self.assertEqual(r.status_code, 201, r.content)
        # 8302.43 ZiG incl VAT -> net 7188.25 ZiG; one bag 718.825 ZiG = US$26.77
        self.assertEqual((r.json()["net"], r.json()["status"]), ("7188.25", "issued"))
        b = Batch.objects.get()
        self.assertEqual((b.qty_remaining, b.currency, round(b.unit_cost_usd, 2)), (Decimal("10"), "ZWG", Decimal("26.77")))

    def test_crop_report_and_profit(self):
        self.api.post(self.T + "harvests/", {"crop_plan": self.plan, "date": "2026-10-01", "tonnes": "150"})
        s = self.sale([{"description": "Maize", "quantity": "150", "unit_price": "300", "crop_plan": self.plan, "vat_category": "exempt"}])
        self.act(s, "issue")
        sup = self.contact("Tillage Co", "supplier")
        self.api.post(self.T + "documents/", {"kind": "purchase", "contact": sup, "date": "2026-10-03", "currency": "USD",
                                              "number": "T-1", "lines": [{"description": "Ploughing 20 ha", "category": "contractors",
                                                                          "quantity": "20", "unit_price": "60", "crop_plan": self.plan}]})
        row = self.api.get(self.T + "reports/crops/", {"season": self.season}).json()["plans"][0]
        # revenue 45,000; cost = ploughing 1,200; 150 t on 20 ha = 7.5 t/ha; cost per t 8.00
        self.assertEqual((row["revenue_usd"], row["other_usd"], row["cost_usd"], row["t_per_ha"], row["cost_per_t"], row["profit_usd"]),
                         ("45000.00", "1200.00", "1200.00", "7.50", "8.00", "43800.00"))
        pnl = self.api.get(self.T + "reports/profit/", {"date_from": "2026-10-01", "date_to": "2026-10-31"}).json()
        self.assertEqual((pnl["total_income"], pnl["total_costs"], pnl["profit"]), ("45000.00", "1200.00", "43800.00"))

    def test_roles(self):
        self.contact("GMB Marondera")
        for phone, role in (("0771000002", "storekeeper"), ("0771000003", "supervisor"), ("0771000004", "viewer")):
            make_user(phone, role.title())
            self.api.post(self.base + "members/", {"login": phone, "role": role})
        store = ApiClient(make_user.__globals__["User"].objects.get(phone__endswith="771000002"))
        sup = ApiClient(make_user.__globals__["User"].objects.get(phone__endswith="771000003"))
        view = ApiClient(make_user.__globals__["User"].objects.get(phone__endswith="771000004"))
        self.assertEqual(store.post(self.T + "documents/", {"kind": "sale"}).status_code, 403)
        self.assertEqual(store.get(self.T + "documents/", {"kind": "sale"}).json()["results"], [])
        self.assertEqual(sup.post(self.T + "harvests/", {"crop_plan": self.plan, "date": "2026-10-01", "tonnes": "2"}).status_code, 201)
        self.assertEqual(view.get(self.T + "reports/profit/").status_code, 403)
        self.assertEqual(ApiClient(make_user("0771000099", "Stranger")).get(self.T + "documents/").status_code, 404)

    def test_draft_edit_and_delete(self):
        d = self.sale([{"description": "Maize", "quantity": "1", "unit_price": "100"}])
        r = self.api.patch(self.T + f"documents/{d['id']}/", {"lines": [{"description": "Maize", "quantity": "2", "unit_price": "100"}]})
        self.assertEqual(r.json()["total"], "231.00")
        self.assertEqual(self.api.delete(self.T + f"documents/{d['id']}/").status_code, 200)


class ReplayTests(TradeBase):
    """A phone with weak signal may send the same record twice. It must be saved once."""

    def test_harvest_transfer_and_count_saved_once(self):
        from erp_inventory.models import Movement
        from .models import Harvest
        hid = "11111111-2222-4333-8444-555555555555"
        for _ in range(2):
            r = self.api.post(self.T + "harvests/", {"id": hid, "crop_plan": self.plan, "date": "2026-10-01", "tonnes": "5"})
            self.assertIn(r.status_code, (200, 201), r.content)
        self.assertEqual(Harvest.objects.count(), 1)
        a = self.api.post(self.base + "stores/", {"name": "A", "kind": "store"}).json()["id"]
        b = self.api.post(self.base + "stores/", {"name": "B", "kind": "store"}).json()["id"]
        item = self.api.post(self.base + "items/", {"name": "Compound D", "category": "fertiliser", "unit": "50 kg bag"}).json()["id"]
        self.api.post(self.base + "stock/receive/", {"item": item, "store": a, "quantity": "10", "unit_cost": "30", "currency": "USD", "date": "2026-10-01"})
        mv = "21111111-2222-4333-8444-555555555555"
        for _ in range(2):
            r = self.api.post(self.base + "stock/transfer/", {"id": mv, "item": item, "from_store": a, "to_store": b, "quantity": "4", "date": "2026-10-02"})
            self.assertIn(r.status_code, (200, 201), r.content)
        cnt = "31111111-2222-4333-8444-555555555555"
        for _ in range(2):
            r = self.api.post(self.base + "stock/count/", {"id": cnt, "item": item, "store": a, "counted": "5", "date": "2026-10-03", "reason": "bag burst"})
            self.assertIn(r.status_code, (200, 201), r.content)
        self.assertEqual(Movement.objects.filter(group=mv).count(), 2)  # one out, one in
        self.assertEqual(Movement.objects.filter(group=cnt).count(), 1)
        on = {r["store_name"]: r["qty"] for r in self.api.get(self.base + "stock/").json()["results"]}
        self.assertEqual(on, {"A": "5.000", "B": "4.000"})


class PwaTests(TradeBase):
    def test_service_worker_and_manifest(self):
        r = self.client.get("/sw.js")
        self.assertEqual(r["Content-Type"], "application/javascript")
        self.assertEqual(r["Service-Worker-Allowed"], "/")
        self.assertIn(b"murimi-erp-", r.content)
        page = self.client.get("/").content
        self.assertIn(b"manifest.webmanifest", page)


class AssetLinksTests(TradeBase):
    def test_assetlinks_lists_the_apk_key(self):
        from django.test import override_settings
        self.assertEqual(self.client.get("/.well-known/assetlinks.json").json(), [])
        with override_settings(ANDROID_CERT_SHA256=["AB:CD"]):
            data = self.client.get("/.well-known/assetlinks.json").json()
        self.assertEqual(data[0]["target"]["package_name"], "zw.murimi.erp")
        self.assertEqual(data[0]["target"]["sha256_cert_fingerprints"], ["AB:CD"])
