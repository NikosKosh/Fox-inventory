from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Contract, Counterparty, DocumentRecord, DocumentType, Organization


class OrganizationDocumentWorkspaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="pass")
        self.sz = Organization.objects.create(name="ООО «СЗ ПРОЕКТ-С-23»", prefix="SZT")
        self.shop = Organization.objects.create(name="ООО «ФОКС-АЙТИ ШОП»", prefix="SHOPTEST")
        self.shop_cp = Counterparty.objects.create(name="ООО «ФОКС-АЙТИ ШОП»")
        self.contract = Contract.objects.create(
            organization=self.sz,
            counterparty=self.shop_cp,
            title="Договор поставки",
            number="3",
        )
        self.doc_type = DocumentType.objects.create(name="Тестовый", code="test-party", sort_order=999)
        self.document = DocumentRecord.objects.create(
            organization=self.sz,
            counterparty=self.shop_cp,
            contract=self.contract,
            document_type=self.doc_type,
            title="УПД №1",
            file="documents/test.pdf",
        )
        self.client.force_login(self.user)

    def test_second_internal_party_sees_shared_contract_and_document(self):
        response = self.client.get(reverse("organization_document_workspace", args=[self.shop.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Договор поставки")
        self.assertContains(response, "УПД №1")
        self.assertEqual(response.context["contracts_total"], 1)
        self.assertEqual(response.context["documents_total"], 1)

    def test_first_party_sees_shared_contract_and_document(self):
        response = self.client.get(reverse("organization_document_workspace", args=[self.sz.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["contracts_total"], 1)
        self.assertEqual(response.context["documents_total"], 1)
