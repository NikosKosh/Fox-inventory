from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


RECURRING_CATEGORIES = {"internet", "services", "maintenance", "rent", "software"}
TRANSACTIONAL_TYPES = {"invoice", "upd", "service-act", "waybill", "invoice-facture"}


def group_existing_documents(apps, schema_editor):
    DocumentRecord = apps.get_model("inventory", "DocumentRecord")
    DocumentOperation = apps.get_model("inventory", "DocumentOperation")

    documents = (
        DocumentRecord.objects
        .filter(
            operation__isnull=True,
            trashed_at__isnull=True,
            document_date__isnull=False,
            document_type__code__in=TRANSACTIONAL_TYPES,
        )
        .select_related("contract", "document_type")
        .order_by("organization_id", "counterparty_id", "contract_id", "document_date", "pk")
    )

    groups = {}
    for document in documents:
        contract = document.contract
        recurring = bool(contract and contract.category in RECURRING_CATEGORIES)
        if recurring:
            bucket = document.document_date.replace(day=1)
            title = f"Расчёты за {bucket:%m.%Y}"
            if contract and contract.title:
                title += f" — {contract.title}"
        else:
            bucket = document.document_date
            if contract and contract.category == "supply":
                title = f"Поставка от {bucket:%d.%m.%Y}"
            elif contract and contract.title:
                title = f"Операция от {bucket:%d.%m.%Y} — {contract.title}"
            else:
                title = f"Операция от {bucket:%d.%m.%Y}"

        key = (
            document.organization_id,
            document.counterparty_id,
            document.contract_id,
            document.location_id,
            bucket,
            title,
        )
        operation = groups.get(key)
        if operation is None:
            operation = DocumentOperation.objects.create(
                organization_id=document.organization_id,
                counterparty_id=document.counterparty_id,
                contract_id=document.contract_id,
                location_id=document.location_id,
                title=title,
                operation_date=bucket,
                created_by_id=document.created_by_id,
            )
            groups[key] = operation

        document.operation_id = operation.pk
        document.save(update_fields=["operation"])


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("inventory", "0011_document_organization_workspace"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentOperation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=255, verbose_name="Название операции")),
                ("operation_date", models.DateField(blank=True, db_index=True, null=True, verbose_name="Дата операции")),
                ("amount", models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True, verbose_name="Сумма")),
                ("notes", models.TextField(blank=True, verbose_name="Комментарий")),
                ("counterparty", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="document_operations", to="inventory.counterparty", verbose_name="Контрагент")),
                ("contract", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="operations", to="inventory.contract", verbose_name="Договор")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_document_operations", to=settings.AUTH_USER_MODEL, verbose_name="Создал")),
                ("location", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="document_operations", to="inventory.location", verbose_name="Объект")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="document_operations", to="inventory.organization", verbose_name="Организация")),
            ],
            options={
                "verbose_name": "операция документов",
                "verbose_name_plural": "операции документов",
                "ordering": ["-operation_date", "-created_at", "-pk"],
            },
        ),
        migrations.AddIndex(
            model_name="documentoperation",
            index=models.Index(fields=["organization", "operation_date"], name="inv_op_org_date_idx"),
        ),
        migrations.AddIndex(
            model_name="documentoperation",
            index=models.Index(fields=["contract", "operation_date"], name="inv_op_contract_date_idx"),
        ),
        migrations.AddField(
            model_name="documentrecord",
            name="operation",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="documents", to="inventory.documentoperation", verbose_name="Операция"),
        ),
        migrations.AddIndex(
            model_name="documentrecord",
            index=models.Index(fields=["operation", "trashed_at"], name="inv_doc_operation_trash_idx"),
        ),
        migrations.RunPython(group_existing_documents, migrations.RunPython.noop),
    ]
