from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import LoginAttempt


@override_settings(
    LOGIN_MAX_FAILURES=3,
    LOGIN_IP_MAX_FAILURES=20,
    LOGIN_FAILURE_WINDOW_MINUTES=15,
    LOGIN_LOCKOUT_MINUTES=15,
    LOGIN_LOG_DISPLAY_LIMIT=100,
    LOGIN_BLOCKED_LOG_INTERVAL_SECONDS=60,
    LOGIN_TRUST_PROXY_HEADERS=True,
    LOGIN_PROXY_IP_HEADERS=("HTTP_CF_CONNECTING_IP", "HTTP_X_FORWARDED_FOR", "HTTP_X_REAL_IP"),
)
class AuthenticationSecurityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="admin",
            password="Old-password-123!",
            is_staff=True,
            is_superuser=True,
        )
        self.login_url = reverse("login")

    def _login(self, password, username="admin", **headers):
        defaults = {
            "REMOTE_ADDR": "192.0.2.10",
            "HTTP_USER_AGENT": "Test Browser/1.0",
        }
        defaults.update(headers)
        return self.client.post(
            self.login_url,
            {"username": username, "password": password},
            **defaults,
        )

    def test_failed_attempt_records_client_ip_and_user_agent(self):
        response = self._login(
            "wrong-password",
            HTTP_CF_CONNECTING_IP="203.0.113.45",
            HTTP_X_FORWARDED_FOR="198.51.100.9, 192.0.2.20",
        )
        self.assertEqual(response.status_code, 200)
        attempt = LoginAttempt.objects.get()
        self.assertEqual(attempt.result, LoginAttempt.Result.FAILED)
        self.assertEqual(attempt.username, "admin")
        self.assertEqual(attempt.username_normalized, "admin")
        self.assertEqual(str(attempt.ip_address), "203.0.113.45")
        self.assertEqual(attempt.user_agent, "Test Browser/1.0")

    def test_successful_login_is_logged(self):
        response = self._login("Old-password-123!", HTTP_X_FORWARDED_FOR="198.51.100.7")
        self.assertEqual(response.status_code, 302)
        attempt = LoginAttempt.objects.get()
        self.assertEqual(attempt.result, LoginAttempt.Result.SUCCESS)
        self.assertEqual(attempt.user, self.user)
        self.assertEqual(str(attempt.ip_address), "198.51.100.7")

    def test_username_is_locked_after_configured_failures(self):
        for _ in range(2):
            response = self._login("wrong-password", REMOTE_ADDR="192.0.2.10")
            self.assertEqual(response.status_code, 200)

        response = self._login("wrong-password", REMOTE_ADDR="192.0.2.10")
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

        response = self._login("Old-password-123!", REMOTE_ADDR="192.0.2.10")
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(LoginAttempt.objects.filter(result=LoginAttempt.Result.BLOCKED).count(), 1)

        # Repeated blocked requests remain visible without flooding the database.
        self.assertEqual(self._login("Old-password-123!", REMOTE_ADDR="192.0.2.10").status_code, 429)
        self.assertEqual(LoginAttempt.objects.filter(result=LoginAttempt.Result.BLOCKED).count(), 1)

        response = self._login("Old-password-123!", REMOTE_ADDR="192.0.2.99")
        self.assertEqual(response.status_code, 302)

    def test_successful_login_resets_username_failure_counter(self):
        self._login("wrong-password")
        self._login("wrong-password")
        self.assertEqual(self._login("Old-password-123!").status_code, 302)
        self.client.post(reverse("logout"))

        self.assertEqual(self._login("wrong-password").status_code, 200)
        self.assertEqual(self._login("wrong-password").status_code, 200)
        self.assertEqual(self._login("Old-password-123!").status_code, 302)

    def test_account_password_change_keeps_current_session(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("account"),
            {
                "old_password": "Old-password-123!",
                "new_password1": "New-password-456!",
                "new_password2": "New-password-456!",
            },
        )
        self.assertRedirects(response, reverse("account"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("New-password-456!"))
        self.assertEqual(str(self.client.session.get("_auth_user_id")), str(self.user.pk))

    def test_regular_user_sees_only_own_login_history(self):
        regular = get_user_model().objects.create_user("operator", password="Operator-pass-123!")
        LoginAttempt.objects.create(
            username="admin",
            username_normalized="admin",
            user=self.user,
            ip_address="192.0.2.1",
            result=LoginAttempt.Result.SUCCESS,
        )
        LoginAttempt.objects.create(
            username="operator",
            username_normalized="operator",
            user=regular,
            ip_address="192.0.2.2",
            result=LoginAttempt.Result.SUCCESS,
        )
        self.client.force_login(regular)
        response = self.client.get(reverse("account"))
        self.assertContains(response, "192.0.2.2")
        self.assertNotContains(response, "192.0.2.1")

    @override_settings(LOGIN_TRUST_PROXY_HEADERS=False)
    def test_proxy_headers_are_ignored_when_disabled(self):
        self._login(
            "wrong-password",
            REMOTE_ADDR="192.0.2.55",
            HTTP_CF_CONNECTING_IP="203.0.113.99",
        )
        attempt = LoginAttempt.objects.get()
        self.assertEqual(str(attempt.ip_address), "192.0.2.55")
