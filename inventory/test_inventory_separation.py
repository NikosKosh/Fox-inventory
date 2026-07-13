from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from inventory.forms import EquipmentForm
from inventory.models import Cabinet, Category, Employee, Equipment, Location, Organization
from inventory.services import import_equipment


class InventorySeparationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "admin@example.test", "password")
        self.client.force_login(self.user)
        self.owner = Organization.objects.create(name="ООО Тест", prefix="TST")
        self.location = Location.objects.create(organization=self.owner, label="Техническая площадка", address="Техническая площадка")
        self.cabinet = Cabinet.objects.create(location=self.location, name="Шкаф 1")
        self.cat_laptop = Category.objects.get(code="N")
        self.cat_switch = Category.objects.get(code="SW")
        self.employee = Employee.objects.create(full_name="Иванов Иван Иванович", organization=self.owner)

    def make_equipment(self, *, category, group, status=Equipment.UsageStatus.STOCK, employee=None, location=None, cabinet=None, name=None):
        return Equipment.objects.create(
            category=category,
            accounting_group=group,
            name=name or category.name,
            owner=self.owner,
            usage_status=status,
            responsible_employee=employee,
            location=location,
            cabinet=cabinet,
        )

    def test_equipment_form_has_explicit_accounting_group(self):
        self.assertIn("accounting_group", EquipmentForm().fields)

    def test_warehouse_contains_only_free_stock_and_reserve(self):
        free_employee = self.make_equipment(category=self.cat_laptop, group=Equipment.AccountingGroup.EMPLOYEE)
        free_technical = self.make_equipment(category=self.cat_switch, group=Equipment.AccountingGroup.TECHNICAL, status=Equipment.UsageStatus.RESERVE)
        self.make_equipment(category=self.cat_laptop, group=Equipment.AccountingGroup.EMPLOYEE, status=Equipment.UsageStatus.EMPLOYEE, employee=self.employee)
        self.make_equipment(category=self.cat_switch, group=Equipment.AccountingGroup.TECHNICAL, status=Equipment.UsageStatus.OBJECT, location=self.location)

        response = self.client.get(reverse("warehouse"))
        self.assertContains(response, free_employee.internal_code)
        self.assertContains(response, free_technical.internal_code)
        self.assertEqual(response.context["objects"].count(), 2)
        self.assertEqual(response.context["employee_count"], 1)
        self.assertEqual(response.context["technical_count"], 1)

    def test_warehouse_group_filter(self):
        self.make_equipment(category=self.cat_laptop, group=Equipment.AccountingGroup.EMPLOYEE)
        technical = self.make_equipment(category=self.cat_switch, group=Equipment.AccountingGroup.TECHNICAL)
        response = self.client.get(reverse("warehouse"), {"group": "technical"})
        self.assertEqual(list(response.context["objects"]), [technical])

    def test_location_detail_shows_direct_and_cabinet_equipment_by_group(self):
        technical = self.make_equipment(
            category=self.cat_switch, group=Equipment.AccountingGroup.TECHNICAL,
            status=Equipment.UsageStatus.OBJECT, location=self.location,
        )
        employee_item = self.make_equipment(
            category=self.cat_laptop, group=Equipment.AccountingGroup.EMPLOYEE,
            status=Equipment.UsageStatus.OBJECT, cabinet=self.cabinet,
        )
        response = self.client.get(self.location.get_absolute_url())
        self.assertContains(response, technical.internal_code)
        self.assertContains(response, employee_item.internal_code)
        self.assertEqual(response.context["technical_equipment"].count(), 1)
        self.assertEqual(response.context["employee_equipment"].count(), 1)

    def test_location_list_links_to_object_card(self):
        response = self.client.get(reverse("location_list"))
        self.assertContains(response, self.location.get_absolute_url())
        self.assertContains(response, "Техническая площадка")

    def test_import_infers_technical_and_employee_groups(self):
        wb = Workbook()
        ws = wb.active
        ws.append([
            "organization", "organization_prefix", "category", "category_code", "name", "model", "status"
        ])
        ws.append(["ООО Импорт", "IMP", "Коммутатор", "SW", "Коммутатор", "SNR", "stock"])
        ws.append(["ООО Импорт", "IMP", "Ноутбук", "N", "Ноутбук", "Honor", "stock"])
        stream = BytesIO()
        wb.save(stream)
        upload = SimpleUploadedFile("import.xlsx", stream.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        created, updated, errors = import_equipment(upload, self.user)
        self.assertEqual(errors, [])
        self.assertEqual(created, 2)
        self.assertEqual(Equipment.objects.get(model="SNR").accounting_group, Equipment.AccountingGroup.TECHNICAL)
        self.assertEqual(Equipment.objects.get(model="Honor").accounting_group, Equipment.AccountingGroup.EMPLOYEE)
