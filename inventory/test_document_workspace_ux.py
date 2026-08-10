import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .document_forms import CounterpartyForm
from .models import (
    Contract,
    Counterparty,
    DocumentOperation,
    DocumentRecord,
    DocumentType,
    Organization,
    OrganizationCounterpartyLink,
)


class DocumentWorkspaceUXTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix="fox-inventory-ux-")
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, True)

        self.user = get_user_model().objects.create_user(
            "workspace-ux-user",
            password="pass",
        )
        self.client.force_login(self.user)

        self.organization = Organization.objects.create(
            name="ООО UX Организация",
            short_name="UX Организация",
            prefix="UXORG",
        )

    def test_quick_create_party_is_visible_before_any_documents(self):
        response = self.client.post(
            reverse(
                "organization_party_add",
                args=[self.organization.pk],
            ),
            {
                "name": "ООО Новая сторона",
                "short_name": "Новая сторона",
                "inn": "6161000001",
            },
        )
        self.assertEqual(response.status_code, 302)

        counterparty = Counterparty.objects.get(
            inn="6161000001"
        )
        self.assertTrue(
            OrganizationCounterpartyLink.objects.filter(
                organization=self.organization,
                counterparty=counterparty,
                archived=False,
            ).exists()
        )

        workspace = self.client.get(
            reverse(
                "organization_document_workspace",
                args=[self.organization.pk],
            ),
            {"party": f"cp:{counterparty.pk}"},
        )
        self.assertEqual(workspace.status_code, 200)
        self.assertContains(workspace, "Новая сторона")
        self.assertContains(workspace, "Сторона уже сохранена")
        self.assertContains(workspace, "Создать договор")
        self.assertContains(workspace, "Просто загрузить документ")

    def test_existing_counterparty_can_be_attached_without_duplicate(self):
        counterparty = Counterparty.objects.create(
            name="ООО Уже существует",
            short_name="Уже существует",
            inn="6161000002",
        )

        response = self.client.post(
            reverse(
                "organization_party_add",
                args=[self.organization.pk],
            ),
            {"existing_counterparty": counterparty.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Counterparty.objects.filter(inn="6161000002").count(),
            1,
        )
        self.assertTrue(
            OrganizationCounterpartyLink.objects.filter(
                organization=self.organization,
                counterparty=counterparty,
            ).exists()
        )

    def test_quick_create_reuses_matching_inn(self):
        existing = Counterparty.objects.create(
            name="ООО Реальная карточка",
            short_name="Реальная",
            inn="6161000003",
        )

        response = self.client.post(
            reverse(
                "organization_party_add",
                args=[self.organization.pk],
            ),
            {
                "name": "ООО Другая запись",
                "inn": "6161000003",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Counterparty.objects.filter(inn="6161000003").count(),
            1,
        )
        self.assertTrue(
            OrganizationCounterpartyLink.objects.filter(
                organization=self.organization,
                counterparty=existing,
            ).exists()
        )

    def test_counterparty_form_blocks_duplicate_inn(self):
        Counterparty.objects.create(
            name="ООО Первая",
            inn="6161000004",
        )
        form = CounterpartyForm(
            data={
                "name": "ООО Вторая",
                "short_name": "",
                "linked_organization": "",
                "inn": "6161000004",
                "kpp": "",
                "contact_name": "",
                "phone": "",
                "email": "",
                "notes": "",
                "archived": False,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("inn", form.errors)

    def test_operation_quick_upload_infers_document_types(self):
        counterparty = Counterparty.objects.create(
            name="ООО Пакет",
            short_name="Пакет",
        )
        operation = DocumentOperation.objects.create(
            organization=self.organization,
            counterparty=counterparty,
            title="Поставка",
            created_by=self.user,
        )
        invoice_type, _ = DocumentType.objects.get_or_create(
            code="invoice",
            defaults={
                "name": "Счёт",
                "sort_order": 10,
            },
        )
        upd_type, _ = DocumentType.objects.get_or_create(
            code="upd",
            defaults={
                "name": "УПД",
                "sort_order": 20,
            },
        )

        response = self.client.post(
            reverse(
                "operation_quick_upload",
                args=[operation.pk],
            ),
            {
                "files": [
                    SimpleUploadedFile(
                        "Счет 77.pdf",
                        b"%PDF-1.4\n%%EOF",
                        content_type="application/pdf",
                    ),
                    SimpleUploadedFile(
                        "УПД 77.pdf",
                        b"%PDF-1.4\n%%EOF",
                        content_type="application/pdf",
                    ),
                ]
            },
        )

        self.assertEqual(response.status_code, 302)
        documents = DocumentRecord.objects.filter(
            operation=operation
        ).order_by("pk")
        self.assertEqual(documents.count(), 2)
        self.assertEqual(
            set(documents.values_list("document_type_id", flat=True)),
            {invoice_type.pk, upd_type.pk},
        )

    def test_contract_form_locks_selected_relationship(self):
        counterparty = Counterparty.objects.create(
            name="ООО Контекст",
            short_name="Контекст",
        )
        response = self.client.get(
            reverse("contract_add"),
            {
                "organization": self.organization.pk,
                "counterparty": counterparty.pk,
                "view": self.organization.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Контекст")
        self.assertContains(response, "disabled")

