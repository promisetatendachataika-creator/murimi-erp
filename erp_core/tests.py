from django.test import TestCase

from murimi_common.testing import ApiClient, make_user

from .models import AuditEvent, CropPlan, Membership, Organisation


class Base(TestCase):
    def setUp(self):
        self.owner = make_user("0771000001", "Tendai Moyo")
        self.api = ApiClient(self.owner)
        r = self.api.post("/api/erp/orgs/", {"name": "Moyo Family Farms", "province": "Mashonaland East",
                                             "district": "Marondera"})
        self.assertEqual(r.status_code, 201, r.content)
        self.org = r.json()["id"]
        self.base = f"/api/erp/orgs/{self.org}/"

    def add_farm(self, name="Chakadenga", **kw):
        r = self.api.post(self.base + "farms/", {"name": name, "province": "Mashonaland East",
                                                 "district": "Marondera", **kw})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()["id"]

    def add_field(self, farm, name="Block A", size="20"):
        r = self.api.post(self.base + "fields/", {"farm": farm, "name": name, "size_ha": size})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()["id"]

    def add_season(self, name="2026/27 Summer", status="active"):
        r = self.api.post(self.base + "seasons/", {"name": name, "start_date": "2026-10-01",
                                                   "end_date": "2027-06-30", "status": status})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()["id"]


class OrganisationTests(Base):
    def test_creator_is_owner_and_sees_permissions(self):
        r = self.api.get("/api/erp/orgs/")
        org = r.json()["results"][0]
        self.assertEqual(org["my_role"], "owner")
        self.assertIn("manage_members", org["can"])
        self.assertTrue(AuditEvent.objects.filter(organisation_id=self.org, action="create").exists())

    def test_other_organisations_are_invisible(self):
        """The most important rule: one business can never see or touch another's records."""
        farm = self.add_farm()
        stranger = make_user("0779999999", "Someone Else")
        other = ApiClient(stranger)
        other.post("/api/erp/orgs/", {"name": "Other Estates"})
        for url in (self.base, self.base + "farms/", self.base + f"farms/{farm}/", self.base + "summary/",
                    self.base + "members/", self.base + "rates/"):
            self.assertEqual(other.get(url).status_code, 404, url)
        self.assertEqual(other.patch(self.base + f"farms/{farm}/", {"name": "Mine now"}).status_code, 404)
        self.assertEqual(other.post(self.base + "farms/", {"name": "Sneaky"}).status_code, 404)
        # Even using another org's farm id inside their own org fails.
        other_org = Organisation.objects.get(name="Other Estates").id
        r = other.post(f"/api/erp/orgs/{other_org}/fields/", {"farm": farm, "name": "X", "size_ha": "1"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("farm", r.json()["errors"])
        self.assertEqual(other.get("/api/erp/orgs/not-a-uuid/").status_code, 404)
        self.assertEqual(ApiClient().get(self.base).status_code, 401)

    def test_roles_limit_changes(self):
        sup = make_user("0771000002", "Farai Supervisor")
        r = self.api.post(self.base + "members/", {"login": "0771000002", "role": "supervisor"})
        self.assertEqual(r.status_code, 201, r.content)
        s = ApiClient(sup)
        self.assertEqual(s.get(self.base + "farms/").status_code, 200)  # can view
        r = s.post(self.base + "farms/", {"name": "X", "province": "Harare", "district": "Y"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(s.post(self.base + "members/", {"login": "0771000001", "role": "owner"}).status_code, 403)
        self.assertEqual(s.get(self.base + "audit/").status_code, 403)

    def test_members_unknown_number_duplicate_and_last_owner(self):
        r = self.api.post(self.base + "members/", {"login": "0770000000", "role": "viewer"})
        self.assertIn("sign up first", r.json()["errors"]["login"])
        make_user("0771000003", "Chipo Accountant")
        self.api.post(self.base + "members/", {"login": "0771000003", "role": "accountant"})
        r = self.api.post(self.base + "members/", {"login": "0771000003", "role": "viewer"})
        self.assertEqual(r.status_code, 400)
        me = Membership.objects.get(user=self.owner)
        self.assertEqual(self.api.patch(self.base + f"members/{me.id}/", {"role": "viewer"}).status_code, 409)
        self.assertEqual(self.api.delete(self.base + f"members/{me.id}/").status_code, 409)


class FarmTests(Base):
    def test_farm_field_crud_with_audit(self):
        farm = self.add_farm(size_ha="120.5")
        field = self.add_field(farm, size="20")
        self.add_field(farm, name="Block B", size="15.25")
        r = self.api.get(self.base + "farms/")
        f = r.json()["results"][0]
        self.assertEqual((f["fields"], f["field_ha"]), (2, "35.25"))
        r = self.api.patch(self.base + f"fields/{field}/", {"size_ha": "22"})
        self.assertEqual(r.json()["size_ha"], "22.00")
        ev = AuditEvent.objects.filter(action="update").first()
        self.assertEqual(ev.changes["size_ha"], ["20.00", "22"])
        self.api.delete(self.base + f"fields/{field}/")
        self.assertEqual(len(self.api.get(self.base + "fields/").json()["results"]), 1)
        self.assertEqual(len(self.api.get(self.base + "fields/", {"include_archived": "1"}).json()["results"]), 2)

    def test_bad_values_are_explained(self):
        farm = self.add_farm()
        r = self.api.post(self.base + "fields/", {"farm": farm, "name": "", "size_ha": "-3"})
        self.assertEqual(set(r.json()["errors"]), {"name", "size_ha"})
        r = self.api.post(self.base + "fields/", {"farm": farm, "name": "C", "size_ha": "1.234"})
        self.assertIn("decimal places", r.json()["errors"]["size_ha"])
        r = self.api.post(self.base + "farms/", {"name": "Chakadenga", "province": "Mashonaland East", "district": "M"})
        self.assertEqual(r.status_code, 400)  # duplicate name


class CropPlanTests(Base):
    def test_plans_cannot_overfill_a_field(self):
        farm = self.add_farm()
        field = self.add_field(farm, size="20")
        season = self.add_season()
        r = self.api.post(self.base + "crop-plans/", {"season": season, "field": field, "crop": "Maize",
                                                      "planted_ha": "12", "target_yield_t_per_ha": "8",
                                                      "budget_amount": "18000", "budget_currency": "USD"})
        self.assertEqual(r.status_code, 201, r.content)
        p = r.json()
        self.assertEqual((p["target_tonnes"], p["budget_per_ha"]), ("96.00", "1500.00"))
        r = self.api.post(self.base + "crop-plans/", {"season": season, "field": field, "crop": "Soya beans",
                                                      "planted_ha": "9"})
        self.assertIn("Only 8.00 ha", r.json()["errors"]["planted_ha"])
        r = self.api.post(self.base + "crop-plans/", {"season": season, "field": field, "crop": "Soya beans",
                                                      "planted_ha": "8"})
        self.assertEqual(r.status_code, 201)
        # The field can't shrink below what's planned on it.
        r = self.api.patch(self.base + f"fields/{field}/", {"size_ha": "15"})
        self.assertIn("already planned", r.json()["errors"]["size_ha"])
        s = self.api.get(self.base + "summary/").json()
        self.assertEqual(s["planted_ha"], "20.00")
        self.assertEqual(s["by_crop"][0]["crop"], "Maize")

    def test_closed_season_and_dates(self):
        farm = self.add_farm()
        field = self.add_field(farm)
        season = self.add_season(status="closed")
        r = self.api.post(self.base + "crop-plans/", {"season": season, "field": field, "crop": "Maize",
                                                      "planted_ha": "2"})
        self.assertIn("closed", r.json()["errors"]["season"])
        r = self.api.post(self.base + "seasons/", {"name": "Bad", "start_date": "2027-01-01", "end_date": "2026-01-01"})
        self.assertIn("end_date", r.json()["errors"])

    def test_offline_made_id_is_kept(self):
        farm_id = "3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90"
        r = self.api.post(self.base + "farms/", {"id": farm_id, "name": "Offline farm", "province": "Harare",
                                                 "district": "Harare"})
        self.assertEqual(r.json()["id"], farm_id)

    def test_plan_on_used_field_cannot_be_deleted_by_field_delete(self):
        farm = self.add_farm()
        field = self.add_field(farm)
        season = self.add_season()
        self.api.post(self.base + "crop-plans/", {"season": season, "field": field, "crop": "Wheat", "planted_ha": "5"})
        r = self.api.delete(self.base + f"seasons/{season}/")
        self.assertEqual(r.status_code, 200)  # deleting a season removes its plans
        self.assertEqual(CropPlan.objects.count(), 0)
