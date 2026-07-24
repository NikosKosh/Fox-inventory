from django.db import migrations, models
from django.db.models import Q

import inventory.validators


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0006_room_employee_room_cabinet_room_equipment_room"),
    ]

    operations = [
        migrations.AddField(
            model_name="equipment",
            name="mac_address",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=17,
                validators=[inventory.validators.validate_mac_address],
                verbose_name="MAC-адрес",
            ),
        ),
        migrations.AddConstraint(
            model_name="equipment",
            constraint=models.UniqueConstraint(
                fields=("mac_address",),
                condition=~Q(mac_address=""),
                name="uniq_nonempty_equipment_mac",
            ),
        ),
    ]
