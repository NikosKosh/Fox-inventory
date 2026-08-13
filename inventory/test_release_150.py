from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import EquipmentForm
from .models import Category, Employee, Equipment, EquipmentLoan, Location, Organization, Room


@override_settings(FIELD_ENCRYPTION_KEY="")
class Release150SafetyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("operator150", password="test-password")
        self.staff = User.objects.create_user("staff150", password="test-password", is_staff=True)
        self.owner = Organization.objects.create(name="ООО Владелец 150", prefix="R150")
        self.borrower = Organization.objects.create(name="ООО Получатель 150", prefix="B150")
        self.category = Category.objects.create(name="Ноутбук 150", code="N150")
        self.location = Location.objects.create(
            organization=self.owner,
            address="Тестовая, 150",
            label="Офис 150",
        )
        self.room = Room.objects.create(location=self.location, name="Кабинет 150")
        self.employee = Employee.objects.create(
            full_name="Иванов Тест Тестович",
            organization=self.owner,
            workplace_location=self.location,
            room=self.room,
        )

    def equipment(self, **kwargs):
        data = {
            "category": self.category,
            "name": "Ноутбук тестовый",
            "owner": self.owner,
            "usage_status": Equipment.UsageStatus.STOCK,
            "condition": Equipment.Condition.USED,
        }
        data.update(kwargs)
        return Equipment.objects.create(**data)

    def test_network_password_is_staff_only(self):
        item = self.equipment()
        item.set_network_password("secret-150")
        item.save()

        self.client.force_login(self.user)
        response = self.client.get(reverse("reveal_network_password", args=[item.pk]))
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.staff)
        response = self.client.get(reverse("reveal_network_password", args=[item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["password"], "secret-150")

    def test_employee_cannot_be_archived_with_assigned_equipment(self):
        item = self.equipment(
            responsible_employee=self.employee,
            usage_status=Equipment.UsageStatus.EMPLOYEE,
            location=self.location,
            room=self.room,
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse("employee_archive", args=[self.employee.pk]))
        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertFalse(self.employee.archived)
        self.assertEqual(item.responsible_employee_id, self.employee.pk)

    def test_assigned_equipment_cannot_be_archived(self):
        item = self.equipment(
            responsible_employee=self.employee,
            usage_status=Equipment.UsageStatus.EMPLOYEE,
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse("equipment_archive", args=[item.pk]))
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertFalse(item.archived)

    def test_temporary_loan_restores_previous_assignment_and_location(self):
        item = self.equipment(
            responsible_employee=self.employee,
            usage_status=Equipment.UsageStatus.EMPLOYEE,
            location=self.location,
            room=self.room,
            freeform_location="Стол 15",
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("equipment_loan", args=[item.pk]),
            {
                "borrower": self.borrower.pk,
                "responsible_employee": "",
                "started_at": "2026-08-12",
                "expected_return_at": "",
                "undocumented": "on",
                "notes": "Тестовая временная передача",
            },
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.usage_status, Equipment.UsageStatus.LOANED)
        self.assertIsNone(item.responsible_employee)
        self.assertIsNone(item.location)
        self.assertIsNone(item.room)

        loan = EquipmentLoan.objects.get(equipment=item, status=EquipmentLoan.Status.ACTIVE)
        self.assertEqual(loan.previous_state["responsible_employee_id"], self.employee.pk)
        self.assertEqual(loan.previous_state["location_id"], self.location.pk)
        self.assertEqual(loan.previous_state["room_id"], self.room.pk)

        response = self.client.post(reverse("loan_return", args=[loan.pk]))
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        loan.refresh_from_db()
        self.assertEqual(loan.status, EquipmentLoan.Status.RETURNED)
        self.assertEqual(item.usage_status, Equipment.UsageStatus.EMPLOYEE)
        self.assertEqual(item.responsible_employee_id, self.employee.pk)
        self.assertEqual(item.location_id, self.location.pk)
        self.assertEqual(item.room_id, self.room.pk)
        self.assertEqual(item.freeform_location, "Стол 15")

    def test_broken_equipment_requires_repair_or_disposal_status(self):
        form = EquipmentForm(
            data={
                "category": self.category.pk,
                "accounting_group": Equipment.AccountingGroup.EMPLOYEE,
                "internal_code": "",
                "name": "Сломанный ноутбук",
                "manufacturer": "",
                "model": "",
                "serial_number": "",
                "mac_address": "",
                "hostname": "",
                "owner": self.owner.pk,
                "responsible_employee": "",
                "location": "",
                "room": "",
                "cabinet": "",
                "freeform_location": "",
                "quantity": "1",
                "usage_status": Equipment.UsageStatus.STOCK,
                "condition": Equipment.Condition.BROKEN,
                "notes": "",
                "network_address": "",
                "network_username": "",
                "network_password": "",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("usage_status", form.errors)
