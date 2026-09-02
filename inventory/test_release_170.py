from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from inventory.models import (
    CatalogItem, Category, Equipment, EquipmentMovement, Location, MaterialStock,
    MaterialTransaction, Organization, Project, ProjectOperationLine, ProjectStage, Warehouse,
)


class Release170Tests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="admin170", password="pass", is_staff=True)
        self.client.force_login(self.user)
        self.org = Organization.objects.create(name="ООО Тест", prefix="T17")
        self.location = Location.objects.create(organization=self.org, label="Стройка 1.1", address="Ростов-на-Дону")
        self.category = Category.objects.create(name="Тест 1.7", code="T17", tracking_mode=Category.TrackingMode.UNIT)
        self.warehouse = Warehouse.objects.create(organization=self.org, name="Основной склад", is_default=True)

    def _catalog(self, name, kind, price, unit="pcs", model=""):
        return CatalogItem.objects.create(
            category=self.category,
            accounting_group="technical",
            name=name,
            manufacturer="FOX Test",
            model=model or name,
            inventory_kind=kind,
            unit_of_measure=unit,
            unit_price=Decimal(price),
        )

    def test_organization_workspace_opens(self):
        response = self.client.get(reverse("organization_detail", args=[self.org.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Объекты")
        self.assertContains(response, "Проекты")
        self.assertContains(response, "Склад")

    def test_material_receipt_increases_stock_and_writes_history(self):
        cable = self._catalog("Кабель UTP", CatalogItem.InventoryKind.MATERIAL, "31.00", unit="m")
        response = self.client.post(reverse("material_receipt"), {
            "organization": self.org.pk,
            "warehouse": self.warehouse.pk,
            "catalog_item": cable.pk,
            "quantity": "500",
            "unit_price": "32.50",
            "source": "Счёт №1",
            "update_catalog_price": "on",
            "note": "Поставка",
        })
        self.assertEqual(response.status_code, 302)
        stock = MaterialStock.objects.get(warehouse=self.warehouse, catalog_item=cable)
        self.assertEqual(stock.quantity, Decimal("500.000"))
        tx = MaterialTransaction.objects.get(catalog_item=cable)
        self.assertEqual(tx.transaction_type, MaterialTransaction.TransactionType.RECEIPT)
        self.assertEqual(tx.balance_after, Decimal("500.000"))
        cable.refresh_from_db()
        self.assertEqual(cable.unit_price, Decimal("32.50"))
        self.assertTrue(cable.price_history.filter(unit_price=Decimal("32.50")).exists())

    def test_stage_operation_writes_off_material_and_installs_equipment(self):
        cable = self._catalog("Гофра 20 мм", CatalogItem.InventoryKind.MATERIAL, "25.00", unit="m")
        stock = MaterialStock.objects.create(warehouse=self.warehouse, catalog_item=cable, quantity=Decimal("300"))
        camera_catalog = self._catalog("IP-камера", CatalogItem.InventoryKind.EQUIPMENT, "22560.00", model="TR-D2181IR3")
        camera = Equipment.objects.create(
            catalog_item=camera_catalog,
            category=self.category,
            accounting_group=Equipment.AccountingGroup.TECHNICAL,
            name="IP-камера",
            manufacturer="FOX Test",
            model="TR-D2181IR3",
            owner=self.org,
            usage_status=Equipment.UsageStatus.STOCK,
            condition=Equipment.Condition.NEW,
        )
        project = Project.objects.create(
            organization=self.org, location=self.location, name="Видеонаблюдение", status=Project.Status.DRAFT, created_by=self.user
        )
        stage = ProjectStage.objects.create(project=project, number=1, name="Наружные камеры")
        response = self.client.post(reverse("project_stage_add_items", args=[stage.pk]), {
            "operation_date": "2026-09-02",
            f"material_{stock.pk}": "180",
            "equipment": [str(camera.pk)],
            "freeform_location": "Фасад",
            "note": "Первый монтаж",
        })
        self.assertEqual(response.status_code, 302)
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal("120.000"))
        camera.refresh_from_db()
        self.assertEqual(camera.location_id, self.location.pk)
        self.assertEqual(camera.usage_status, Equipment.UsageStatus.OBJECT)
        self.assertEqual(camera.origin_project_id, project.pk)
        self.assertEqual(camera.origin_project_stage_id, stage.pk)
        self.assertTrue(camera.movements.filter(movement_type=EquipmentMovement.MovementType.PROJECT_INSTALLED, project_stage=stage).exists())
        lines = ProjectOperationLine.objects.filter(operation__stage=stage)
        self.assertEqual(lines.count(), 2)
        material_line = lines.get(line_type=ProjectOperationLine.LineType.MATERIAL)
        self.assertEqual(material_line.unit_price_snapshot, Decimal("25.00"))
        self.assertEqual(material_line.line_total_snapshot, Decimal("4500.00"))
        self.assertTrue(MaterialTransaction.objects.filter(project_line=material_line, balance_after=Decimal("120.000")).exists())

    def test_staff_can_void_project_operation_and_restore_stock(self):
        cable = self._catalog("Кабель", CatalogItem.InventoryKind.MATERIAL, "10.00", unit="m")
        stock = MaterialStock.objects.create(warehouse=self.warehouse, catalog_item=cable, quantity=Decimal("100"))
        project = Project.objects.create(organization=self.org, location=self.location, name="Монтаж")
        stage = ProjectStage.objects.create(project=project, number=1, name="Этап")
        response = self.client.post(reverse("project_stage_add_items", args=[stage.pk]), {
            "operation_date": "2026-09-02",
            f"material_{stock.pk}": "30",
        })
        self.assertEqual(response.status_code, 302)
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal("70.000"))
        op = stage.operations.get()
        response = self.client.post(reverse("project_operation_void", args=[op.pk]), {"reason": "Ошибка количества"})
        self.assertEqual(response.status_code, 302)
        stock.refresh_from_db(); op.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal("100.000"))
        self.assertIsNotNone(op.voided_at)
        self.assertEqual(op.total_cost, 0)
        self.assertTrue(MaterialTransaction.objects.filter(transaction_type=MaterialTransaction.TransactionType.ADJUSTMENT_PLUS, note="Ошибка количества").exists())

    def test_completed_stage_rejects_new_operation(self):
        project = Project.objects.create(organization=self.org, location=self.location, name="Проект")
        stage = ProjectStage.objects.create(project=project, number=1, name="Этап", status=ProjectStage.Status.COMPLETED)
        response = self.client.get(reverse("project_stage_add_items", args=[stage.pk]))
        self.assertEqual(response.status_code, 302)

    def test_void_restores_exact_equipment_state_before_project_installation(self):
        camera_catalog = self._catalog("IP-камера rollback", CatalogItem.InventoryKind.EQUIPMENT, "20000.00", model="CAM-RB")
        previous_project = Project.objects.create(
            organization=self.org, location=self.location, name="Предыдущий проект", status=Project.Status.COMPLETED
        )
        previous_stage = ProjectStage.objects.create(
            project=previous_project, number=1, name="Исторический этап", status=ProjectStage.Status.COMPLETED
        )
        camera = Equipment.objects.create(
            catalog_item=camera_catalog,
            category=self.category,
            accounting_group=Equipment.AccountingGroup.TECHNICAL,
            name="IP-камера rollback",
            manufacturer="FOX Test",
            model="CAM-RB",
            owner=self.org,
            usage_status=Equipment.UsageStatus.RESERVE,
            condition=Equipment.Condition.NEW,
            freeform_location="Резерв в сейфе",
            origin_project=previous_project,
            origin_project_stage=previous_stage,
        )
        project = Project.objects.create(organization=self.org, location=self.location, name="Новая установка")
        stage = ProjectStage.objects.create(project=project, number=1, name="Монтаж")
        response = self.client.post(reverse("project_stage_add_items", args=[stage.pk]), {
            "operation_date": "2026-09-02",
            "equipment": [str(camera.pk)],
            "freeform_location": "Фасад",
        })
        self.assertEqual(response.status_code, 302)
        line = ProjectOperationLine.objects.get(operation__stage=stage, equipment=camera)
        self.assertEqual(line.equipment_previous_state["usage_status"], Equipment.UsageStatus.RESERVE)
        self.assertEqual(line.equipment_previous_state["freeform_location"], "Резерв в сейфе")
        self.assertEqual(line.equipment_previous_state["origin_project_id"], previous_project.pk)

        response = self.client.post(reverse("project_operation_void", args=[line.operation_id]), {"reason": "Отмена монтажа"})
        self.assertEqual(response.status_code, 302)
        camera.refresh_from_db()
        self.assertEqual(camera.usage_status, Equipment.UsageStatus.RESERVE)
        self.assertEqual(camera.freeform_location, "Резерв в сейфе")
        self.assertIsNone(camera.location_id)
        self.assertEqual(camera.origin_project_id, previous_project.pk)
        self.assertEqual(camera.origin_project_stage_id, previous_stage.pk)

    def test_void_refuses_if_equipment_changed_after_installation(self):
        camera_catalog = self._catalog("IP-камера moved", CatalogItem.InventoryKind.EQUIPMENT, "21000.00", model="CAM-MV")
        camera = Equipment.objects.create(
            catalog_item=camera_catalog, category=self.category,
            accounting_group=Equipment.AccountingGroup.TECHNICAL,
            name="IP-камера moved", manufacturer="FOX Test", model="CAM-MV",
            owner=self.org, usage_status=Equipment.UsageStatus.STOCK, condition=Equipment.Condition.NEW,
        )
        project = Project.objects.create(organization=self.org, location=self.location, name="Монтаж камеры")
        stage = ProjectStage.objects.create(project=project, number=1, name="Монтаж")
        self.client.post(reverse("project_stage_add_items", args=[stage.pk]), {
            "operation_date": "2026-09-02", "equipment": [str(camera.pk)], "freeform_location": "Фасад",
        })
        op = stage.operations.get()
        camera.refresh_from_db()
        camera.freeform_location = "Фасад, сектор B"
        camera.save(update_fields=["freeform_location", "updated_at"])
        response = self.client.post(reverse("project_operation_void", args=[op.pk]), {"reason": "Проверка защиты"})
        self.assertEqual(response.status_code, 302)
        op.refresh_from_db(); camera.refresh_from_db()
        self.assertIsNone(op.voided_at)
        self.assertEqual(camera.usage_status, Equipment.UsageStatus.OBJECT)
        self.assertEqual(camera.freeform_location, "Фасад, сектор B")

    def test_completed_project_is_read_only_until_staff_reopens_it(self):
        project = Project.objects.create(
            organization=self.org, location=self.location, name="Закрытый проект", status=Project.Status.COMPLETED
        )
        stage = ProjectStage.objects.create(project=project, number=1, name="Закрытый этап", status=ProjectStage.Status.COMPLETED)
        response = self.client.get(reverse("project_edit", args=[project.pk]))
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse("project_stage_edit", args=[project.pk, stage.pk]))
        self.assertEqual(response.status_code, 302)

        response = self.client.post(reverse("project_reopen", args=[project.pk]))
        self.assertEqual(response.status_code, 302)
        project.refresh_from_db(); stage.refresh_from_db()
        self.assertEqual(project.status, Project.Status.ACTIVE)
        self.assertEqual(stage.status, ProjectStage.Status.COMPLETED)

        response = self.client.post(reverse("project_stage_reopen", args=[stage.pk]))
        self.assertEqual(response.status_code, 302)
        stage.refresh_from_db()
        self.assertEqual(stage.status, ProjectStage.Status.ACTIVE)

    def test_stock_and_reserve_are_not_counted_as_unplaced_attention(self):
        catalog = self._catalog("Запасная камера", CatalogItem.InventoryKind.EQUIPMENT, "12000.00", model="STOCK-CAM")
        Equipment.objects.create(
            catalog_item=catalog, category=self.category,
            accounting_group=Equipment.AccountingGroup.TECHNICAL,
            name="Запасная камера", manufacturer="FOX Test", model="STOCK-CAM",
            owner=self.org, usage_status=Equipment.UsageStatus.STOCK, condition=Equipment.Condition.NEW,
        )
        response = self.client.get(reverse("organization_detail", args=[self.org.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["attention"]["unplaced"], 0)

    def test_safe_legacy_stock_cards_can_be_converted_to_material_stock(self):
        catalog = self._catalog("Монтажная коробка", CatalogItem.InventoryKind.EQUIPMENT, "150.00", model="BOX-100")
        legacy = Equipment.objects.create(
            catalog_item=catalog, category=self.category,
            accounting_group=Equipment.AccountingGroup.TECHNICAL,
            name="Монтажная коробка", manufacturer="FOX Test", model="BOX-100",
            owner=self.org, usage_status=Equipment.UsageStatus.STOCK,
            condition=Equipment.Condition.NEW, quantity=7,
        )
        response = self.client.get(reverse("catalog_convert_to_material", args=[catalog.pk]))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("catalog_convert_to_material", args=[catalog.pk]), {
            "inventory_kind": CatalogItem.InventoryKind.CONSUMABLE,
            "unit_of_measure": CatalogItem.UnitOfMeasure.PCS,
            "confirm": "on",
        })
        self.assertEqual(response.status_code, 302)
        catalog.refresh_from_db(); legacy.refresh_from_db()
        self.assertEqual(catalog.inventory_kind, CatalogItem.InventoryKind.CONSUMABLE)
        self.assertTrue(legacy.archived)
        stock = MaterialStock.objects.get(warehouse=self.warehouse, catalog_item=catalog)
        self.assertEqual(stock.quantity, Decimal("7.000"))
        self.assertTrue(MaterialTransaction.objects.filter(
            catalog_item=catalog,
            transaction_type=MaterialTransaction.TransactionType.CONVERSION,
            quantity=Decimal("7.000"),
        ).exists())

    def test_conversion_is_blocked_for_real_individual_asset(self):
        catalog = self._catalog("Коммутатор", CatalogItem.InventoryKind.EQUIPMENT, "10000.00", model="SW-1")
        asset = Equipment.objects.create(
            catalog_item=catalog, category=self.category,
            accounting_group=Equipment.AccountingGroup.TECHNICAL,
            name="Коммутатор", manufacturer="FOX Test", model="SW-1",
            serial_number="SERIAL-1", owner=self.org,
            usage_status=Equipment.UsageStatus.STOCK, condition=Equipment.Condition.NEW,
        )
        response = self.client.post(reverse("catalog_convert_to_material", args=[catalog.pk]), {
            "inventory_kind": CatalogItem.InventoryKind.MATERIAL,
            "unit_of_measure": CatalogItem.UnitOfMeasure.PCS,
            "confirm": "on",
        })
        self.assertEqual(response.status_code, 200)
        catalog.refresh_from_db(); asset.refresh_from_db()
        self.assertEqual(catalog.inventory_kind, CatalogItem.InventoryKind.EQUIPMENT)
        self.assertFalse(asset.archived)
