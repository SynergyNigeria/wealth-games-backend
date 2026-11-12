from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from lotteries.models import Giveaway


class CorsConfigurationTests(TestCase):
    @override_settings(CORS_ALLOWED_ORIGINS=["https://frontend.example"])
    def test_allowed_frontend_can_preflight_api_requests(self):
        response = Client().options(
            "/api/auth/login/",
            HTTP_ORIGIN="https://frontend.example",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Access-Control-Allow-Origin"],
            "https://frontend.example",
        )
        self.assertIn("POST", response["Access-Control-Allow-Methods"])


class AdminCsrfRecoveryTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="csrf_admin",
            email="csrf-admin@example.test",
            password="Secure-Test-Password-12345",
        )
        self.giveaway = Giveaway.objects.create(
            name="CSRF recovery test",
            amount="100.00",
            number_of_winners=1,
        )
        self.client = Client(enforce_csrf_checks=True, HTTP_HOST="127.0.0.1")

    def test_expired_admin_form_refreshes_without_applying_delete(self):
        login_page = self.client.get(reverse("admin:login"))
        stale_token = self.client.cookies["csrftoken"].value
        self.assertIn("no-store", login_page["Cache-Control"])

        login = self.client.post(
            reverse("admin:login"),
            {
                "username": self.admin.username,
                "password": "Secure-Test-Password-12345",
                "csrfmiddlewaretoken": stale_token,
            },
        )
        self.assertEqual(login.status_code, 302)
        self.assertNotEqual(self.client.cookies["csrftoken"].value, stale_token)

        delete_url = reverse(
            "admin:lotteries_giveaway_delete",
            args=[self.giveaway.pk],
        )
        stale_delete = self.client.post(
            delete_url,
            {"csrfmiddlewaretoken": stale_token, "post": "yes"},
        )

        self.assertEqual(stale_delete.status_code, 302)
        self.assertEqual(stale_delete["Location"], delete_url)
        self.assertTrue(Giveaway.objects.filter(pk=self.giveaway.pk).exists())

        refreshed_page = self.client.get(delete_url)
        fresh_token = self.client.cookies["csrftoken"].value
        self.assertEqual(refreshed_page.status_code, 200)
        self.assertIn("no-store", refreshed_page["Cache-Control"])

        delete = self.client.post(
            delete_url,
            {"csrfmiddlewaretoken": fresh_token, "post": "yes"},
        )

        self.assertEqual(delete.status_code, 302)
        self.assertFalse(Giveaway.objects.filter(pk=self.giveaway.pk).exists())

    def test_admin_user_delete_works_with_fresh_csrf_token(self):
        user = get_user_model().objects.create_user(
            username="delete_me",
            password="Secure-Test-Password-12345",
        )
        self.client.force_login(self.admin)
        delete_url = reverse("admin:auth_user_delete", args=[user.pk])
        confirmation = self.client.get(delete_url)
        csrf_token = self.client.cookies["csrftoken"].value

        self.assertEqual(confirmation.status_code, 200)
        self.assertIn("no-store", confirmation["Cache-Control"])

        response = self.client.post(
            delete_url,
            {"csrfmiddlewaretoken": csrf_token, "post": "yes"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(get_user_model().objects.filter(pk=user.pk).exists())
