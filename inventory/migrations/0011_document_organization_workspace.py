from django.db import migrations


def ensure_fox_it_shop_organization(apps, schema_editor):
    Organization = apps.get_model("inventory", "Organization")
    Organization.objects.get_or_create(
        prefix="FOXSHOP",
        defaults={
            "name": "ООО «ФОКС-АЙТИ ШОП»",
            "short_name": "FOX-IT SHOP",
            "kind": "company",
            "archived": False,
            "notes": "",
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0010_document_center"),
    ]

    operations = [
        migrations.RunPython(
            ensure_fox_it_shop_organization,
            migrations.RunPython.noop,
        ),
    ]
