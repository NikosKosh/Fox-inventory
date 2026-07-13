from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Employee, Location, Organization


class VisualRefreshTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("visual-admin", password="test-pass-123")
        self.client.force_login(self.user)
        self.org = Organization.objects.create(name="ООО Работодатель", prefix="PG")
        self.location = Location.objects.create(
            organization=self.org,
            label="Главный офис",
            address="г. Тестовый, ул. Примерная, д. 1",
        )
        Employee.objects.create(
            full_name="Иванов Иван Иванович",
            position="Главный инженер проекта",
            department="Управление по реализации проекта",
            organization=self.org,
            workplace_location=self.location,
            phone="8-900-000-00-00",
        )

    def test_header_uses_new_brand_and_controls(self):
        response = self.client.get(reverse("employee_list"))
        self.assertContains(response, 'class="brand-copy"')
        self.assertContains(response, "<strong>FOX</strong>", html=True)
        self.assertContains(response, 'class="global-search app-search"')
        self.assertNotContains(response, "production</small>")

    def test_employee_list_has_readable_data_table(self):
        response = self.client.get(reverse("employee_list"))
        self.assertContains(response, 'class="data-table sortable-table employee-table"')
        self.assertContains(response, 'class="employee-avatar"')
        self.assertContains(response, 'class="organization-tag"')
        self.assertContains(response, 'class="location-link"')
        self.assertContains(response, "Иванов Иван Иванович")

    def test_sort_indicator_uses_css_chevron_state(self):
        response = self.client.get(reverse("employee_list"), {"sort": "organization", "dir": "desc"})
        self.assertContains(response, "state-desc")
        self.assertContains(response, "sort=organization&amp;dir=asc")
