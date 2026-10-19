"""Labour and payroll. The pay figures here were worked out by hand from the rules
in payroll.py, so if a rule changes by accident a test fails."""
import datetime
from decimal import Decimal

from erp_core.tests import Base
from erp_finance.models import ExchangeRate
from murimi_common.testing import ApiClient, make_user

from . import payroll
from .models import PayRun, WorkEntry

SEP = {"period_start": "2026-09-01", "period_end": "2026-09-30", "pay_date": "2026-09-30"}


class LabourBase(Base):
    def setUp(self):
        super().setUp()
        self.L = self.base + "labour/"

    def worker(self, name, **kw):
        data = {"full_name": name, "pay_basis": "monthly", "rate": "500", "currency": "USD", "kind": "permanent", **kw}
        r = self.api.post(self.L + "workers/", data)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()["id"]

    def task(self, name="Picking cotton", unit="kg", rate="0.05"):
        r = self.api.post(self.L + "tasks/", {"name": name, "unit": unit, "rate": rate, "currency": "USD"})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()["id"]

    def payrun(self, **kw):
        r = self.api.post(self.L + "pay-runs/", {**SEP, **kw})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def slip(self, run, name):
        return next(s for s in run["payslips"] if s["worker_name"] == name)


class MathsTests(LabourBase):
    def test_paye_bands_follow_zimra_layout(self):
        bands = [[100, 0, 0], [300, 20, 20], [1000, 25, 35], [2000, 30, 85], [3000, 35, 185], [None, 40, 335]]
        self.assertEqual(payroll.paye(bands, Decimal("100"))[0], Decimal("0"))
        self.assertEqual(payroll.paye(bands, Decimal("300"))[0], Decimal("40.00"))     # 300 x 20% - 20
        self.assertEqual(payroll.paye(bands, Decimal("500"))[0], Decimal("90.00"))     # 500 x 25% - 35
        self.assertEqual(payroll.paye(bands, Decimal("5000"))[0], Decimal("1665.00"))  # 5000 x 40% - 335
        # Half a month: the bands shrink too, so 150 is taxed like 300 in a full month.
        self.assertEqual(payroll.paye(bands, Decimal("150"), Decimal("0.5"))[0], Decimal("20.00"))

    def test_period_scale(self):
        d = datetime.date
        self.assertEqual(payroll.period_scale(d(2026, 2, 1), d(2026, 2, 28)), Decimal("1"))
        self.assertEqual(payroll.period_scale(d(2026, 9, 1), d(2026, 9, 14)), Decimal("0.4600"))

    def test_monthly_salary_payslip(self):
        self.worker("Farai Ncube", grade="B1")
        self.worker("Grace Mutasa", rate="2000", job_title="Farm manager")
        run = self.payrun()
        s = self.slip(run, "Farai Ncube")
        # 500 gross; NSSA 4.5% = 22.50; taxable 477.50; PAYE 477.50 x 25% - 35 = 84.375 -> 84.38; AIDS 3% = 2.53
        self.assertEqual((s["gross"], s["nssa_employee"], s["taxable"], s["paye"], s["aids_levy"], s["net"], s["nssa_employer"]),
                         ("500.00", "22.50", "477.50", "84.38", "2.53", "390.59", "22.50"))
        g = self.slip(run, "Grace Mutasa")
        # NSSA stops at the US$700 ceiling: 31.50. taxable 1968.50 x 30% - 85 = 505.55; AIDS 15.17
        self.assertEqual((g["nssa_employee"], g["paye"], g["aids_levy"], g["net"]), ("31.50", "505.55", "15.17", "1447.78"))
        t = run["totals"]["USD"]
        self.assertEqual((t["to_zimra"], t["to_nssa"], t["people"]), ("607.63", "108.00", 2))

    def test_daily_and_piece_work_and_crop_costs(self):
        farm = self.add_farm()
        field = self.add_field(farm)
        season = self.add_season()
        plan = self.api.post(self.base + "crop-plans/", {"season": season, "field": field, "crop": "Cotton",
                                                           "planted_ha": "10"}).json()["id"]
        w = self.worker("Tapiwa Dube", pay_basis="daily", rate="5", kind="casual")
        cotton = self.task()
        r = self.api.post(self.L + "work/", {"date": "2026-10-02", "kind": "day", "crop_plan": plan, "entries": [
            {"worker": w}, {"worker": w, "days": "0.5"}]})
        self.assertEqual(r.status_code, 201, r.content)
        r = self.api.post(self.L + "work/", {"worker": w, "date": "2026-10-03", "kind": "piece", "task": cotton,
                                              "quantity": "200", "crop_plan": plan})
        self.assertEqual(r.json()["results"][0]["amount"], "10.00")
        run = self.payrun(period_start="2026-10-01", period_end="2026-10-15", pay_date="2026-10-15", name="1st half Oct")
        s = self.slip(run, "Tapiwa Dube")
        # 1.5 days x $5 = 7.50 + piece 10.00 = 17.50; NSSA 0.79; PAYE 0 (below the shrunk tax-free band)
        self.assertEqual((s["day_pay"], s["piece_pay"], s["gross"], s["nssa_employee"], s["paye"], s["net"]),
                         ("7.50", "10.00", "17.50", "0.79", "0.00", "16.71"))
        costs = self.api.get(self.base + "stock/plan-costs/", {"season": season}).json()["plans"][plan]
        self.assertEqual(costs["labour_usd"], "17.50")

    def test_zig_share_and_zig_workers(self):
        ExchangeRate.objects.create(date=datetime.date(2026, 9, 1), currency="ZWG", units_per_usd=Decimal("26.85"))
        self.worker("Rudo Chari", rate="200", zig_share_pct="35")
        self.worker("Blessing Moyo", rate="6000", currency="ZWG")
        run = self.payrun()
        s = self.slip(run, "Rudo Chari")
        # gross 200, NSSA 9.00, taxable 191 -> 191 x 20% - 20 = 18.20, AIDS 0.55, net 172.25
        # 35% of net in ZiG: 60.29 USD x 26.85 = 1618.79 ZiG; the rest 111.96 in US$
        self.assertEqual((s["net"], s["net_in_usd"], s["net_in_zwg"]), ("172.25", "111.96", "1618.79"))
        z = self.slip(run, "Blessing Moyo")
        # ZiG worker: ceiling = US$700 x 26.85 = 18,795; NSSA 270; taxable 5730 x 20% - 560 = 586; AIDS 17.58
        self.assertEqual((z["currency"], z["nssa_employee"], z["paye"], z["aids_levy"]), ("ZWG", "270.00", "586.00", "17.58"))


class FlowTests(LabourBase):
    def test_advances_are_taken_off_and_big_ones_wait(self):
        w = self.worker("Farai Ncube", rate="300")
        self.assertEqual(self.api.post(self.L + "advances/", {"worker": w, "date": "2026-09-10", "amount": "50"}).status_code, 201)
        self.api.post(self.L + "advances/", {"worker": w, "date": "2026-09-12", "amount": "400"})
        run = self.payrun()
        s = self.slip(run, "Farai Ncube")
        self.assertEqual(s["advances"], "50.00")
        self.assertTrue(any("waits for the next" in x for x in s["warnings"]))

    def test_extras_approve_locks_and_reopen(self):
        w = self.worker("Tapiwa Dube", pay_basis="daily", rate="6", kind="seasonal")
        e = self.api.post(self.L + "work/", {"worker": w, "date": "2026-09-05"}).json()["results"][0]["id"]
        run = self.payrun()
        slip = self.slip(run, "Tapiwa Dube")
        r = self.api.patch(self.L + f"payslips/{slip['id']}/", {"extra_pay": "4", "extra_note": "Sunday work"})
        self.assertEqual(self.slip(r.json(), "Tapiwa Dube")["gross"], "10.00")
        r = self.api.post(self.L + f"pay-runs/{run['id']}/approve/", {})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "approved")
        self.assertEqual(self.slip(r.json(), "Tapiwa Dube")["gross"], "10.00")  # extra kept through approval
        self.assertEqual(self.api.delete(self.L + f"work/{e}/").status_code, 409)
        self.assertEqual(self.api.patch(self.L + f"payslips/{slip['id']}/", {"extra_pay": "9"}).status_code, 409)
        # A second pay run for the same month doesn't pay the same day again.
        self.assertEqual(self.payrun()["payslips"], [])
        self.assertEqual(self.api.post(self.L + f"pay-runs/{run['id']}/reopen/", {}).status_code, 400)
        r = self.api.post(self.L + f"pay-runs/{run['id']}/reopen/", {"reason": "Forgot overtime"})
        self.assertEqual(r.json()["status"], "draft")
        self.assertFalse(WorkEntry.objects.filter(payslip__isnull=False).exists())

    def test_minimum_wage_warning(self):
        self.worker("Low Pay", rate="70", grade="A1")
        run = self.payrun()
        self.assertTrue(any("Below the NEC minimum" in x for x in self.slip(run, "Low Pay")["warnings"]))
        self.assertEqual(self.api.get(self.L + "summary/").json()["below_minimum"][0]["minimum_usd"], "80.00")

    def test_offline_entry_saved_once_and_future_dates_refused(self):
        w = self.worker("Tapiwa Dube", pay_basis="daily", rate="6")
        eid = "6b0c3c2e-1a7e-4b7a-9e0f-1d2c3b4a5f60"
        for _ in range(2):
            r = self.api.post(self.L + "work/", {"id": eid, "worker": w, "date": "2026-10-01"})
            self.assertIn(r.status_code, (200, 201))
        self.assertEqual(WorkEntry.objects.count(), 1)
        r = self.api.post(self.L + "work/", {"worker": w, "date": "2099-01-01"})
        self.assertEqual(r.status_code, 400)

    def test_supervisor_records_work_but_sees_no_pay(self):
        w = self.worker("Farai Ncube", pay_basis="daily", rate="6")
        sup = make_user("0771000002", "Sipho Supervisor")
        self.api.post(self.base + "members/", {"login": "0771000002", "role": "supervisor"})
        s = ApiClient(sup)
        lst = s.get(self.L + "workers/").json()["results"]
        self.assertEqual(lst[0]["full_name"], "Farai Ncube")
        self.assertNotIn("rate", lst[0])
        self.assertNotIn("rate", s.get(self.L + f"workers/{w}/").json())
        r = s.post(self.L + "work/", {"worker": w, "date": "2026-10-01"})
        self.assertEqual(r.status_code, 201)
        self.assertNotIn("amount", r.json()["results"][0])
        self.assertEqual(s.get(self.L + "pay-runs/").status_code, 403)
        self.assertEqual(s.post(self.L + "workers/", {"full_name": "X", "rate": "1"}).status_code, 403)
        self.assertEqual(s.get(self.L + "advances/").status_code, 403)

    def test_other_businesses_cant_see_workers(self):
        self.worker("Farai Ncube")
        other = ApiClient(make_user("0771000009", "Stranger"))
        self.assertEqual(other.get(self.L + "workers/").status_code, 404)
        self.assertEqual(other.get(self.L + "pay-runs/").status_code, 404)

    def test_own_rules_beat_murimi_rules(self):
        r = self.api.post(self.L + "rules/", {"currency": "USD", "effective_from": "2026-09-01",
                                               "bands": [[200, 0, 0], [None, 10, 20]], "aids_levy_pct": "3",
                                               "nssa_employee_pct": "4.5", "nssa_employer_pct": "4.5",
                                               "nssa_ceiling": "700", "working_days": "26"})
        self.assertEqual(r.status_code, 201, r.content)
        self.worker("Farai Ncube", rate="500")
        s = self.slip(self.payrun(), "Farai Ncube")
        self.assertEqual(s["paye"], "27.75")  # 477.50 x 10% - 20
        bad = self.api.post(self.L + "rules/", {"currency": "USD", "effective_from": "2026-09-02",
                                                 "bands": [[200, 0, 0], [100, 10, 0]], "aids_levy_pct": "3",
                                                 "nssa_employee_pct": "4.5", "nssa_employer_pct": "4.5"})
        self.assertEqual(bad.status_code, 400)

    def test_csv_and_draft_overlap(self):
        self.worker("Farai Ncube")
        run = self.payrun()
        r = self.api.post(self.L + "pay-runs/", SEP)
        self.assertEqual(r.status_code, 400)
        csv = self.api.get(self.L + f"pay-runs/{run['id']}/csv/")
        self.assertIn(b"Farai Ncube", csv.content)
        self.assertEqual(self.api.delete(self.L + f"pay-runs/{run['id']}/").status_code, 200)
        self.assertFalse(PayRun.objects.exists())
