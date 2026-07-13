from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("inventory", "0005_employee_workplace_location")]

    operations = [
        migrations.CreateModel(
            name="Room",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=180, verbose_name="Название помещения")),
                ("room_type", models.CharField(choices=[("meeting", "Переговорная"), ("office", "Кабинет / рабочая зона"), ("server", "Серверная / техническая"), ("storage", "Склад / кладовая"), ("common", "Общее помещение"), ("other", "Другое")], default="office", max_length=20, verbose_name="Тип помещения")),
                ("floor", models.CharField(blank=True, max_length=80, verbose_name="Этаж / зона")),
                ("notes", models.TextField(blank=True, verbose_name="Описание / комментарий")),
                ("archived", models.BooleanField(default=False, verbose_name="В архиве")),
                ("location", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="rooms", to="inventory.location", verbose_name="Объект")),
            ],
            options={"verbose_name": "помещение", "verbose_name_plural": "помещения", "ordering": ["location__organization__name", "location__address", "name"]},
        ),
        migrations.AddConstraint(model_name="room", constraint=models.UniqueConstraint(fields=("location", "name"), name="uniq_location_room")),
        migrations.AddField(model_name="employee", name="room", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="employees", to="inventory.room", verbose_name="Помещение / комната")),
        migrations.AddField(model_name="cabinet", name="room", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="cabinets", to="inventory.room", verbose_name="Помещение")),
        migrations.AddField(model_name="equipment", name="room", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="equipment", to="inventory.room", verbose_name="Помещение / комната")),
    ]
