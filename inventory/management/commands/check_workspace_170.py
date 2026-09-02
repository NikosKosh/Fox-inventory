from django.core.management.base import BaseCommand, CommandError
from django.db import models
from django.db.models import Q
from inventory.models import CatalogItem, Equipment, MaterialStock, Organization, Project, ProjectOperation, ProjectOperationLine, Warehouse


class Command(BaseCommand):
    help = "Проверяет целостность Organization Workspace / Projects / Materials после обновления 1.7.0."

    def handle(self, *args, **options):
        organizations = Organization.objects.count()
        warehouses = Warehouse.objects.count()
        missing_default = Organization.objects.exclude(warehouses__is_default=True).count()
        equipment = Equipment.objects.count()
        unlinked_catalog = Equipment.objects.filter(catalog_item__isnull=True).count()
        material_catalog = CatalogItem.objects.filter(
            inventory_kind__in=[CatalogItem.InventoryKind.MATERIAL, CatalogItem.InventoryKind.CONSUMABLE, CatalogItem.InventoryKind.COMPONENT]
        ).count()
        invalid_material_stocks = MaterialStock.objects.filter(catalog_item__inventory_kind=CatalogItem.InventoryKind.EQUIPMENT).count()
        active_equipment_on_material_catalog = Equipment.objects.filter(archived=False).exclude(
            usage_status=Equipment.UsageStatus.DISPOSED
        ).exclude(catalog_item__inventory_kind=CatalogItem.InventoryKind.EQUIPMENT).count()
        negative_stock = MaterialStock.objects.filter(quantity__lt=0).count()
        invalid_project_org = Project.objects.exclude(location__organization_id=models.F("organization_id")).count()
        invalid_project_responsible = Project.objects.filter(responsible_employee__isnull=False).exclude(
            responsible_employee__organization_id=models.F("organization_id")
        ).count()
        invalid_origin_stage = Equipment.objects.filter(origin_project_stage__isnull=False).filter(
            Q(origin_project__isnull=True) | ~Q(origin_project_stage__project_id=models.F("origin_project_id"))
        ).count()
        equipment_lines_without_state = ProjectOperationLine.objects.filter(
            line_type=ProjectOperationLine.LineType.EQUIPMENT, operation__voided_at__isnull=True,
            equipment_previous_state={},
        ).count()

        self.stdout.write(f"Organizations: {organizations}")
        self.stdout.write(f"Warehouses: {warehouses}")
        self.stdout.write(f"Organizations without default warehouse: {missing_default}")
        self.stdout.write(f"Equipment: {equipment}")
        self.stdout.write(f"Equipment without catalog: {unlinked_catalog}")
        self.stdout.write(f"Material catalog items: {material_catalog}")
        self.stdout.write(f"Material stock rows: {MaterialStock.objects.count()}")
        self.stdout.write(f"Invalid material stocks: {invalid_material_stocks}")
        self.stdout.write(f"Active individual assets attached to material catalog: {active_equipment_on_material_catalog}")
        self.stdout.write(f"Negative stocks: {negative_stock}")
        self.stdout.write(f"Projects: {Project.objects.count()}")
        self.stdout.write(f"Project operations: {ProjectOperation.objects.count()}")
        self.stdout.write(f"Projects with organization/object mismatch: {invalid_project_org}")
        self.stdout.write(f"Projects with responsible employee mismatch: {invalid_project_responsible}")
        self.stdout.write(f"Equipment with inconsistent project origin: {invalid_origin_stage}")
        self.stdout.write(f"Active equipment project lines without rollback snapshot: {equipment_lines_without_state}")

        errors = []
        if missing_default:
            errors.append("есть организации без основного склада")
        if unlinked_catalog:
            errors.append("есть оборудование без номенклатуры")
        if invalid_material_stocks:
            errors.append("есть остатки у номенклатуры типа «оборудование»")
        if active_equipment_on_material_catalog:
            errors.append("есть действующие индивидуальные активы, привязанные к материальной номенклатуре")
        if negative_stock:
            errors.append("обнаружен отрицательный остаток")
        if invalid_project_org:
            errors.append("есть проекты, привязанные к объекту другой организации")
        if invalid_project_responsible:
            errors.append("есть проекты с ответственным из другой организации")
        if invalid_origin_stage:
            errors.append("есть оборудование с несогласованными проектом и этапом происхождения")
        if equipment_lines_without_state:
            errors.append("есть активные проектные установки оборудования без снимка состояния для безопасного отката")
        if errors:
            raise CommandError("; ".join(errors))
        self.stdout.write(self.style.SUCCESS("WORKSPACE 1.7.0: OK"))
