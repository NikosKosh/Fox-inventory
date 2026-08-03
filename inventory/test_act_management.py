import shutil
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import EmployeeActDocumentForm, EmployeeEquipmentActWorkflowForm
from .models import Act, Category, Employee, Equipment, EquipmentMovement, Organization


class ActDefaultsTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="ООО Тест",
            prefix="TST",
            act_organization_name="ООО «ТЕСТ»",
            act_city="г. Тестовый",
            act_issue_representative_position="Ответственный за выдачу",
            act_issue_representative_name="Представитель В.В.",
            act_return_representative_position="Ответственный за приём",
            act_return_representative_name="Петров П.П.",
        )
        self.employee = Employee.objects.create(full_name="Иванов И.И.", organization=self.organization)

    def test_issue_form_uses_organization_defaults(self):
        form = EmployeeEquipmentActWorkflowForm(employee=self.employee, operation="issue")
        self.assertEqual(form.fields["organization_name"].initial, "ООО «ТЕСТ»")
        self.assertEqual(form.fields["city"].initial, "г. Тестовый")
        self.assertEqual(form.fields["representative_position"].initial, "Ответственный за выдачу")
        self.assertEqual(form.fields["representative_name"].initial, "Представитель В.В.")

    def test_return_form_uses_separate_defaults(self):
        form = EmployeeActDocumentForm(employee=self.employee, act_type="return")
        self.assertEqual(form.fields["representative_position"].initial, "Ответственный за приём")
        self.assertEqual(form.fields["representative_name"].initial, "Петров П.П.")

    def test_submitted_values_can_override_defaults(self):
        category = Category.objects.get(code="N")
        equipment = Equipment.objects.create(
            category=category, name="Ноутбук", owner=self.organization
        )
        form = EmployeeEquipmentActWorkflowForm(
            data={
                "act_date": "2026-08-03",
                "city": "г. Другой",
                "organization_name": "Другое наименование",
                "representative_position": "Другая должность",
                "representative_name": "Сидоров С.С.",
                "equipment": [equipment.pk],
                "notes": "",
            },
            employee=self.employee,
            operation="issue",
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["representative_name"], "Сидоров С.С.")


class ActDeletionTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix="fox-inventory-test-media-")
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(lambda: shutil.rmtree(self.media_root, ignore_errors=True))

        self.staff = get_user_model().objects.create_user(
            "admin", password="test-password", is_staff=True
        )
        self.regular = get_user_model().objects.create_user(
            "operator", password="test-password", is_staff=False
        )
        self.organization = Organization.objects.create(name="ООО Тест", prefix="DEL")
        self.employee = Employee.objects.create(full_name="Иванов И.И.", organization=self.organization)
        self.category = Category.objects.get(code="N")
        self.equipment = Equipment.objects.create(
            category=self.category,
            name="Ноутбук",
            owner=self.organization,
            responsible_employee=self.employee,
            usage_status=Equipment.UsageStatus.EMPLOYEE,
        )

    def _act(self, number):
        act = Act.objects.create(
            number=number,
            act_type=Act.ActType.ISSUE,
            employee=self.employee,
            from_organization=self.organization,
            document=SimpleUploadedFile(
                f"{number}.pdf", b"%PDF-1.4 test", content_type="application/pdf"
            ),
        )
        act.equipment.add(self.equipment)
        EquipmentMovement.objects.create(
            equipment=self.equipment,
            movement_type=EquipmentMovement.MovementType.ASSIGNED,
            to_employee=self.employee,
            from_status=Equipment.UsageStatus.STOCK,
            to_status=Equipment.UsageStatus.EMPLOYEE,
            act=act,
            created_by=self.staff,
        )
        return act

    def test_staff_can_delete_act_without_reverting_equipment_or_history(self):
        act = self._act("TEST-1")
        document_path = Path(self.media_root) / act.document.name
        self.assertTrue(document_path.exists())
        movement = act.movements.get()
        self.client.force_login(self.staff)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("act_delete", args=[act.pk]))

        self.assertRedirects(response, reverse("act_list"))
        self.assertFalse(Act.objects.filter(pk=act.pk).exists())
        self.equipment.refresh_from_db()
        movement.refresh_from_db()
        self.assertEqual(self.equipment.responsible_employee, self.employee)
        self.assertEqual(self.equipment.usage_status, Equipment.UsageStatus.EMPLOYEE)
        self.assertIsNone(movement.act_id)
        self.assertFalse(document_path.exists())

    def test_regular_user_cannot_delete_act(self):
        act = self._act("TEST-2")
        self.client.force_login(self.regular)
        response = self.client.post(reverse("act_delete", args=[act.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Act.objects.filter(pk=act.pk).exists())

    def test_bulk_delete_requires_confirmation_and_deletes_selected_only(self):
        first = self._act("TEST-3")
        second = self._act("TEST-4")
        untouched = self._act("REAL-1")
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("act_bulk_delete"),
            {"acts": [first.pk, second.pk]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Удалить выбранные акты")
        self.assertEqual(Act.objects.count(), 3)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("act_bulk_delete"),
                {"acts": [first.pk, second.pk], "confirm": "1"},
            )
        self.assertRedirects(response, reverse("act_list"))
        self.assertFalse(Act.objects.filter(pk__in=[first.pk, second.pk]).exists())
        self.assertTrue(Act.objects.filter(pk=untouched.pk).exists())
