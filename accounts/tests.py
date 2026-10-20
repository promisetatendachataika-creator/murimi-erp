from django.test import TestCase

from murimi_common.testing import ApiClient, make_user

from .models import User


class AccountTests(TestCase):
    def test_register_login_by_number_or_username(self):
        api = ApiClient()
        r = api.post("/api/auth/register/", {"full_name": "Tendai Moyo", "phone": "077 123 4567",
                                             "username": "Tendai_Moyo", "password": "maize-season-2026"})
        self.assertEqual(r.status_code, 201, r.content)
        u = User.objects.get()
        self.assertEqual((u.phone, u.username), ("+263771234567", "tendai_moyo"))
        api.post("/api/auth/logout/")
        self.assertEqual(api.get("/api/auth/me/").status_code, 401)
        for who in ("0771234567", "tendai_moyo", "+263 77 123 4567"):
            r = api.post("/api/auth/login/", {"login": who, "password": "maize-season-2026"})
            self.assertEqual(r.status_code, 200, who)

    def test_weak_password_duplicate_and_csrf(self):
        make_user("0771111111", "Rudo Chari")
        api = ApiClient()
        r = api.post("/api/auth/register/", {"full_name": "X Y", "phone": "0771111111", "password": "123"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("phone", r.json()["errors"])
        self.assertIn("password", r.json()["errors"])
        self.assertEqual(api.c.post("/api/auth/login/", {}).status_code, 403)  # no CSRF token

    def test_login_attempts_are_limited(self):
        make_user("0772222222", "Farai Dube")
        api = ApiClient()
        for _ in range(5):
            api.post("/api/auth/login/", {"login": "0772222222", "password": "wrong-password"})
        r = api.post("/api/auth/login/", {"login": "0772222222", "password": "harvest-time-2026"})
        self.assertEqual(r.status_code, 429)


class PasswordResetTests(TestCase):
    def setUp(self):
        self.u = make_user("0772000001", "Rudo Chari")

    def test_live_site_without_whatsapp_never_shows_codes(self):
        from django.test import override_settings
        with override_settings(DEBUG=False, MURIMI_CODE_SENDER="console"):
            r = self.client.post("/api/auth/password/forgot/", {"login": "0772000001"}, content_type="application/json")
        data = r.json()
        self.assertTrue(data["whatsapp_off"])
        self.assertNotIn("test_code", data)
        self.assertIn("owner", data["message"])

    def test_code_resets_password_and_logs_in(self):
        from django.test import override_settings
        with override_settings(DEBUG=True, MURIMI_CODE_SENDER="console"):
            code = self.client.post("/api/auth/password/forgot/", {"login": "0772000001"},
                                    content_type="application/json").json()["test_code"]
        bad = self.client.post("/api/auth/password/reset/", {"login": "0772000001", "code": "000000",
                               "password": "green-mealies-2027"}, content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        r = self.client.post("/api/auth/password/reset/", {"login": "0772000001", "code": code,
                             "password": "green-mealies-2027"}, content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.u.refresh_from_db()
        self.assertTrue(self.u.check_password("green-mealies-2027"))
        again = self.client.post("/api/auth/password/reset/", {"login": "0772000001", "code": code,
                                 "password": "another-pass-2027"}, content_type="application/json")
        self.assertEqual(again.status_code, 400)  # a code works once

    def test_unknown_account_gets_same_answer(self):
        from django.test import override_settings
        with override_settings(DEBUG=True, MURIMI_CODE_SENDER="console"):
            r = self.client.post("/api/auth/password/forgot/", {"login": "0779999999"}, content_type="application/json")
        self.assertTrue(r.json()["sent"])
        self.assertNotIn("test_code", r.json())
