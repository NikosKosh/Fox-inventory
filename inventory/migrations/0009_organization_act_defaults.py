from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0008_loginattempt"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="act_organization_name",
            field=models.CharField(
                blank=True,
                help_text="Если не заполнено, используется основное наименование организации.",
                max_length=255,
                verbose_name="Наименование организации в актах",
            ),
        ),
        migrations.AddField(
            model_name="organization",
            name="act_city",
            field=models.CharField(
                blank=True,
                help_text="Например: г. Ростов-на-Дону.",
                max_length=120,
                verbose_name="Город в актах",
            ),
        ),
        migrations.AddField(
            model_name="organization",
            name="act_issue_representative_position",
            field=models.CharField(blank=True, max_length=255, verbose_name="Должность представителя при выдаче"),
        ),
        migrations.AddField(
            model_name="organization",
            name="act_issue_representative_name",
            field=models.CharField(blank=True, max_length=255, verbose_name="ФИО представителя при выдаче"),
        ),
        migrations.AddField(
            model_name="organization",
            name="act_return_representative_position",
            field=models.CharField(blank=True, max_length=255, verbose_name="Должность представителя при возврате"),
        ),
        migrations.AddField(
            model_name="organization",
            name="act_return_representative_name",
            field=models.CharField(blank=True, max_length=255, verbose_name="ФИО представителя при возврате"),
        ),
    ]
