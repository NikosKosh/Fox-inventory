from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Category, Employee, Equipment, Location, Organization


class ProductionWorkspaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("admin", password="test-pass-123")
        self.client.force_login(self.user)
        self.org = Organization.objects.create(name="ООО Тест", prefix="TST")
        self.location = Location.objects.create(organization=self.org, label="Офис", address="г. Тестовый, ул. Тестовая, 1")
        self.employee = Employee.objects.create(
            full_name="Иванов Иван Иванович", organization=self.org,
            workplace_location=self.location, workplace=self.location.address,
        )
        self.category, _ = Category.objects.get_or_create(code="N", defaults={"name": "Ноутбук"})
        self.equipment = Equipment.objects.create(
            category=self.category, name="Тестовый ноутбук", owner=self.org,
            responsible_employee=self.employee, serial_number="SERIAL-001",
        )

    def test_object_page_contains_employee_and_equipment(self):
        response = self.client.get(reverse("location_detail", args=[self.location.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Иванов Иван Иванович")
        self.assertContains(response, "Тестовый ноутбук")

    def test_object_employee_tab_and_prefilled_employee_form(self):
        response = self.client.get(reverse("location_detail", args=[self.location.pk]), {"tab": "employees"})
        self.assertContains(response, "Не хватает")
        form_response = self.client.get(reverse("employee_add"), {"location": self.location.pk})
        self.assertEqual(str(form_response.context["form"].initial["workplace_location"]), str(self.location.pk))

    def test_equipment_preview(self):
        response = self.client.get(reverse("equipment_preview", args=[self.equipment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SERIAL-001")
        self.assertContains(response, "Открыть полную карточку")

    def test_global_search(self):
        response = self.client.get(reverse("global_search"), {"q": "SERIAL-001"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Тестовый ноутбук")

    def test_control_center(self):
        response = self.client.get(reverse("control_center"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Контроль данных")

    def test_lists_are_paginated(self):
        response = self.client.get(reverse("equipment_list"))
        self.assertIn("page_obj", response.context)
        response = self.client.get(reverse("employee_list"))
        self.assertIn("page_obj", response.context)
