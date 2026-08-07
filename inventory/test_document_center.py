import shutil
import tempfile
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Contract, Counterparty, DocumentRecord, DocumentType, Organization, Reminder


class DocumentCenterTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix="fox-inventory-doc-tests-")
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(lambda: shutil.rmtree(self.media_root, ignore_errors=True))

        self.user = get_user_model().objects.create_user(
            username="admin", password="Strong-Test-Password-17", is_staff=True
        )
        self.client.force_login(self.user)
        self.org_a = Organization.objects.create(name="Организация A", short_name="A", prefix="OA")
        self.org_b = Organization.objects.create(name="Организация B", short_name="B", prefix="OB")
        self.counterparty = Counterparty.objects.create(name="ПАО Тест", inn="1234567890")
        self.doc_type = DocumentType.objects.create(name="Тестовый счёт", code="test-invoice", sort_order=1)

    def pdf(self, name="document.pdf"):
        return SimpleUploadedFile(name, b"%PDF-1.4\n% FOX Inventory test\n", content_type="application/pdf")

    def test_contract_document_inherits_contract_context(self):
        contract = Contract.objects.create(
            organization=self.org_a,
            counterparty=self.counterparty,
            title="Интернет",
            number="123",
            created_by=self.user,
        )
        document = DocumentRecord.objects.create(
            organization=self.org_b,
            contract=contract,
            document_type=self.doc_type,
            file=self.pdf(),
            original_name="document.pdf",
            created_by=self.user,
        )
        document.refresh_from_db()
        self.assertEqual(document.organization, self.org_a)
        self.assertEqual(document.counterparty, self.counterparty)

    def test_document_list_filters_by_organization(self):
        first = DocumentRecord.objects.create(
            organization=self.org_a, document_type=self.doc_type, file=self.pdf("a.pdf"), original_name="a.pdf"
        )
        second = DocumentRecord.objects.create(
            organization=self.org_b, document_type=self.doc_type, file=self.pdf("b.pdf"), original_name="b.pdf"
        )
        response = self.client.get(reverse("document_list"), {"organization": self.org_a.pk})
        self.assertContains(response, first.original_name)
        self.assertNotContains(response, second.original_name)

    def test_inbox_accepts_multiple_files_without_classification(self):
        response = self.client.post(
            reverse("document_inbox"),
            {"organization": self.org_a.pk, "files": [self.pdf("one.pdf"), self.pdf("two.pdf")]},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        documents = DocumentRecord.objects.filter(organization=self.org_a)
        self.assertEqual(documents.count(), 2)
        self.assertEqual(documents.filter(document_type__isnull=True).count(), 2)

    def test_document_moves_to_trash_without_deleting_file(self):
        document = DocumentRecord.objects.create(
            organization=self.org_a,
            document_type=self.doc_type,
            file=self.pdf("keep.pdf"),
            original_name="keep.pdf",
            created_by=self.user,
        )
        stored_name = document.file.name
        response = self.client.post(reverse("document_trash", args=[document.pk]))
        self.assertRedirects(response, reverse("document_list"))
        document.refresh_from_db()
        self.assertIsNotNone(document.trashed_at)
        self.assertTrue(document.file.storage.exists(stored_name))

    def test_monthly_reminder_advances_when_completed(self):
        due = timezone.localdate()
        reminder = Reminder.objects.create(
            title="Оплатить интернет",
            organization=self.org_a,
            counterparty=self.counterparty,
            next_due_date=due,
            recurrence=Reminder.Recurrence.MONTHLY,
            amount=Decimal("1000.00"),
            created_by=self.user,
        )
        response = self.client.post(reverse("reminder_done", args=[reminder.pk]))
        self.assertRedirects(response, reverse("reminder_list"))
        reminder.refresh_from_db()
        self.assertTrue(reminder.active)
        self.assertGreater(reminder.next_due_date, due)
        self.assertIsNotNone(reminder.last_completed_at)

    def test_one_time_reminder_becomes_inactive(self):
        reminder = Reminder.objects.create(
            title="Продлить домен",
            next_due_date=date(2026, 10, 15),
            recurrence=Reminder.Recurrence.ONCE,
            created_by=self.user,
        )
        self.client.post(reverse("reminder_done", args=[reminder.pk]))
        reminder.refresh_from_db()
        self.assertFalse(reminder.active)

    def test_dashboard_exposes_document_counts(self):
        Contract.objects.create(
            organization=self.org_a,
            counterparty=self.counterparty,
            title="Интернет",
            created_by=self.user,
        )
        DocumentRecord.objects.create(
            organization=self.org_a,
            document_type=self.doc_type,
            file=self.pdf("invoice.pdf"),
            original_name="invoice.pdf",
            created_by=self.user,
        )
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "документов")
        self.assertContains(response, "договоров")
