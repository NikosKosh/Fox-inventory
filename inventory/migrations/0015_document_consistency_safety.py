from pathlib import Path

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_consistency_fields(apps, schema_editor):
    Contract = apps.get_model("inventory", "Contract")
    DocumentRecord = apps.get_model("inventory", "DocumentRecord")

    for contract in Contract.objects.exclude(main_file="").iterator():
        if not contract.main_file_original_name:
            contract.main_file_original_name = Path(contract.main_file.name).name
            contract.save(update_fields=["main_file_original_name"])

    DocumentRecord.objects.filter(
        classification_source=""
    ).update(classification_source="manual")


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("inventory", "0014_organization_counterparty_links"),
    ]

    operations = [
        migrations.AddField(
            model_name="contract",
            name="main_file_original_name",
            field=models.CharField(
                blank=True,
                max_length=255,
                verbose_name="Исходное имя файла договора",
            ),
        ),
        migrations.AddField(
            model_name="documentrecord",
            name="classification_source",
            field=models.CharField(
                choices=[
                    ("manual", "Указано вручную"),
                    ("filename", "Определено по имени файла"),
                    ("", "Не определено"),
                ],
                default="manual",
                max_length=20,
                verbose_name="Источник типа",
            ),
        ),
        migrations.AddField(
            model_name="documentrecord",
            name="file_sha256",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=64,
                verbose_name="SHA-256 файла",
            ),
        ),
        migrations.CreateModel(
            name="DocumentFileVersion",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "file",
                    models.FileField(
                        upload_to="documents/versions/%Y/%m/",
                        verbose_name="Файл версии",
                    ),
                ),
                (
                    "original_name",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        verbose_name="Исходное имя",
                    ),
                ),
                (
                    "file_sha256",
                    models.CharField(
                        blank=True,
                        max_length=64,
                        verbose_name="SHA-256",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_document_versions",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Сохранил версию",
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="file_versions",
                        to="inventory.documentrecord",
                        verbose_name="Документ",
                    ),
                ),
            ],
            options={
                "verbose_name": "версия файла документа",
                "verbose_name_plural": "версии файлов документов",
                "ordering": ["-created_at", "-pk"],
            },
        ),
        migrations.CreateModel(
            name="DocumentActivity",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("action", models.CharField(max_length=40, verbose_name="Действие")),
                (
                    "message",
                    models.CharField(
                        blank=True,
                        max_length=500,
                        verbose_name="Описание",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="document_activity",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Пользователь",
                    ),
                ),
                (
                    "contract",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="document_activity",
                        to="inventory.contract",
                        verbose_name="Договор",
                    ),
                ),
                (
                    "counterparty",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="document_activity",
                        to="inventory.counterparty",
                        verbose_name="Вторая сторона",
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="activity",
                        to="inventory.documentrecord",
                        verbose_name="Документ",
                    ),
                ),
                (
                    "operation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="activity",
                        to="inventory.documentoperation",
                        verbose_name="Пакет",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="document_activity",
                        to="inventory.organization",
                        verbose_name="Организация",
                    ),
                ),
            ],
            options={
                "verbose_name": "событие документооборота",
                "verbose_name_plural": "события документооборота",
                "ordering": ["-created_at", "-pk"],
            },
        ),
        migrations.AddIndex(
            model_name="documentactivity",
            index=models.Index(
                fields=["document", "created_at"],
                name="inv_activity_doc_time_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="documentactivity",
            index=models.Index(
                fields=["operation", "created_at"],
                name="inv_activity_op_time_idx",
            ),
        ),
        migrations.RunPython(
            backfill_consistency_fields,
            migrations.RunPython.noop,
        ),
    ]
