import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models
import inventory.models
import inventory.validators


def create_default_document_types(apps, schema_editor):
    DocumentType = apps.get_model("inventory", "DocumentType")
    defaults = [
        (10, "Договор", "contract"),
        (20, "Дополнительное соглашение", "addendum"),
        (30, "Счёт", "invoice"),
        (40, "УПД", "upd"),
        (50, "Акт", "service-act"),
        (60, "Накладная", "waybill"),
        (70, "Счёт-фактура", "invoice-facture"),
        (80, "Спецификация", "specification"),
        (90, "Письмо", "letter"),
        (100, "Прочее", "other"),
    ]
    for order, name, code in defaults:
        DocumentType.objects.get_or_create(code=code, defaults={"name": name, "sort_order": order})


def remove_default_document_types(apps, schema_editor):
    DocumentType = apps.get_model("inventory", "DocumentType")
    DocumentType.objects.filter(code__in=[
        "contract", "addendum", "invoice", "upd", "service-act", "waybill",
        "invoice-facture", "specification", "letter", "other",
    ]).delete()


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("inventory", "0009_organization_act_defaults"),
    ]

    operations = [
        migrations.CreateModel(
            name="Counterparty",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255, verbose_name="Наименование")),
                ("short_name", models.CharField(blank=True, max_length=150, verbose_name="Краткое наименование")),
                ("inn", models.CharField(blank=True, db_index=True, max_length=20, verbose_name="ИНН")),
                ("kpp", models.CharField(blank=True, max_length=20, verbose_name="КПП")),
                ("contact_name", models.CharField(blank=True, max_length=255, verbose_name="Контакт")),
                ("phone", models.CharField(blank=True, max_length=80, verbose_name="Телефон")),
                ("email", models.EmailField(blank=True, max_length=254, verbose_name="Email")),
                ("notes", models.TextField(blank=True, verbose_name="Комментарий")),
                ("archived", models.BooleanField(default=False, verbose_name="В архиве")),
            ],
            options={
                "verbose_name": "контрагент",
                "verbose_name_plural": "контрагенты",
                "ordering": ["name"],
                "indexes": [models.Index(fields=["name"], name="inventory_c_name_0cb663_idx")],
            },
        ),
        migrations.CreateModel(
            name="DocumentType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120, unique=True, verbose_name="Название")),
                ("code", models.SlugField(max_length=60, unique=True, verbose_name="Код")),
                ("sort_order", models.PositiveIntegerField(default=100, verbose_name="Порядок")),
                ("archived", models.BooleanField(default=False, verbose_name="В архиве")),
            ],
            options={
                "verbose_name": "тип документа",
                "verbose_name_plural": "типы документов",
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.CreateModel(
            name="Contract",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=255, verbose_name="Название")),
                ("number", models.CharField(blank=True, max_length=120, verbose_name="Номер договора")),
                ("contract_date", models.DateField(blank=True, null=True, verbose_name="Дата договора")),
                ("category", models.CharField(choices=[("services", "Услуги"), ("internet", "Связь / интернет"), ("rent", "Аренда"), ("software", "ПО / лицензии"), ("supply", "Поставка"), ("maintenance", "Обслуживание"), ("other", "Прочее")], default="other", max_length=30, verbose_name="Категория")),
                ("starts_at", models.DateField(blank=True, null=True, verbose_name="Начало действия")),
                ("ends_at", models.DateField(blank=True, null=True, verbose_name="Окончание действия")),
                ("indefinite", models.BooleanField(default=False, verbose_name="Бессрочный")),
                ("main_file", models.FileField(blank=True, upload_to=inventory.models.contract_file_upload_to, validators=[inventory.validators.validate_business_document], verbose_name="Файл договора")),
                ("notes", models.TextField(blank=True, verbose_name="Комментарий")),
                ("archived", models.BooleanField(db_index=True, default=False, verbose_name="В архиве")),
                ("counterparty", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="contracts", to="inventory.counterparty", verbose_name="Контрагент")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_contracts", to=settings.AUTH_USER_MODEL, verbose_name="Создал")),
                ("location", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="contracts", to="inventory.location", verbose_name="Объект")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="contracts", to="inventory.organization", verbose_name="Организация")),
                ("responsible_employee", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="contracts", to="inventory.employee", verbose_name="Ответственный")),
            ],
            options={
                "verbose_name": "договор",
                "verbose_name_plural": "договоры",
                "ordering": ["archived", "-contract_date", "counterparty__name", "title"],
                "indexes": [
                    models.Index(fields=["organization", "archived"], name="inventory_c_organiz_6da538_idx"),
                    models.Index(fields=["counterparty", "archived"], name="inventory_c_counter_f94ef6_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="DocumentRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(blank=True, max_length=255, verbose_name="Название")),
                ("number", models.CharField(blank=True, max_length=120, verbose_name="Номер")),
                ("document_date", models.DateField(blank=True, db_index=True, null=True, verbose_name="Дата документа")),
                ("amount", models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True, verbose_name="Сумма")),
                ("file", models.FileField(upload_to=inventory.models.document_file_upload_to, validators=[inventory.validators.validate_business_document], verbose_name="Файл")),
                ("original_name", models.CharField(blank=True, max_length=255, verbose_name="Исходное имя файла")),
                ("notes", models.TextField(blank=True, verbose_name="Комментарий")),
                ("trashed_at", models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="В корзине с")),
                ("contract", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="documents", to="inventory.contract", verbose_name="Договор")),
                ("counterparty", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="documents", to="inventory.counterparty", verbose_name="Контрагент")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="uploaded_documents", to=settings.AUTH_USER_MODEL, verbose_name="Загрузил")),
                ("document_type", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="documents", to="inventory.documenttype", verbose_name="Тип документа")),
                ("equipment", models.ManyToManyField(blank=True, related_name="document_records", to="inventory.equipment", verbose_name="Оборудование")),
                ("location", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="document_records", to="inventory.location", verbose_name="Объект")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="document_records", to="inventory.organization", verbose_name="Организация")),
            ],
            options={
                "verbose_name": "документ",
                "verbose_name_plural": "документы",
                "ordering": ["-document_date", "-created_at", "-pk"],
                "indexes": [
                    models.Index(fields=["organization", "trashed_at"], name="inventory_d_organiz_21a46e_idx"),
                    models.Index(fields=["counterparty", "trashed_at"], name="inventory_d_counter_9f6952_idx"),
                    models.Index(fields=["contract", "trashed_at"], name="inv_doc_contract_trash_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="Reminder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=255, verbose_name="Напоминание")),
                ("next_due_date", models.DateField(db_index=True, default=django.utils.timezone.localdate, verbose_name="Дата")),
                ("remind_days_before", models.PositiveSmallIntegerField(default=0, verbose_name="Напомнить заранее, дней")),
                ("recurrence", models.CharField(choices=[("once", "Однократно"), ("monthly", "Ежемесячно"), ("yearly", "Ежегодно"), ("interval", "Через заданное число дней")], default="once", max_length=20, verbose_name="Повтор")),
                ("interval_days", models.PositiveIntegerField(blank=True, null=True, verbose_name="Интервал, дней")),
                ("amount", models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True, verbose_name="Сумма")),
                ("notes", models.TextField(blank=True, verbose_name="Комментарий")),
                ("snoozed_until", models.DateField(blank=True, null=True, verbose_name="Отложено до")),
                ("active", models.BooleanField(db_index=True, default=True, verbose_name="Активно")),
                ("last_completed_at", models.DateTimeField(blank=True, null=True, verbose_name="Последнее выполнение")),
                ("contract", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reminders", to="inventory.contract", verbose_name="Договор")),
                ("counterparty", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reminders", to="inventory.counterparty", verbose_name="Контрагент")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_reminders", to=settings.AUTH_USER_MODEL, verbose_name="Создал")),
                ("location", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reminders", to="inventory.location", verbose_name="Объект")),
                ("organization", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reminders", to="inventory.organization", verbose_name="Организация")),
            ],
            options={
                "verbose_name": "напоминание",
                "verbose_name_plural": "напоминания",
                "ordering": ["next_due_date", "pk"],
                "indexes": [models.Index(fields=["active", "next_due_date"], name="inventory_r_active_7541a8_idx")],
            },
        ),
        migrations.RunPython(create_default_document_types, remove_default_document_types),
    ]
