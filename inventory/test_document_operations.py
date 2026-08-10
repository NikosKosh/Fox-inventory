from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Contract, Counterparty, DocumentOperation, DocumentRecord, DocumentType, Organization


class DocumentOperationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("operation-user", password="pass")
        self.org = Organization.objects.create(name="Организация", prefix="OPTEST")
        self.cp = Counterparty.objects.create(name="Контрагент")
        self.contract = Contract.objects.create(
            organization=self.org,
            counterparty=self.cp,
            title="Договор поставки",
            number="1",
            category=Contract.Category.SUPPLY,
        )
        self.doc_type = DocumentType.objects.create(name="УПД тест", code="upd-test-operation", sort_order=999)
        self.operation = DocumentOperation.objects.create(
            organization=self.org,
            counterparty=self.cp,
            contract=self.contract,
            title="Поставка",
            operation_date=date(2026, 8, 10),
            created_by=self.user,
        )
        self.document = DocumentRecord.objects.create(
            organization=self.org,
            counterparty=self.cp,
            contract=self.contract,
            operation=self.operation,
            document_type=self.doc_type,
            title="УПД №1",
            file="documents/test-operation.pdf",
        )
        self.client.force_login(self.user)

    def test_operation_detail_contains_package_document(self):
        response = self.client.get(reverse("operation_detail", args=[self.operation.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Поставка")
        self.assertContains(response, "УПД №1")

    def test_contract_detail_separates_operation_documents(self):
        response = self.client.get(reverse("contract_detail", args=[self.contract.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Поставка")
        self.assertNotContains(response, "УПД №1</b><small>УПД тест")

    def test_document_inherits_operation_context(self):
        other = DocumentRecord(
            organization=self.org,
            operation=self.operation,
            document_type=self.doc_type,
            title="УПД №2",
            file="documents/test-operation-2.pdf",
        )
        other.save()
        self.assertEqual(other.contract_id, self.contract.pk)
        self.assertEqual(other.counterparty_id, self.cp.pk)
