from decimal import Decimal
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
from django.core.validators import MinValueValidator


def create_default_warehouses(apps, schema_editor):
    Organization = apps.get_model("inventory", "Organization")
    Warehouse = apps.get_model("inventory", "Warehouse")
    for organization in Organization.objects.all().iterator():
        Warehouse.objects.get_or_create(
            organization=organization,
            name="Основной склад",
            defaults={"is_default": True},
        )


def reverse_default_warehouses(apps, schema_editor):
    Warehouse = apps.get_model("inventory", "Warehouse")
    Warehouse.objects.filter(name="Основной склад", is_default=True, stocks__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("inventory", "0017_catalog_pricing"),
    ]

    operations = [
        migrations.AddField(
            model_name="catalogitem",
            name="inventory_kind",
            field=models.CharField(
                choices=[
                    ("equipment", "Инвентарное оборудование"),
                    ("material", "Материал"),
                    ("consumable", "Расходник"),
                    ("component", "Запчасть / компонент"),
                ],
                db_index=True,
                default="equipment",
                max_length=20,
                verbose_name="Тип учёта",
            ),
        ),
        migrations.AddField(
            model_name="catalogitem",
            name="unit_of_measure",
            field=models.CharField(
                choices=[
                    ("pcs", "шт."), ("m", "м"), ("kg", "кг"), ("l", "л"),
                    ("set", "компл."), ("pack", "уп."), ("roll", "рулон"),
                ],
                default="pcs",
                max_length=12,
                verbose_name="Единица измерения",
            ),
        ),
        migrations.AlterModelOptions(
            name="location",
            options={"ordering": ["organization__name", "address"], "verbose_name": "объект", "verbose_name_plural": "объекты"},
        ),
        migrations.AddField(
            model_name="location",
            name="responsible_employee",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="managed_locations", to="inventory.employee", verbose_name="Ответственный за объект",
            ),
        ),
        migrations.CreateModel(
            name="Warehouse",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(default="Основной склад", max_length=180, verbose_name="Название склада")),
                ("is_default", models.BooleanField(default=False, verbose_name="Основной склад")),
                ("archived", models.BooleanField(default=False, verbose_name="В архиве")),
                ("notes", models.TextField(blank=True, verbose_name="Комментарий")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="warehouses", to="inventory.organization", verbose_name="Организация")),
            ],
            options={"verbose_name": "склад", "verbose_name_plural": "склады", "ordering": ["organization__name", "name"]},
        ),
        migrations.AddConstraint(
            model_name="warehouse",
            constraint=models.UniqueConstraint(fields=("organization", "name"), name="uniq_org_warehouse_name"),
        ),
        migrations.AddConstraint(
            model_name="warehouse",
            constraint=models.UniqueConstraint(condition=models.Q(("is_default", True)), fields=("organization",), name="uniq_default_warehouse_per_org"),
        ),
        migrations.CreateModel(
            name="Project",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255, verbose_name="Название проекта")),
                ("code", models.CharField(blank=True, max_length=80, verbose_name="Код / номер")),
                ("project_type", models.CharField(choices=[("installation", "Монтаж"), ("modernization", "Модернизация"), ("repair", "Ремонт"), ("expansion", "Расширение"), ("dismantling", "Демонтаж"), ("other", "Другое")], default="installation", max_length=30, verbose_name="Тип проекта")),
                ("status", models.CharField(choices=[("draft", "Черновик"), ("active", "В работе"), ("completed", "Завершён"), ("archived", "Архив")], db_index=True, default="draft", max_length=20, verbose_name="Статус")),
                ("start_date", models.DateField(blank=True, null=True, verbose_name="Дата начала")),
                ("end_date", models.DateField(blank=True, null=True, verbose_name="Дата завершения")),
                ("description", models.TextField(blank=True, verbose_name="Описание")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="inventory_projects_created", to=settings.AUTH_USER_MODEL, verbose_name="Создал")),
                ("location", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="projects", to="inventory.location", verbose_name="Объект")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="projects", to="inventory.organization", verbose_name="Организация")),
                ("responsible_employee", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="responsible_projects", to="inventory.employee", verbose_name="Ответственный")),
            ],
            options={"verbose_name": "проект", "verbose_name_plural": "проекты", "ordering": ["-created_at", "name"]},
        ),
        migrations.AddIndex(model_name="project", index=models.Index(fields=["organization", "status"], name="project_org_status_idx")),
        migrations.AddIndex(model_name="project", index=models.Index(fields=["location", "status"], name="project_location_status_idx")),
        migrations.CreateModel(
            name="ProjectStage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("number", models.PositiveIntegerField(default=1, verbose_name="Этап №")),
                ("name", models.CharField(max_length=255, verbose_name="Название этапа")),
                ("status", models.CharField(choices=[("draft", "Черновик"), ("active", "В работе"), ("completed", "Завершён")], db_index=True, default="draft", max_length=20, verbose_name="Статус")),
                ("start_date", models.DateField(blank=True, null=True, verbose_name="Дата начала")),
                ("completed_at", models.DateField(blank=True, null=True, verbose_name="Дата завершения")),
                ("notes", models.TextField(blank=True, verbose_name="Описание / комментарий")),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="stages", to="inventory.project", verbose_name="Проект")),
            ],
            options={"verbose_name": "этап проекта", "verbose_name_plural": "этапы проекта", "ordering": ["number", "pk"]},
        ),
        migrations.AddConstraint(model_name="projectstage", constraint=models.UniqueConstraint(fields=("project", "number"), name="uniq_project_stage_number")),
        migrations.CreateModel(
            name="ProjectOperation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("operation_date", models.DateField(default=django.utils.timezone.localdate, verbose_name="Дата")),
                ("note", models.TextField(blank=True, verbose_name="Комментарий")),
                ("voided_at", models.DateTimeField(blank=True, null=True, verbose_name="Отменена")),
                ("void_reason", models.TextField(blank=True, verbose_name="Причина отмены")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="project_operations", to=settings.AUTH_USER_MODEL, verbose_name="Провёл")),
                ("voided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="project_operations_voided", to=settings.AUTH_USER_MODEL, verbose_name="Отменил")),
                ("stage", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="operations", to="inventory.projectstage", verbose_name="Этап")),
            ],
            options={"verbose_name": "операция проекта", "verbose_name_plural": "операции проекта", "ordering": ["-operation_date", "-created_at", "-pk"]},
        ),
        migrations.CreateModel(
            name="MaterialStock",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("quantity", models.DecimalField(decimal_places=3, default=0, max_digits=16, validators=[MinValueValidator(0)], verbose_name="Остаток")),
                ("catalog_item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="material_stocks", to="inventory.catalogitem", verbose_name="Номенклатура")),
                ("warehouse", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="stocks", to="inventory.warehouse", verbose_name="Склад")),
            ],
            options={"verbose_name": "остаток материала", "verbose_name_plural": "остатки материалов", "ordering": ["warehouse__organization__name", "catalog_item__name"]},
        ),
        migrations.AddConstraint(model_name="materialstock", constraint=models.UniqueConstraint(fields=("warehouse", "catalog_item"), name="uniq_warehouse_catalog_stock")),
        migrations.CreateModel(
            name="ProjectOperationLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("line_type", models.CharField(choices=[("material", "Материал / расходник"), ("equipment", "Оборудование")], max_length=20, verbose_name="Тип строки")),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=16, validators=[MinValueValidator(Decimal("0.001"))], verbose_name="Количество")),
                ("item_name_snapshot", models.CharField(max_length=255, verbose_name="Наименование на момент операции")),
                ("unit_snapshot", models.CharField(blank=True, max_length=20, verbose_name="Единица")),
                ("unit_price_snapshot", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True, validators=[MinValueValidator(0)], verbose_name="Цена на момент операции")),
                ("line_total_snapshot", models.DecimalField(blank=True, decimal_places=2, max_digits=16, null=True, validators=[MinValueValidator(0)], verbose_name="Сумма")),
                ("equipment_previous_state", models.JSONField(blank=True, default=dict, verbose_name="Состояние оборудования до установки")),
                ("equipment_installed_state", models.JSONField(blank=True, default=dict, verbose_name="Состояние оборудования после установки")),
                ("catalog_item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="project_lines", to="inventory.catalogitem", verbose_name="Номенклатура")),
                ("equipment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="project_lines", to="inventory.equipment", verbose_name="Экземпляр оборудования")),
                ("operation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lines", to="inventory.projectoperation", verbose_name="Операция")),
                ("warehouse", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="project_lines", to="inventory.warehouse", verbose_name="Склад списания")),
            ],
            options={"verbose_name": "строка операции проекта", "verbose_name_plural": "строки операций проекта", "ordering": ["pk"]},
        ),
        migrations.CreateModel(
            name="MaterialTransaction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("transaction_type", models.CharField(choices=[("receipt", "Поступление"), ("project_write_off", "Списание в проект"), ("conversion", "Преобразование из индивидуального учёта"), ("adjustment_plus", "Корректировка +"), ("adjustment_minus", "Корректировка −")], db_index=True, max_length=30, verbose_name="Операция")),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=16, validators=[MinValueValidator(Decimal("0.001"))], verbose_name="Количество")),
                ("balance_after", models.DecimalField(decimal_places=3, max_digits=16, validators=[MinValueValidator(0)], verbose_name="Остаток после операции")),
                ("unit_price_snapshot", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True, validators=[MinValueValidator(0)], verbose_name="Цена")),
                ("line_total_snapshot", models.DecimalField(blank=True, decimal_places=2, max_digits=16, null=True, validators=[MinValueValidator(0)], verbose_name="Сумма")),
                ("source", models.CharField(blank=True, max_length=255, verbose_name="Источник / документ")),
                ("note", models.TextField(blank=True, verbose_name="Комментарий")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("catalog_item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="material_transactions", to="inventory.catalogitem", verbose_name="Номенклатура")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="material_transactions", to=settings.AUTH_USER_MODEL, verbose_name="Пользователь")),
                ("project_line", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="material_transaction", to="inventory.projectoperationline", verbose_name="Строка проекта")),
                ("warehouse", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="transactions", to="inventory.warehouse", verbose_name="Склад")),
            ],
            options={"verbose_name": "движение материала", "verbose_name_plural": "движения материалов", "ordering": ["-created_at", "-pk"]},
        ),
        migrations.AddField(
            model_name="equipment",
            name="origin_project",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="origin_equipment", to="inventory.project", verbose_name="Проект происхождения"),
        ),
        migrations.AddField(
            model_name="equipment",
            name="origin_project_stage",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="origin_equipment", to="inventory.projectstage", verbose_name="Этап происхождения"),
        ),
        migrations.AddField(
            model_name="equipmentmovement",
            name="project_stage",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="equipment_movements", to="inventory.projectstage", verbose_name="Этап проекта"),
        ),
        migrations.AlterField(
            model_name="equipmentmovement",
            name="movement_type",
            field=models.CharField(choices=[("created", "Создание карточки"), ("edited", "Изменение карточки"), ("assigned", "Выдача сотруднику"), ("returned", "Возврат"), ("installed", "Установка на объекте"), ("loaned", "Передача другой организации"), ("loan_return", "Возврат владельцу"), ("repair", "Передача в ремонт"), ("disposed", "Списание"), ("act", "Операция по акту"), ("import", "Импорт"), ("project_installed", "Установка по проекту"), ("project_rollback", "Отмена проектной установки")], max_length=30, verbose_name="Тип операции"),
        ),
        migrations.RunPython(create_default_warehouses, reverse_default_warehouses),
    ]
