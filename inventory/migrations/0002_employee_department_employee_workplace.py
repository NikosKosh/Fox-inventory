from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="department",
            field=models.CharField(blank=True, max_length=255, verbose_name="Подразделение"),
        ),
        migrations.AddField(
            model_name="employee",
            name="workplace",
            field=models.CharField(blank=True, max_length=500, verbose_name="Рабочее место"),
        ),
    ]
