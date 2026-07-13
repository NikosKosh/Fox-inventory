from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0002_employee_department_employee_workplace"),
    ]

    operations = [
        migrations.AddField(
            model_name="equipmentmovement",
            name="act",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="movements",
                to="inventory.act",
                verbose_name="Связанный акт",
            ),
        ),
    ]
