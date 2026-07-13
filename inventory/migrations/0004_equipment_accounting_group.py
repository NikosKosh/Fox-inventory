from django.db import migrations, models


TECHNICAL_CODES = {
    "SW", "R", "AP", "CAM", "NVR", "HDD", "MB", "SFP", "CAB", "UPS", "ACS", "CT", "TL"
}
TECHNICAL_WORDS = (
    "коммутатор", "маршрутизатор", "точка доступа", "видеокамер", "камера видеонаблюдения",
    "видеорегистратор", "монтажная коробка", "sfp", "сетевой шкаф", "антивандальный шкаф",
    "пластиковый шкаф", "источник бесперебойного питания", "ибп", "скуд", "контроллер доступа",
    "считыватель", "кабельный тестер", "инструмент для обжима",
)


def classify_existing(apps, schema_editor):
    Equipment = apps.get_model("inventory", "Equipment")
    for item in Equipment.objects.select_related("category").all().iterator():
        code = (item.category.code or "").strip().upper()
        text = " ".join([
            item.category.name or "", item.name or "", item.manufacturer or "", item.model or ""
        ]).casefold().replace("ё", "е")
        group = "technical" if code in TECHNICAL_CODES or any(word in text for word in TECHNICAL_WORDS) else "employee"
        Equipment.objects.filter(pk=item.pk).update(accounting_group=group)


class Migration(migrations.Migration):
    dependencies = [("inventory", "0003_equipmentmovement_act")]
    operations = [
        migrations.AddField(
            model_name="equipment",
            name="accounting_group",
            field=models.CharField(
                choices=[("employee", "Для сотрудников"), ("technical", "Техническое")],
                db_index=True,
                default="employee",
                max_length=20,
                verbose_name="Контур учёта",
            ),
        ),
        migrations.RunPython(classify_existing, migrations.RunPython.noop),
    ]
