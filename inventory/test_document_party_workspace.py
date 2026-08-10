from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (
    Contract,
    Counterparty,
    DocumentOperation,
    DocumentRecord,
    DocumentType,
    Organization,
)


class DocumentPartyWorkspaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("workspace-user", password="pass")
        self.client.force_login(self.user)

        self.org_a = Organization.objects.create(
            name="ООО Альфа",
            short_name="Альфа",
            prefix="ALPHA",
        )
        self.org_b = Organization.objects.create(
            name="ООО Бета",
            short_name="Бета",
            prefix="BETA",
        )

        self.cp_b = Counterparty.objects.create(
            name=self.org_b.name,
            short_name=self.org_b.short_name,
            linked_organization=self.org_b,
        )
        self.cp_a = Counterparty.objects.create(
            name=self.org_a.name,
            short_name=self.org_a.short_name,
            linked_organization=self.org_a,
        )
        self.external = Counterparty.objects.create(name="ООО Внешний поставщик")

        self.internal_contract = Contract.objects.create(
            organization=self.org_a,
            counterparty=self.cp_b,
            title="Договор поставки",
            number="7",
            contract_date=date(2026, 8, 1),
            category=Contract.Category.SUPPLY,
        )
        self.external_contract = Contract.objects.create(
            organization=self.org_a,
            counterparty=self.external,
            title="Сервисный договор",
            number="15",
            contract_date=date(2026, 8, 2),
            category=Contract.Category.SERVICES,
        )

        doc_type = DocumentType.objects.create(
            name="УПД workspace",
            code="upd-workspace",
            sort_order=991,
        )

        self.internal_operation = DocumentOperation.objects.create(
            organization=self.org_a,
            counterparty=self.cp_b,
            contract=self.internal_contract,
            title="Поставка оборудования",
            operation_date=date(2026, 8, 3),
            created_by=self.user,
        )
        self.internal_document = DocumentRecord.objects.create(
            organization=self.org_a,
            counterparty=self.cp_b,
            contract=self.internal_contract,
            operation=self.internal_operation,
            document_type=doc_type,
            title="УПД внутренней пары",
            file="documents/workspace-internal.pdf",
        )
        self.external_document = DocumentRecord.objects.create(
            organization=self.org_a,
            counterparty=self.external,
            contract=self.external_contract,
            document_type=doc_type,
            title="Документ внешнего поставщика",
            file="documents/workspace-external.pdf",
        )

    def test_internal_contract_is_visible_from_both_organizations(self):
        response = self.client.get(
            reverse("organization_document_workspace", args=[self.org_b.pk]),
            {"party": f"org:{self.org_a.pk}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Договор поставки")
        self.assertContains(response, "Поставка оборудования")
        self.assertContains(response, "УПД внутренней пары")

    def test_partner_selection_does_not_mix_other_counterparties(self):
        response = self.client.get(
            reverse("organization_document_workspace", args=[self.org_a.pk]),
            {"party": f"cp:{self.external.pk}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Сервисный договор")
        self.assertContains(response, "Документ внешнего поставщика")
        self.assertNotContains(response, "Поставка оборудования")
        self.assertNotContains(response, "УПД внутренней пары")

    def test_unselected_workspace_is_partner_picker_not_flat_registry(self):
        response = self.client.get(
            reverse("organization_document_workspace", args=[self.org_a.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "С кем работаем?")
        self.assertContains(response, "ООО Бета")
        self.assertContains(response, "ООО Внешний поставщик")
        self.assertNotContains(response, "Документы вне операций")

    def test_contract_string_does_not_duplicate_number(self):
        contract = Contract(
            organization=self.org_a,
            counterparty=self.external,
            title="Договор №ABC-42",
            number="ABC-42",
        )
        self.assertEqual(str(contract), "Договор №ABC-42")
