from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_party_links(apps, schema_editor):
    OrganizationCounterpartyLink = apps.get_model(
        "inventory", "OrganizationCounterpartyLink"
    )
    Counterparty = apps.get_model("inventory", "Counterparty")
    Contract = apps.get_model("inventory", "Contract")
    DocumentOperation = apps.get_model("inventory", "DocumentOperation")
    DocumentRecord = apps.get_model("inventory", "DocumentRecord")
    Reminder = apps.get_model("inventory", "Reminder")

    profile_by_organization = {
        item.linked_organization_id: item.pk
        for item in Counterparty.objects.filter(
            linked_organization_id__isnull=False
        )
    }

    def link(owner_id, counterparty_id):
        if not owner_id or not counterparty_id:
            return
        counterparty = Counterparty.objects.filter(pk=counterparty_id).first()
        if counterparty is None:
            return
        if counterparty.linked_organization_id == owner_id:
            return

        OrganizationCounterpartyLink.objects.get_or_create(
            organization_id=owner_id,
            counterparty_id=counterparty_id,
            defaults={"archived": False},
        )

        linked_org_id = counterparty.linked_organization_id
        reverse_counterparty_id = profile_by_organization.get(owner_id)
        if linked_org_id and reverse_counterparty_id:
            OrganizationCounterpartyLink.objects.get_or_create(
                organization_id=linked_org_id,
                counterparty_id=reverse_counterparty_id,
                defaults={"archived": False},
            )

    for Model in (Contract, DocumentOperation, DocumentRecord, Reminder):
        for owner_id, counterparty_id in (
            Model.objects.exclude(counterparty_id__isnull=True)
            .values_list("organization_id", "counterparty_id")
            .distinct()
        ):
            link(owner_id, counterparty_id)


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("inventory", "0013_counterparty_internal_links"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrganizationCounterpartyLink",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("archived", models.BooleanField(default=False, verbose_name="В архиве")),
                (
                    "counterparty",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="organization_links",
                        to="inventory.counterparty",
                        verbose_name="Вторая сторона",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_counterparty_links",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Создал",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="counterparty_links",
                        to="inventory.organization",
                        verbose_name="Организация",
                    ),
                ),
            ],
            options={
                "verbose_name": "связь организации со стороной",
                "verbose_name_plural": "связи организаций со сторонами",
                "ordering": ["counterparty__name", "pk"],
            },
        ),
        migrations.AddConstraint(
            model_name="organizationcounterpartylink",
            constraint=models.UniqueConstraint(
                fields=("organization", "counterparty"),
                name="uniq_org_counterparty_link",
            ),
        ),
        migrations.AddIndex(
            model_name="organizationcounterpartylink",
            index=models.Index(
                fields=["organization", "archived"],
                name="inv_party_link_org_idx",
            ),
        ),
        migrations.RunPython(backfill_party_links, migrations.RunPython.noop),
    ]
