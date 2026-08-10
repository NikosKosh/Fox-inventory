from django.db import migrations, models
import django.db.models.deletion


def _party_key(value):
    return "".join(ch for ch in (value or "").casefold() if ch.isalnum())


def link_internal_organizations(apps, schema_editor):
    Organization = apps.get_model("inventory", "Organization")
    Counterparty = apps.get_model("inventory", "Counterparty")

    organizations = Organization.objects.filter(kind="company").order_by("pk")

    for organization in organizations:
        keys = {
            _party_key(organization.name),
            _party_key(organization.short_name),
        }
        keys.discard("")

        candidates = []
        for counterparty in Counterparty.objects.filter(
            linked_organization__isnull=True
        ).order_by("pk"):
            cp_keys = {
                _party_key(counterparty.name),
                _party_key(counterparty.short_name),
            }
            cp_keys.discard("")
            if keys.intersection(cp_keys):
                candidates.append(counterparty)

        counterparty = None
        org_name = (organization.name or "").strip().casefold()
        for candidate in candidates:
            if (candidate.name or "").strip().casefold() == org_name:
                counterparty = candidate
                break

        if counterparty is None and candidates:
            counterparty = candidates[0]

        if counterparty is None:
            counterparty = Counterparty.objects.create(
                name=organization.name,
                short_name=organization.short_name,
            )

        counterparty.linked_organization_id = organization.pk
        counterparty.save(update_fields=["linked_organization"])


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0012_document_operations"),
    ]

    operations = [
        migrations.AddField(
            model_name="counterparty",
            name="linked_organization",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="counterparty_profile",
                to="inventory.organization",
                verbose_name="Внутренняя организация",
            ),
        ),
        migrations.RunPython(
            link_internal_organizations,
            migrations.RunPython.noop,
        ),
    ]
