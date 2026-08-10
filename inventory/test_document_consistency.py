import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.datastructures import MultiValueDict

from .document_forms import DocumentUploadForm
from .models import (
    Contract,
    Counterparty,
    DocumentActivity,
    DocumentFileVersion,
    DocumentOperation,
    DocumentRecord,
    DocumentType,
    Organization,
    OrganizationCounterpartyLink,
)


PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"


class DocumentConsistencySafetyTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(
            prefix="fox-inventory-consistency-"
        )
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_root
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(
            shutil.rmtree,
            self.media_root,
            True,
        )

        self.user = get_user_model().objects.create_user(
            "consistency-user",
            password="pass",
        )
        self.client.force_login(self.user)

        self.org = Organization.objects.create(
            name="ООО Consistency",
            short_name="Consistency",
            prefix="CONS",
        )
        self.cp = Counterparty.objects.create(
            name="ООО Partner",
            short_name="Partner",
            inn="6161001001",
        )
        OrganizationCounterpartyLink.objects.create(
            organization=self.org,
            counterparty=self.cp,
            created_by=self.user,
        )
        self.invoice_type, _ = DocumentType.objects.get_or_create(
            code="invoice",
            defaults={
                "name": "Счёт consistency",
                "sort_order": 700,
            },
        )
        self.upd_type, _ = DocumentType.objects.get_or_create(
            code="upd",
            defaults={
                "name": "УПД consistency",
                "sort_order": 701,
            },
        )

    def make_document(
        self,
        name,
        *,
        operation=None,
        document_type=None,
        number="",
    ):
        return DocumentRecord.objects.create(
            organization=self.org,
            counterparty=self.cp,
            operation=operation,
            document_type=document_type or self.invoice_type,
            number=number,
            file=SimpleUploadedFile(
                name,
                PDF,
                content_type="application/pdf",
            ),
            original_name=name,
            created_by=self.user,
        )

    def test_grouped_registry_collapses_package_to_one_record(self):
        first = self.make_document("invoice-a.pdf")
        second = self.make_document(
            "upd-a.pdf",
            document_type=self.upd_type,
        )
        third = self.make_document("invoice-b.pdf")

        before = self.client.get(
            reverse("document_list"),
            {"mode": "grouped"},
        )
        self.assertEqual(before.status_code, 200)
        self.assertEqual(before.context["logical_total"], 3)
        self.assertEqual(before.context["matching_files_total"], 3)

        operation = DocumentOperation.objects.create(
            organization=self.org,
            counterparty=self.cp,
            title="Поставка 1",
            created_by=self.user,
        )
        first.operation = operation
        first.save(update_fields=["operation", "updated_at"])
        second.operation = operation
        second.save(update_fields=["operation", "updated_at"])

        after = self.client.get(
            reverse("document_list"),
            {"mode": "grouped"},
        )
        self.assertEqual(after.status_code, 200)
        self.assertEqual(after.context["logical_total"], 2)
        self.assertEqual(after.context["matching_files_total"], 3)
        self.assertContains(after, "Поставка 1")
        self.assertContains(after, third.display_title)

    def test_existing_package_document_cannot_be_grouped_again(self):
        operation = DocumentOperation.objects.create(
            organization=self.org,
            counterparty=self.cp,
            title="Уже собран",
            created_by=self.user,
        )
        document = self.make_document(
            "already.pdf",
            operation=operation,
        )

        response = self.client.post(
            reverse("operation_from_documents"),
            {"selected_documents": [document.pk]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            DocumentOperation.objects.filter(
                organization=self.org
            ).count(),
            1,
        )

    def test_disband_keeps_files_and_deletes_only_package(self):
        operation = DocumentOperation.objects.create(
            organization=self.org,
            counterparty=self.cp,
            title="Ошибочный пакет",
            created_by=self.user,
        )
        first = self.make_document(
            "one.pdf",
            operation=operation,
        )
        second = self.make_document(
            "two.pdf",
            operation=operation,
        )

        response = self.client.post(
            reverse(
                "operation_disband",
                args=[operation.pk],
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            DocumentOperation.objects.filter(
                pk=operation.pk
            ).exists()
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(first.operation_id)
        self.assertIsNone(second.operation_id)
        self.assertEqual(
            DocumentRecord.objects.filter(
                pk__in=[first.pk, second.pk]
            ).count(),
            2,
        )

    def test_remove_and_move_document_between_compatible_packages(self):
        source = DocumentOperation.objects.create(
            organization=self.org,
            counterparty=self.cp,
            title="Источник",
            created_by=self.user,
        )
        target = DocumentOperation.objects.create(
            organization=self.org,
            counterparty=self.cp,
            title="Назначение",
            created_by=self.user,
        )
        document = self.make_document(
            "move.pdf",
            operation=source,
        )

        moved = self.client.post(
            reverse(
                "operation_move_document",
                args=[source.pk, document.pk],
            ),
            {"target_operation": target.pk},
        )
        self.assertEqual(moved.status_code, 302)
        document.refresh_from_db()
        self.assertEqual(document.operation_id, target.pk)

        removed = self.client.post(
            reverse(
                "operation_remove_document",
                args=[target.pk, document.pk],
            )
        )
        self.assertEqual(removed.status_code, 302)
        document.refresh_from_db()
        self.assertIsNone(document.operation_id)

    def test_quick_upload_does_not_invent_document_date(self):
        operation = DocumentOperation.objects.create(
            organization=self.org,
            counterparty=self.cp,
            title="Поставка",
            operation_date=timezone.localdate(),
            created_by=self.user,
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
                        PDF,
                        content_type="application/pdf",
                    )
                ]
            },
        )
        self.assertEqual(response.status_code, 302)
        document = DocumentRecord.objects.get(
            operation=operation
        )
        self.assertIsNone(document.document_date)
        self.assertEqual(
            document.classification_source,
            DocumentRecord.ClassificationSource.FILENAME,
        )
        self.assertTrue(document.file_sha256)

    def test_duplicate_binary_is_rejected_on_second_upload(self):
        operation = DocumentOperation.objects.create(
            organization=self.org,
            counterparty=self.cp,
            title="Поставка",
            created_by=self.user,
        )
        url = reverse(
            "operation_quick_upload",
            args=[operation.pk],
        )

        first = self.client.post(
            url,
            {
                "files": [
                    SimpleUploadedFile(
                        "first.pdf",
                        PDF,
                        content_type="application/pdf",
                    )
                ]
            },
        )
        self.assertEqual(first.status_code, 302)

        second = self.client.post(
            url,
            {
                "files": [
                    SimpleUploadedFile(
                        "copy.pdf",
                        PDF,
                        content_type="application/pdf",
                    )
                ]
            },
        )
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            DocumentRecord.objects.filter(
                operation=operation
            ).count(),
            1,
        )

    def test_multi_file_form_rejects_shared_document_requisites(self):
        form = DocumentUploadForm(
            data={
                "organization": self.org.pk,
                "counterparty": self.cp.pk,
                "document_date": "2026-08-10",
                "number": "ONE-NUMBER",
                "title": "",
                "amount": "",
                "notes": "",
                "contract": "",
                "operation": "",
                "location": "",
                "document_type": "",
                "equipment": [],
            },
            files=MultiValueDict(
                {
                    "files": [
                        SimpleUploadedFile(
                            "a.pdf",
                            PDF,
                            content_type="application/pdf",
                        ),
                        SimpleUploadedFile(
                            "b.pdf",
                            PDF + b"2",
                            content_type="application/pdf",
                        ),
                    ]
                }
            ),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("document_date", form.errors)
        self.assertIn("number", form.errors)

    def test_internal_organization_filter_is_symmetric(self):
        other_org = Organization.objects.create(
            name="ООО Internal B",
            short_name="Internal B",
            prefix="INTB",
        )
        internal_cp = Counterparty.objects.create(
            name="ООО Internal B",
            short_name="Internal B",
            linked_organization=other_org,
        )
        document = DocumentRecord.objects.create(
            organization=self.org,
            counterparty=internal_cp,
            document_type=self.invoice_type,
            title="Симметричный файл",
            file=SimpleUploadedFile(
                "symmetric.pdf",
                PDF + b"s",
                content_type="application/pdf",
            ),
            original_name="symmetric.pdf",
            created_by=self.user,
        )

        response = self.client.get(
            reverse("document_list"),
            {
                "mode": "files",
                "organization": other_org.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, document.display_title)

    def test_empty_party_link_can_be_unlinked_but_used_link_cannot(self):
        empty_cp = Counterparty.objects.create(
            name="ООО Empty",
            short_name="Empty",
        )
        empty_link = OrganizationCounterpartyLink.objects.create(
            organization=self.org,
            counterparty=empty_cp,
            created_by=self.user,
        )

        response = self.client.post(
            reverse(
                "organization_party_unlink",
                args=[self.org.pk, empty_cp.pk],
            )
        )
        self.assertEqual(response.status_code, 302)
        empty_link.refresh_from_db()
        self.assertTrue(empty_link.archived)

        contract = Contract.objects.create(
            organization=self.org,
            counterparty=self.cp,
            title="Договор с данными",
            created_by=self.user,
        )
        used_link = OrganizationCounterpartyLink.objects.get(
            organization=self.org,
            counterparty=self.cp,
        )
        response = self.client.post(
            reverse(
                "organization_party_unlink",
                args=[self.org.pk, self.cp.pk],
            )
        )
        self.assertEqual(response.status_code, 302)
        used_link.refresh_from_db()
        self.assertFalse(used_link.archived)
        self.assertTrue(
            Contract.objects.filter(pk=contract.pk).exists()
        )

    def test_replacing_file_creates_version_and_activity(self):
        document = self.make_document("old.pdf")
        response = self.client.post(
            reverse(
                "document_edit",
                args=[document.pk],
            ),
            {
                "organization": self.org.pk,
                "document_type": self.invoice_type.pk,
                "counterparty": self.cp.pk,
                "contract": "",
                "operation": "",
                "location": "",
                "equipment": [],
                "title": document.title,
                "number": "",
                "document_date": "",
                "amount": "",
                "notes": "",
                "file": SimpleUploadedFile(
                    "new.pdf",
                    PDF + b"new",
                    content_type="application/pdf",
                ),
            },
        )
        self.assertEqual(response.status_code, 302)
        document.refresh_from_db()
        self.assertEqual(
            DocumentFileVersion.objects.filter(
                document=document
            ).count(),
            1,
        )
        self.assertTrue(
            DocumentActivity.objects.filter(
                document=document,
                action="edited",
            ).exists()
        )
        self.assertEqual(document.original_name, "new.pdf")


