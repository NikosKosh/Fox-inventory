from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from .models import Act, Category, Employee, Equipment, EquipmentLoan, EquipmentMovement, Location, Organization


class InventoryFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("admin", password="test-password", is_staff=True)
        self.client.login(username="admin", password="test-password")
        self.owner = Organization.objects.create(name="ООО Владелец оборудования", prefix="FOX")
        self.borrower = Organization.objects.create(name="ООО Клиент", prefix="CL")
        self.category = Category.objects.get(code="N")
        self.employee = Employee.objects.create(full_name="Иванов Иван", organization=self.borrower)

    def test_equipment_code_generation(self):
        first = Equipment.objects.create(category=self.category, name="Ноутбук 1", owner=self.owner)
        second = Equipment.objects.create(category=self.category, name="Ноутбук 2", owner=self.owner)
        self.assertEqual(first.internal_code, "FOX-N-001")
        self.assertEqual(second.internal_code, "FOX-N-002")

    def test_assignment_creates_history(self):
        item = Equipment.objects.create(category=self.category, name="Ноутбук", owner=self.owner)
        response = self.client.post(reverse("equipment_assign", args=[item.pk]), {
            "employee": self.employee.pk,
            "status": Equipment.UsageStatus.EMPLOYEE,
            "location": "",
            "cabinet": "",
            "freeform_location": "",
            "notes": "Выдано для работы",
        })
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.responsible_employee, self.employee)
        self.assertTrue(item.movements.filter(movement_type=EquipmentMovement.MovementType.ASSIGNED).exists())

    def test_act_assigns_multiple_equipment(self):
        one = Equipment.objects.create(category=self.category, name="Ноутбук", owner=self.owner)
        two = Equipment.objects.create(category=self.category, name="Сумка", owner=self.owner)
        doc = SimpleUploadedFile("act.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        response = self.client.post(reverse("act_add"), {
            "number": "1",
            "act_type": Act.ActType.ISSUE,
            "act_date": "2026-07-09",
            "employee": self.employee.pk,
            "from_organization": self.owner.pk,
            "to_organization": "",
            "equipment": [one.pk, two.pk],
            "notes": "",
            "public_enabled": "",
            "apply_to_current_state": "on",
            "document": doc,
        })
        self.assertEqual(response.status_code, 302)
        one.refresh_from_db(); two.refresh_from_db()
        self.assertEqual(one.responsible_employee, self.employee)
        self.assertEqual(two.responsible_employee, self.employee)

    def test_loan_and_return(self):
        item = Equipment.objects.create(category=self.category, name="Коммутатор", owner=self.owner)
        response = self.client.post(reverse("equipment_loan", args=[item.pk]), {
            "borrower": self.borrower.pk,
            "responsible_employee": self.employee.pk,
            "started_at": "2026-07-09",
            "expected_return_at": "",
            "undocumented": "on",
            "notes": "Без документов",
        })
        self.assertEqual(response.status_code, 302)
        loan = EquipmentLoan.objects.get(equipment=item)
        item.refresh_from_db()
        self.assertEqual(item.usage_status, Equipment.UsageStatus.LOANED)
        response = self.client.post(reverse("loan_return", args=[loan.pk]))
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db(); loan.refresh_from_db()
        self.assertEqual(item.usage_status, Equipment.UsageStatus.STOCK)
        self.assertEqual(loan.status, EquipmentLoan.Status.RETURNED)


    def test_act_file_requires_login_unless_public_enabled(self):
        item = Equipment.objects.create(category=self.category, name="Ноутбук", owner=self.owner)
        doc = SimpleUploadedFile("secure.pdf", b"%PDF-1.4 secure", content_type="application/pdf")
        act = Act.objects.create(number="SEC", employee=self.employee, document=doc, public_enabled=False)
        act.equipment.add(item)
        self.client.logout()
        response = self.client.get(act.document.url)
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse("public_act_file", args=[act.public_token]))
        self.assertEqual(response.status_code, 404)
        act.public_enabled = True
        act.save(update_fields=["public_enabled", "updated_at"])
        response = self.client.get(reverse("public_act_file", args=[act.public_token]))
        self.assertEqual(response.status_code, 200)

    def test_dashboard_and_lists(self):
        for name in ["dashboard", "equipment_list", "employee_list", "organization_list", "act_list"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)


class EmployeeCardOperationsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("operator", password="test-password")
        self.client.login(username="operator", password="test-password")
        self.owner = Organization.objects.create(name="ООО Владелец оборудования", prefix="SZ")
        self.employee_org = Organization.objects.create(name="ООО Работодатель", prefix="PG")
        self.employee = Employee.objects.create(
            full_name="Иванов Иван Иванович",
            organization=self.employee_org,
            position="Инженер",
        )
        self.category = Category.objects.get(code="N")
        self.one = Equipment.objects.create(
            category=self.category,
            name="Ноутбук",
            manufacturer="Honor",
            model="MagicBook X16",
            serial_number="SN-001",
            owner=self.owner,
            notes="Цена за единицу 64 000.00 руб.",
        )
        self.two = Equipment.objects.create(
            category=self.category,
            name="Мышь",
            manufacturer="Logitech",
            model="M170",
            serial_number="MOUSE-001",
            owner=self.owner,
            notes="Цена за единицу 999.00 руб.",
        )

    def _workflow_payload(self, equipment):
        return {
            "act_date": "2026-07-09",
            "city": "г. Тестовый",
            "organization_name": "ООО «РАБОТОДАТЕЛЬ»",
            "representative_position": "Генеральный директор",
            "representative_name": "Петрова Анна Сергеевна",
            "equipment": [item.pk for item in equipment],
            "notes": "Комплект для работы",
        }

    def test_employee_card_can_issue_and_return_equipment_with_acts(self):
        response = self.client.post(
            reverse("employee_issue_equipment", args=[self.employee.pk]),
            self._workflow_payload([self.one, self.two]),
        )
        self.assertEqual(response.status_code, 302)
        self.one.refresh_from_db(); self.two.refresh_from_db()
        self.assertEqual(self.one.responsible_employee, self.employee)
        self.assertEqual(self.two.responsible_employee, self.employee)
        issue_act = Act.objects.get(employee=self.employee, act_type=Act.ActType.ISSUE)
        self.assertEqual(issue_act.equipment.count(), 2)
        self.assertEqual(issue_act.movements.count(), 2)

        payload = self._workflow_payload([self.two])
        payload["representative_position"] = "Специалист по информационным технологиям"
        payload["representative_name"] = "Сидоров Сергей Иванович"
        response = self.client.post(
            reverse("employee_return_equipment", args=[self.employee.pk]), payload
        )
        self.assertEqual(response.status_code, 302)
        self.two.refresh_from_db()
        self.assertIsNone(self.two.responsible_employee)
        self.assertEqual(self.two.usage_status, Equipment.UsageStatus.STOCK)
        return_act = Act.objects.get(employee=self.employee, act_type=Act.ActType.RETURN)
        self.assertEqual(return_act.equipment.count(), 1)
        self.assertEqual(return_act.movements.count(), 1)

    def test_employee_card_and_operation_pages_open(self):
        self.assertEqual(self.client.get(reverse("employee_detail", args=[self.employee.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("employee_issue_equipment", args=[self.employee.pk])).status_code, 200)
        self.one.responsible_employee = self.employee
        self.one.usage_status = Equipment.UsageStatus.EMPLOYEE
        self.one.save()
        self.assertEqual(self.client.get(reverse("employee_return_equipment", args=[self.employee.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("employee_generate_act", args=[self.employee.pk, "issue"])).status_code, 200)

    def test_new_employee_redirects_to_issue_and_act_workflow(self):
        response = self.client.post(reverse("employee_add"), {
            "full_name": "Новый Сотрудник Тестовый",
            "position": "Инженер",
            "department": "ИТ",
            "workplace_location": "",
            "phone": "",
            "organization": self.employee_org.pk,
            "archived": "",
            "notes": "",
        })
        employee = Employee.objects.get(full_name="Новый Сотрудник Тестовый")
        self.assertRedirects(response, reverse("employee_assign_without_act", args=[employee.pk]))

    def test_generates_valid_issue_and_return_docx(self):
        for item in (self.one, self.two):
            item.responsible_employee = self.employee
            item.usage_status = Equipment.UsageStatus.EMPLOYEE
            item.save()
        payload = {
            "act_date": "2026-07-09",
            "city": "г. Тестовый",
            "organization_name": "ООО «РАБОТОДАТЕЛЬ»",
            "representative_position": "Генеральный директор",
            "representative_name": "Петрова Анна Сергеевна",
            "equipment": [self.one.pk, self.two.pk],
        }
        response = self.client.post(reverse("employee_generate_act", args=[self.employee.pk, "issue"]), payload)
        self.assertEqual(response.status_code, 302)
        act = Act.objects.get(employee=self.employee, act_type=Act.ActType.ISSUE)
        self.assertEqual(act.equipment.count(), 2)
        self.assertTrue(act.document.name.endswith(".docx"))

        response = self.client.post(reverse("employee_generate_act", args=[self.employee.pk, "return"]), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertTrue(response.content.startswith(b"PK"))
        self.assertGreater(len(response.content), 10000)

    def test_can_assign_equipment_without_act_and_mark_it_undocumented(self):
        response = self.client.post(
            reverse("employee_assign_without_act", args=[self.employee.pk]),
            {"equipment": [self.one.pk, self.two.pk], "notes": "Фактически уже выдано"},
        )
        self.assertRedirects(response, self.employee.get_absolute_url())
        self.one.refresh_from_db(); self.two.refresh_from_db()
        self.assertEqual(self.one.responsible_employee, self.employee)
        self.assertEqual(self.two.responsible_employee, self.employee)
        self.assertEqual(Act.objects.filter(employee=self.employee).count(), 0)
        movement = self.one.movements.get(movement_type=EquipmentMovement.MovementType.ASSIGNED)
        self.assertIsNone(movement.act_id)
        detail = self.client.get(self.employee.get_absolute_url())
        self.assertContains(detail, "Без акта")
        self.assertContains(detail, "Оформить акт · 2")

    def test_later_issue_act_is_saved_and_linked_to_actless_movements(self):
        self.client.post(
            reverse("employee_assign_without_act", args=[self.employee.pk]),
            {"equipment": [self.one.pk, self.two.pk], "notes": "Фактическая выдача"},
        )
        payload = {
            "act_date": "2026-07-10",
            "city": "г. Тестовый",
            "organization_name": "ООО «РАБОТОДАТЕЛЬ»",
            "representative_position": "Генеральный директор",
            "representative_name": "Петрова Анна Сергеевна",
            "equipment": [self.one.pk, self.two.pk],
        }
        response = self.client.post(
            reverse("employee_generate_act", args=[self.employee.pk, "issue"]), payload
        )
        self.assertEqual(response.status_code, 302)
        act = Act.objects.get(employee=self.employee, act_type=Act.ActType.ISSUE)
        self.assertEqual(set(act.equipment.values_list("pk", flat=True)), {self.one.pk, self.two.pk})
        self.assertEqual(
            EquipmentMovement.objects.filter(
                equipment__in=[self.one, self.two],
                movement_type=EquipmentMovement.MovementType.ASSIGNED,
                act=act,
            ).count(),
            2,
        )
        detail = self.client.get(self.employee.get_absolute_url())
        self.assertNotContains(detail, "Оформить акт · 2")
        self.assertContains(detail, "Акт есть")


class NavigationAndWorkplaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("nav-user", password="test-password")
        self.client.login(username="nav-user", password="test-password")
        self.organization = Organization.objects.create(name="ООО Работодатель", prefix="PG")
        self.location = Location.objects.create(
            organization=self.organization,
            address="г. Тестовый, ул. Примерная, д. 1",
            label="Главный офис",
        )
        self.employee = Employee.objects.create(
            full_name="Иванов Иван Иванович",
            organization=self.organization,
        )

    def test_employee_workplace_is_selected_from_locations(self):
        response = self.client.post(reverse("employee_edit", args=[self.employee.pk]), {
            "full_name": self.employee.full_name,
            "position": "Инженер",
            "department": "ИТ",
            "workplace_location": self.location.pk,
            "phone": "",
            "organization": self.organization.pk,
            "archived": "",
            "notes": "",
        })
        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.workplace, self.location.address)

    def test_archive_page_and_top_navigation_open(self):
        self.employee.archived = True
        self.employee.save(update_fields=["archived", "updated_at"])
        response = self.client.get(reverse("archive_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.employee.full_name)
        self.assertContains(response, "Объекты / адреса")

    def test_employee_detail_remembers_filtered_back_url(self):
        filtered = reverse("employee_list") + "?q=Иванов"
        response = self.client.get(
            reverse("employee_detail", args=[self.employee.pk]),
            {"back": filtered},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, filtered.replace("&", "&amp;"))
        response = self.client.get(reverse("employee_detail", args=[self.employee.pk]))
        self.assertContains(response, filtered.replace("&", "&amp;"))
