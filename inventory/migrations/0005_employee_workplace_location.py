from django.db import migrations, models
import django.db.models.deletion


def link_existing_workplaces(apps, schema_editor):
    Employee = apps.get_model("inventory", "Employee")
    Location = apps.get_model("inventory", "Location")
    for employee in Employee.objects.exclude(workplace="").iterator():
        text = employee.workplace.strip()
        location = Location.objects.filter(address__iexact=text).first()
        if not location:
            location = Location.objects.filter(label__iexact=text).first()
        if location:
            employee.workplace_location_id = location.pk
            employee.save(update_fields=["workplace_location"])


class Migration(migrations.Migration):
    dependencies = [("inventory", "0004_equipment_accounting_group")]
    operations = [
        migrations.AddField(
            model_name="employee",
            name="workplace_location",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="employees",
                to="inventory.location",
                verbose_name="Объект / рабочее место",
            ),
        ),
        migrations.RunPython(link_existing_workplaces, migrations.RunPython.noop),
    ]
