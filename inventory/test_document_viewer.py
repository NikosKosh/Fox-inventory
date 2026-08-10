import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (
    Contract,
    Counterparty,
    DocumentOperation,
    DocumentRecord,
    DocumentType,
    Organization,
)


class DocumentViewerTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix="fox-inventory-viewer-")
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, True)

        self.user = get_user_model().objects.create_user(
            "viewer-user",
            password="pass",
        )
        self.client.force_login(self.user)

        self.organization = Organization.objects.create(
            name="ООО Просмотр",
            short_name="Просмотр",
            prefix="VIEW",
        )
        self.counterparty = Counterparty.objects.create(
            name="ООО Контрагент просмотра",
            short_name="Контрагент",
        )
        self.contract = Contract.objects.create(
            organization=self.organization,
            counterparty=self.counterparty,
            title="Договор просмотра",
            number="V-1",
            main_file=SimpleUploadedFile(
                "contract.pdf",
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
                content_type="application/pdf",
            ),
        )
        self.operation = DocumentOperation.objects.create(
            organization=self.organization,
            counterparty=self.counterparty,
            contract=self.contract,
            title="Поставка для просмотра",
            created_by=self.user,
        )
        self.invoice_type = DocumentType.objects.create(
            name="Счёт viewer",
            code="invoice-viewer",
            sort_order=980,
        )
        self.upd_type = DocumentType.objects.create(
            name="УПД viewer",
            code="upd-viewer",
            sort_order=981,
        )
        self.invoice = DocumentRecord.objects.create(
            organization=self.organization,
            counterparty=self.counterparty,
            contract=self.contract,
            operation=self.operation,
            document_type=self.invoice_type,
            title="Счёт №10",
            file=SimpleUploadedFile(
                "invoice.pdf",
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
                content_type="application/pdf",
            ),
            original_name="Счёт №10.pdf",
            created_by=self.user,
        )
        self.upd = DocumentRecord.objects.create(
            organization=self.organization,
            counterparty=self.counterparty,
            contract=self.contract,
            operation=self.operation,
            document_type=self.upd_type,
            title="УПД №11",
            file=SimpleUploadedFile(
                "upd.pdf",
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
                content_type="application/pdf",
            ),
            original_name="УПД №11.pdf",
            created_by=self.user,
        )

    def test_pdf_preview_is_inline(self):
        response = self.client.get(
            reverse("document_preview", args=[self.invoice.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response["Content-Disposition"].startswith("inline;")
        )
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")

    def test_document_viewer_shows_preview_and_package_navigation(self):
        response = self.client.get(
            reverse("document_detail", args=[self.upd.pk]),
            {"view": self.organization.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("document_preview", args=[self.upd.pk]),
        )
        self.assertContains(response, "Счёт №10")
        self.assertContains(response, "2 из 2")
        self.assertContains(response, "Поставка для просмотра")

    def test_contract_main_file_has_protected_viewer(self):
        response = self.client.get(
            reverse("contract_file_view", args=[self.contract.pk]),
            {"view": self.organization.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("contract_file_preview", args=[self.contract.pk]),
        )
        preview = self.client.get(
            reverse("contract_file_preview", args=[self.contract.pk])
        )
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(
            preview["Content-Disposition"].startswith("inline;")
        )

    def test_office_file_uses_download_fallback(self):
        office = DocumentRecord.objects.create(
            organization=self.organization,
            counterparty=self.counterparty,
            document_type=self.invoice_type,
            title="Таблица",
            file=SimpleUploadedFile(
                "table.xlsx",
                b"not-a-real-xlsx",
                content_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
            ),
            original_name="table.xlsx",
            created_by=self.user,
        )
        response = self.client.get(
            reverse("document_detail", args=[office.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Предпросмотр этого формата недоступен",
        )
        self.assertContains(
            response,
            reverse("document_download", args=[office.pk]),
        )
        self.assertNotContains(
            response,
            reverse("document_preview", args=[office.pk]),
        )
