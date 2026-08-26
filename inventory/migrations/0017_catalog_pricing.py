import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


SKU_TOKEN_RE = re.compile(r"[\(\[]([^\)\]]+)[\)\]]")
PRICE_PATTERNS = [
    re.compile(r"цена\s+за\s+единицу\s*[:\-]?\s*([\d\s]+(?:[.,]\d{1,2})?)\s*руб", re.IGNORECASE),
    re.compile(r"стоимость\s*[:=]\s*([\d\s]+(?:[.,]\d{1,2})?)\s*руб", re.IGNORECASE),
]
DATE_RE = re.compile(r"\bот\s+(\d{1,2})[.](\d{1,2})[.](\d{2,4})\b", re.IGNORECASE)


def compact(value):
    return " ".join(str(value or "").strip().split())


def normalized(value):
    return compact(value).casefold().replace("ё", "е")


def extract_sku(model):
    for match in SKU_TOKEN_RE.findall(compact(model)):
        candidate = compact(match).strip(" ,.;")
        if not (4 <= len(candidate) <= 64):
            continue
        if not re.search(r"\d", candidate):
            continue
        has_letters = bool(re.search(r"[A-Za-zА-Яа-я]", candidate))
        digit_count = len(re.findall(r"\d", candidate))
        numeric_part_number = (not has_letters and digit_count >= 6 and bool(re.search(r"[-/]", candidate)))
        if not has_letters and not numeric_part_number:
            continue
        if re.fullmatch(r"[A-Za-zА-Яа-я0-9._/+\-]+", candidate):
            return candidate.upper()
    return ""


def identity_key(category_code, manufacturer, model, sku=""):
    resolved_sku = compact(sku) or extract_sku(model)
    discriminator = "sku:" + normalized(resolved_sku) if resolved_sku else "model:" + normalized(model)
    return f"{normalized(category_code).upper()}|{normalized(manufacturer)}|{discriminator}"


def parse_price(note):
    text = compact(note)
    for pattern in PRICE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        raw = match.group(1).replace(" ", "").replace(",", ".")
        try:
            return Decimal(raw).quantize(Decimal("0.01"))
        except InvalidOperation:
            continue
    return None


def parse_date(note):
    match = DATE_RE.search(compact(note))
    if not match:
        return None
    day, month, year = (int(value) for value in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_source(note):
    text = compact(note)
    marker = re.search(r"Источник:\s*(.+?)(?:;\s*цена\s+за\s+единицу|$)", text, re.IGNORECASE)
    return (marker.group(1).strip() if marker else "Импортировано из существующей карточки")[:255]


def choose_price(events):
    events = [event for event in events if event["price"] is not None]
    if not events:
        return None
    dated = [event for event in events if event["date"]]
    candidates = events
    if dated:
        latest_date = max(event["date"] for event in dated)
        candidates = [event for event in dated if event["date"] == latest_date]
    counts = Counter(event["price"] for event in candidates)
    max_count = max(counts.values())
    preferred = {price for price, count in counts.items() if count == max_count}
    for event in reversed(candidates):
        if event["price"] in preferred:
            return event["price"]
    return candidates[-1]["price"]


def most_common(values, fallback=""):
    values = [compact(value) for value in values if compact(value)]
    if not values:
        return fallback
    counts = Counter(values)
    return sorted(counts, key=lambda value: (-counts[value], -len(value), value.casefold()))[0]


def seed_catalog(apps, schema_editor):
    Equipment = apps.get_model("inventory", "Equipment")
    CatalogItem = apps.get_model("inventory", "CatalogItem")
    CatalogPriceHistory = apps.get_model("inventory", "CatalogPriceHistory")

    groups = defaultdict(list)
    equipment = list(Equipment.objects.select_related("category").order_by("pk"))
    for item in equipment:
        sku = extract_sku(item.model)
        key = identity_key(item.category.code, item.manufacturer, item.model, sku)
        groups[key].append(item)

    for key, items in groups.items():
        first = items[0]
        category = first.category
        name = most_common([item.name for item in items], category.name)
        manufacturer = most_common([item.manufacturer for item in items])
        model = most_common([item.model for item in items])
        sku = extract_sku(model)
        accounting_group = most_common([item.accounting_group for item in items], "employee")

        events = []
        for item in items:
            price = parse_price(item.notes)
            if price is None:
                continue
            events.append({
                "price": price,
                "date": parse_date(item.notes),
                "source": parse_source(item.notes),
                "pk": item.pk,
            })
        current_price = choose_price(events)
        distinct_prices = {event["price"] for event in events}

        catalog = CatalogItem.objects.create(
            category_id=category.pk,
            accounting_group=accounting_group,
            name=name,
            manufacturer=manufacturer,
            model=model,
            sku=sku,
            identity_key=key,
            unit_price=current_price,
            price_needs_review=(current_price is None or len(distinct_prices) > 1),
            notes="Создано автоматически при переходе FOX Inventory 1.5.1 → 1.6.0.",
            archived=False,
        )
        Equipment.objects.filter(pk__in=[item.pk for item in items]).update(catalog_item_id=catalog.pk)

        seen = set()
        for event in sorted(events, key=lambda row: (row["date"] or date.min, row["pk"])):
            marker = (event["price"], event["date"], event["source"])
            if marker in seen:
                continue
            seen.add(marker)
            CatalogPriceHistory.objects.create(
                catalog_item_id=catalog.pk,
                unit_price=event["price"],
                effective_date=event["date"],
                source=event["source"],
                changed_by_id=None,
            )


def unseed_catalog(apps, schema_editor):
    Equipment = apps.get_model("inventory", "Equipment")
    CatalogItem = apps.get_model("inventory", "CatalogItem")
    Equipment.objects.update(catalog_item_id=None)
    CatalogItem.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0016_equipmentloan_previous_state"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CatalogItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("accounting_group", models.CharField(choices=[("employee", "Для сотрудников"), ("technical", "Техническое")], db_index=True, default="employee", max_length=20, verbose_name="Контур учёта")),
                ("name", models.CharField(max_length=255, verbose_name="Наименование")),
                ("manufacturer", models.CharField(blank=True, max_length=150, verbose_name="Производитель")),
                ("model", models.CharField(blank=True, max_length=180, verbose_name="Модель / конфигурация")),
                ("sku", models.CharField(blank=True, db_index=True, max_length=80, verbose_name="Артикул / код модели")),
                ("identity_key", models.CharField(editable=False, max_length=600, unique=True)),
                ("unit_price", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name="Учётная цена, ₽")),
                ("price_needs_review", models.BooleanField(db_index=True, default=False, verbose_name="Цена требует подтверждения")),
                ("notes", models.TextField(blank=True, verbose_name="Комментарий")),
                ("archived", models.BooleanField(default=False, verbose_name="В архиве")),
                ("category", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="catalog_items", to="inventory.category", verbose_name="Категория")),
            ],
            options={
                "verbose_name": "номенклатура",
                "verbose_name_plural": "номенклатура",
                "ordering": ["category__name", "manufacturer", "model", "name"],
            },
        ),
        migrations.AddIndex(
            model_name="catalogitem",
            index=models.Index(fields=["manufacturer", "model"], name="catalog_maker_model_idx"),
        ),
        migrations.AddIndex(
            model_name="catalogitem",
            index=models.Index(fields=["category", "archived"], name="catalog_category_active_idx"),
        ),
        migrations.CreateModel(
            name="CatalogPriceHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("unit_price", models.DecimalField(decimal_places=2, max_digits=14, validators=[django.core.validators.MinValueValidator(0)], verbose_name="Цена, ₽")),
                ("effective_date", models.DateField(blank=True, null=True, verbose_name="Дата цены")),
                ("source", models.CharField(blank=True, max_length=255, verbose_name="Источник")),
                ("catalog_item", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="price_history", to="inventory.catalogitem", verbose_name="Номенклатура")),
                ("changed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="catalog_price_changes", to=settings.AUTH_USER_MODEL, verbose_name="Изменил")),
            ],
            options={
                "verbose_name": "история цены",
                "verbose_name_plural": "история цен",
                "ordering": ["-effective_date", "-created_at", "-pk"],
            },
        ),
        migrations.AddField(
            model_name="equipment",
            name="catalog_item",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="equipment", to="inventory.catalogitem", verbose_name="Номенклатура"),
        ),
        migrations.CreateModel(
            name="ActItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("position", models.PositiveIntegerField(default=1, verbose_name="№")),
                ("item_name", models.CharField(max_length=255, verbose_name="Наименование")),
                ("manufacturer", models.CharField(blank=True, max_length=150, verbose_name="Производитель")),
                ("model", models.CharField(blank=True, max_length=255, verbose_name="Модель")),
                ("internal_code", models.CharField(blank=True, max_length=80, verbose_name="Внутренний номер")),
                ("serial_number", models.CharField(blank=True, max_length=180, verbose_name="Серийный номер")),
                ("condition", models.CharField(blank=True, max_length=100, verbose_name="Состояние")),
                ("quantity", models.PositiveIntegerField(default=1, verbose_name="Количество")),
                ("unit_price", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name="Цена, ₽")),
                ("line_total", models.DecimalField(blank=True, decimal_places=2, max_digits=16, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name="Сумма, ₽")),
                ("act", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="inventory.act", verbose_name="Акт")),
                ("catalog_item", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="act_items", to="inventory.catalogitem", verbose_name="Номенклатура")),
                ("equipment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="act_items", to="inventory.equipment", verbose_name="Оборудование")),
            ],
            options={
                "verbose_name": "строка акта",
                "verbose_name_plural": "строки акта",
                "ordering": ["position", "pk"],
            },
        ),
        migrations.AddConstraint(
            model_name="actitem",
            constraint=models.UniqueConstraint(fields=("act", "equipment"), name="uniq_act_equipment_snapshot"),
        ),
        migrations.RunPython(seed_catalog, unseed_catalog),
    ]
