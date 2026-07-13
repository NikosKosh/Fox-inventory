from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import EmployeeForm, EquipmentForm
from .models import Category, Employee, Equipment, EquipmentMovement, Location, Organization, Room


class RoomFeatureTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="tester", password="test-pass-123")
        self.client.force_login(self.user)
        self.org = Organization.objects.create(name="ООО Тест", prefix="TST")
        self.location = Location.objects.create(organization=self.org, label="Главный офис", address="ул. Тестовая, 1")
        self.room = Room.objects.create(location=self.location, name="Переговорная", room_type=Room.RoomType.MEETING)
        self.category = Category.objects.create(name="Веб-камера", code="WC")

    def test_room_detail_and_location_rooms_tab(self):
        response = self.client.get(reverse("room_detail", args=[self.room.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Переговорная")
        response = self.client.get(reverse("location_detail", args=[self.location.pk]), {"tab": "rooms"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Помещения объекта")
        self.assertContains(response, "Переговорная")

    def test_equipment_form_infers_location_from_room(self):
        form = EquipmentForm(data={
            "category": self.category.pk,
            "accounting_group": Equipment.AccountingGroup.TECHNICAL,
            "internal_code": "TST-WC-001",
            "name": "Веб-камера",
            "manufacturer": "Logitech",
            "model": "Brio",
            "serial_number": "CAM-001",
            "hostname": "",
            "owner": self.org.pk,
            "responsible_employee": "",
            "location": "",
            "room": self.room.pk,
            "cabinet": "",
            "freeform_location": "",
            "quantity": 1,
            "usage_status": Equipment.UsageStatus.OBJECT,
            "condition": Equipment.Condition.NEW,
            "notes": "",
            "network_address": "",
            "network_username": "",
            "network_password": "",
            "archived": False,
        })
        self.assertTrue(form.is_valid(), form.errors)
        equipment = form.save()
        self.assertEqual(equipment.room, self.room)
        self.assertEqual(equipment.location, self.location)

    def test_employee_form_infers_object_from_room(self):
        form = EmployeeForm(data={
            "full_name": "Иванов Иван Иванович",
            "position": "Инженер",
            "department": "ИТ",
            "workplace_location": "",
            "room": self.room.pk,
            "phone": "",
            "organization": self.org.pk,
            "archived": False,
            "notes": "",
        })
        self.assertTrue(form.is_valid(), form.errors)
        employee = form.save()
        self.assertEqual(employee.room, self.room)
        self.assertEqual(employee.workplace_location, self.location)

    def test_bulk_assign_equipment_to_room(self):
        equipment = Equipment.objects.create(
            category=self.category,
            accounting_group=Equipment.AccountingGroup.TECHNICAL,
            name="Камера переговорной",
            owner=self.org,
            usage_status=Equipment.UsageStatus.STOCK,
        )
        response = self.client.post(reverse("room_assign_equipment", args=[self.room.pk]), {
            "equipment": [equipment.pk],
            "notes": "Фактическое размещение.",
        })
        self.assertRedirects(response, reverse("room_detail", args=[self.room.pk]))
        equipment.refresh_from_db()
        self.assertEqual(equipment.room, self.room)
        self.assertEqual(equipment.location, self.location)
        self.assertEqual(equipment.usage_status, Equipment.UsageStatus.OBJECT)
        movement = EquipmentMovement.objects.get(equipment=equipment, movement_type=EquipmentMovement.MovementType.INSTALLED)
        self.assertIn("Переговорная", movement.notes)

    def test_room_preview_contains_room_link(self):
        equipment = Equipment.objects.create(
            category=self.category,
            accounting_group=Equipment.AccountingGroup.TECHNICAL,
            name="Микрофон",
            owner=self.org,
            location=self.location,
            room=self.room,
            usage_status=Equipment.UsageStatus.OBJECT,
        )
        response = self.client.get(reverse("equipment_preview", args=[equipment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Помещение")
        self.assertContains(response, "Переговорная")
