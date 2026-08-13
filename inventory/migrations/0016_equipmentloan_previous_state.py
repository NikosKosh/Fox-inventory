from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0015_document_consistency_safety"),
    ]

    operations = [
        migrations.AddField(
            model_name="equipmentloan",
            name="previous_state",
            field=models.JSONField(blank=True, default=dict, verbose_name="Состояние до передачи"),
        ),
    ]
