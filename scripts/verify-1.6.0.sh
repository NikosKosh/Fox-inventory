#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")/.."

echo "========== VERSION =========="
cat VERSION

echo "========== DJANGO CHECK =========="
docker compose exec -T web python manage.py check

echo "========== CATALOG / PRICE CHECK =========="
docker compose exec -T web python manage.py shell <<'PY'
from django.db.models import Q
from inventory.models import Act, ActItem, CatalogItem, Equipment

print("Equipment:", Equipment.objects.count())
print("Catalog items:", CatalogItem.objects.count())
print("Unlinked equipment:", Equipment.objects.filter(catalog_item__isnull=True).count())
print("Missing prices:", CatalogItem.objects.filter(unit_price__isnull=True, archived=False).count())
print("Prices requiring review:", CatalogItem.objects.filter(unit_price__isnull=False, price_needs_review=True, archived=False).count())
print("Acts:", Act.objects.count())
print("Act snapshots:", ActItem.objects.count())

print("\nHonor X16 catalog:")
for item in CatalogItem.objects.filter(
    manufacturer__iexact="Honor",
    model__icontains="MagicBook X16 2026",
).order_by("sku", "model"):
    print(
        item.pk,
        "|", item.sku or "—",
        "|", item.unit_price if item.unit_price is not None else "NO PRICE",
        "| review:", item.price_needs_review,
        "| units:", item.equipment.count(),
    )

if Equipment.objects.filter(catalog_item__isnull=True).exists():
    raise SystemExit("ERROR: some equipment was not linked to nomenclature")

argn = CatalogItem.objects.filter(manufacturer__iexact="Honor", sku__iexact="5301ARGN").count()
argq = CatalogItem.objects.filter(manufacturer__iexact="Honor", sku__iexact="5301ARGQ").count()
if argn != 1 or argq != 1:
    raise SystemExit(f"ERROR: expected one ARGN and one ARGQ catalog item, got ARGN={argn}, ARGQ={argq}")

print("\nCATALOG MIGRATION: OK")
PY

echo "========== MIGRATION =========="
docker compose exec -T web python manage.py showmigrations inventory | tail -25

echo "========== HEALTH =========="
curl -fsS http://127.0.0.1:${APP_PORT:-8088}/health/ || curl -fsS http://127.0.0.1:8088/health/
echo
