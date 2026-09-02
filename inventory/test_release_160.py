from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .catalog import ensure_catalog_item, return_price_overrides, snapshot_act_items
from .forms import ActForm
from .models import Act, CatalogItem, CatalogPriceHistory, Category, Employee, Equipment, Organization


class Release160CatalogPricingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user("staff160", password="test", is_staff=True)
        self.owner = Organization.objects.create(name="ООО 160", prefix="R160")
        self.category = Category.objects.create(name="Ноутбук 160", code="N160")
        self.employee = Employee.objects.create(full_name="Иванов Иван Иванович", organization=self.owner)
        self.catalog = CatalogItem.objects.create(
            category=self.category,
            accounting_group=Equipment.AccountingGroup.EMPLOYEE,
            name="Honor MagicBook X16 2026",
            manufacturer="Honor",
            model="MagicBook X16 2026 (5301ARGQ), 16 ГБ, SSD 512 ГБ",
            unit_price=Decimal("62499.00"),
        )

    def equipment(self, code, serial):
        return Equipment.objects.create(
            internal_code=code,
            catalog_item=self.catalog,
            category=self.category,
            accounting_group=Equipment.AccountingGroup.EMPLOYEE,
            name="legacy name",
            manufacturer="legacy maker",
            model="legacy model",
            serial_number=serial,
            owner=self.owner,
            usage_status=Equipment.UsageStatus.STOCK,
            condition=Equipment.Condition.NEW,
        )

    def test_catalog_price_is_shared_by_all_physical_units(self):
        first = self.equipment("R160-N160-001", "SN160-1")
        second = self.equipment("R160-N160-002", "SN160-2")
        self.assertEqual(first.unit_price, Decimal("62499.00"))
        self.assertEqual(second.unit_price, Decimal("62499.00"))
        self.assertEqual(first.display_name, "Honor MagicBook X16 2026")
        self.assertEqual(first.name, "Honor MagicBook X16 2026")

        self.catalog.unit_price = Decimal("65000.00")
        self.catalog.save()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.unit_price, Decimal("65000.00"))
        self.assertEqual(second.unit_price, Decimal("65000.00"))

    def test_sku_identity_merges_cosmetic_model_variants(self):
        first = ensure_catalog_item(
            category=self.category,
            accounting_group=Equipment.AccountingGroup.EMPLOYEE,
            name="Мышь беспроводная",
            manufacturer="Logitech",
            model="M170 (910-004642), Grey",
        )
        second = ensure_catalog_item(
            category=self.category,
            accounting_group=Equipment.AccountingGroup.EMPLOYEE,
            name="Мышь беспроводная",
            manufacturer="Logitech",
            model="M170 (910-004642)",
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.sku, "910-004642")

    def test_manual_price_change_creates_history_and_confirms_price(self):
        self.catalog.price_needs_review = True
        self.catalog.save(update_fields=["price_needs_review", "updated_at"])
        self.client.force_login(self.staff)
        response = self.client.post(reverse("catalog_edit", args=[self.catalog.pk]), {
            "category": self.category.pk,
            "accounting_group": Equipment.AccountingGroup.EMPLOYEE,
            "name": self.catalog.name,
            "manufacturer": self.catalog.manufacturer,
            "model": self.catalog.model,
            "sku": self.catalog.sku,
            "inventory_kind": CatalogItem.InventoryKind.EQUIPMENT,
            "unit_of_measure": CatalogItem.UnitOfMeasure.PCS,
            "unit_price": "63000.00",
            "price_needs_review": "on",
            "notes": "",
        })
        self.assertEqual(response.status_code, 302)
        self.catalog.refresh_from_db()
        self.assertEqual(self.catalog.unit_price, Decimal("63000.00"))
        self.assertFalse(self.catalog.price_needs_review)
        self.assertTrue(CatalogPriceHistory.objects.filter(catalog_item=self.catalog, unit_price=Decimal("63000.00")).exists())


    def test_new_purchase_price_does_not_silently_override_shared_accounting_price(self):
        same = ensure_catalog_item(
            category=self.category,
            accounting_group=Equipment.AccountingGroup.EMPLOYEE,
            name=self.catalog.name,
            manufacturer=self.catalog.manufacturer,
            model=self.catalog.model,
            unit_price=Decimal("65000.00"),
            source="Новая закупка",
            effective_date=date(2026, 8, 26),
            changed_by=self.staff,
        )
        self.assertEqual(same.pk, self.catalog.pk)
        same.refresh_from_db()
        self.assertEqual(same.unit_price, Decimal("62499.00"))
        self.assertTrue(same.price_needs_review)
        self.assertTrue(
            CatalogPriceHistory.objects.filter(
                catalog_item=same,
                unit_price=Decimal("65000.00"),
                source="Новая закупка",
            ).exists()
        )

    def test_act_snapshot_does_not_change_when_catalog_price_changes(self):
        item = self.equipment("R160-N160-003", "SN160-3")
        act = Act.objects.create(
            act_type=Act.ActType.ISSUE,
            act_date=date(2026, 8, 26),
            employee=self.employee,
            from_organization=self.owner,
        )
        act.equipment.add(item)
        snapshot_act_items(act, [item])
        snapshot = act.items.get(equipment=item)
        self.assertEqual(snapshot.unit_price, Decimal("62499.00"))
        self.assertEqual(snapshot.line_total, Decimal("62499.00"))

        self.catalog.unit_price = Decimal("70000.00")
        self.catalog.save()
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.unit_price, Decimal("62499.00"))
        self.assertEqual(snapshot.line_total, Decimal("62499.00"))


    def test_snapshotted_act_locks_equipment_field_on_edit(self):
        item = self.equipment("R160-N160-005", "SN160-5")
        act = Act.objects.create(
            act_type=Act.ActType.ISSUE,
            act_date=date(2026, 8, 26),
            employee=self.employee,
            from_organization=self.owner,
        )
        act.equipment.add(item)
        snapshot_act_items(act, [item])
        form = ActForm(instance=act)
        self.assertTrue(form.fields["equipment"].disabled)

    def test_return_reuses_issue_snapshot_price(self):
        item = self.equipment("R160-N160-004", "SN160-4")
        issue = Act.objects.create(
            act_type=Act.ActType.ISSUE,
            act_date=date(2026, 8, 1),
            employee=self.employee,
            from_organization=self.owner,
        )
        issue.equipment.add(item)
        snapshot_act_items(issue, [item])

        self.catalog.unit_price = Decimal("70000.00")
        self.catalog.save()
        overrides = return_price_overrides(self.employee, [item], date(2026, 8, 26))
        self.assertEqual(overrides[item.pk], Decimal("62499.00"))
