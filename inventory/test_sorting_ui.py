from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Category, Employee, Equipment, Location, Organization


class SortingAndBrandingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("sort-admin", password="test-pass-123")
        self.client.force_login(self.user)
        self.org_a = Organization.objects.create(name="Альфа", prefix="ALF")
        self.org_b = Organization.objects.create(name="Бета", prefix="BET")
        self.loc_a = Location.objects.create(organization=self.org_a, label="Офис А", address="Адрес А")
        self.loc_b = Location.objects.create(organization=self.org_b, label="Офис Б", address="Адрес Б")
        self.emp_z = Employee.objects.create(full_name="Яковлев Яков", organization=self.org_a, workplace_location=self.loc_a, phone="200")
        self.emp_a = Employee.objects.create(full_name="Андреев Андрей", organization=self.org_b, workplace_location=self.loc_b, phone="100")
        self.category = Category.objects.create(name="Тестовое оборудование", code="ZZ")
        self.eq_z = Equipment.objects.create(category=self.category, owner=self.org_a, name="Ящик", internal_code="ZZ-002", responsible_employee=self.emp_z)
        self.eq_a = Equipment.objects.create(category=self.category, owner=self.org_b, name="Адаптер", internal_code="ZZ-001", responsible_employee=self.emp_a)

    def test_favicon_and_manifest_are_in_base(self):
        response = self.client.get(reverse("employee_list"))
        self.assertContains(response, 'rel="icon"')
        self.assertContains(response, 'rel="manifest"')
        self.assertContains(response, "Сотрудники — FOX Inventory")

    def test_employee_sort_toggles_and_preserves_filters(self):
        response = self.client.get(reverse("employee_list"), {"sort": "full_name", "dir": "desc", "location": self.loc_a.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "sort=full_name&amp;dir=asc")
        self.assertContains(response, f"location={self.loc_a.pk}")
        self.assertContains(response, "state-desc")

        response = self.client.get(reverse("employee_list"), {"sort": "phone", "dir": "asc"})
        names = [item.full_name for item in response.context["objects"]]
        self.assertEqual(names, ["Андреев Андрей", "Яковлев Яков"])

    def test_equipment_sort_by_name_desc(self):
        response = self.client.get(reverse("equipment_list"), {"sort": "name", "dir": "desc"})
        names = [item.name for item in response.context["objects"]]
        self.assertEqual(names, ["Ящик", "Адаптер"])

    def test_location_cards_sort_by_employee_count(self):
        Employee.objects.create(full_name="Второй сотрудник", organization=self.org_b, workplace_location=self.loc_b)
        response = self.client.get(reverse("location_list"), {"sort": "employees", "dir": "desc"})
        locations = list(response.context["objects"])
        self.assertEqual(locations[0].pk, self.loc_b.pk)
        self.assertContains(response, "sort-chip active")

    def test_invalid_sort_falls_back_safely(self):
        response = self.client.get(reverse("warehouse"), {"sort": "__bad_field", "dir": "sideways"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sort"], "code")
        self.assertEqual(response.context["direction"], "asc")
