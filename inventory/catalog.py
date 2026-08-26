import re
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation


SKU_TOKEN_RE = re.compile(r"[\(\[]([^\)\]]+)[\)\]]")
PRICE_PATTERNS = [
    re.compile(r"цена\s+за\s+единицу\s*[:\-]?\s*([\d\s]+(?:[.,]\d{1,2})?)\s*руб", re.IGNORECASE),
    re.compile(r"стоимость\s*[:=]\s*([\d\s]+(?:[.,]\d{1,2})?)\s*руб", re.IGNORECASE),
]
DATE_RE = re.compile(r"\bот\s+(\d{1,2})[.](\d{1,2})[.](\d{2,4})\b", re.IGNORECASE)


def compact_text(value):
    return " ".join(str(value or "").strip().split())


def normalized_text(value):
    return compact_text(value).casefold().replace("ё", "е")


def extract_catalog_sku(model):
    text = compact_text(model)
    for match in SKU_TOKEN_RE.findall(text):
        candidate = compact_text(match).strip(" ,.;")
        # Numeric-only values like lens size (2.8) are not SKUs.
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


def make_catalog_identity_key(category_code, manufacturer, model, sku=""):
    code = normalized_text(category_code).upper()
    maker = normalized_text(manufacturer)
    resolved_sku = compact_text(sku) or extract_catalog_sku(model)
    if resolved_sku:
        discriminator = "sku:" + normalized_text(resolved_sku)
    else:
        discriminator = "model:" + normalized_text(model)
    return f"{code}|{maker}|{discriminator}"


def parse_note_price(note):
    text = compact_text(note)
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


def parse_note_date(note):
    text = compact_text(note)
    match = DATE_RE.search(text)
    if not match:
        return None
    day, month, year = (int(value) for value in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_price_source(note):
    text = compact_text(note)
    if not text:
        return ""
    marker = re.search(r"Источник:\s*(.+?)(?:;\s*цена\s+за\s+единицу|$)", text, re.IGNORECASE)
    if marker:
        return marker.group(1).strip()[:255]
    return text[:255]


def choose_current_price(price_events):
    """Choose current accounting price from imported history.

    The most recent dated source wins. If dates are absent/tied, use the most
    frequent price and then the most recently encountered event.
    """
    events = [event for event in price_events if event.get("price") is not None]
    if not events:
        return None
    dated = [event for event in events if event.get("date")]
    candidates = events
    if dated:
        latest_date = max(event["date"] for event in dated)
        candidates = [event for event in dated if event["date"] == latest_date]
    counts = Counter(event["price"] for event in candidates)
    best_count = max(counts.values())
    prices = {price for price, count in counts.items() if count == best_count}
    for event in reversed(candidates):
        if event["price"] in prices:
            return event["price"]
    return candidates[-1]["price"]


def format_money(value, *, suffix=True):
    if value is None:
        return "—"
    value = Decimal(value).quantize(Decimal("0.01"))
    text = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    if suffix:
        text += " ₽"
    return text


def ensure_catalog_item(*, category, accounting_group, name, manufacturer, model, unit_price=None, source="", effective_date=None, changed_by=None):
    """Resolve/create a catalog item and optionally register an explicit/imported price."""
    from .models import CatalogItem, CatalogPriceHistory

    sku = extract_catalog_sku(model)
    identity_key = make_catalog_identity_key(category.code, manufacturer, model, sku)
    item = CatalogItem.objects.filter(identity_key=identity_key).first()
    if item is None:
        item = CatalogItem.objects.create(
            category=category,
            accounting_group=accounting_group,
            name=compact_text(name) or category.name,
            manufacturer=compact_text(manufacturer),
            model=compact_text(model),
            sku=sku,
            identity_key=identity_key,
            unit_price=unit_price,
            price_needs_review=unit_price is None,
        )
    if unit_price is not None:
        unit_price = Decimal(unit_price).quantize(Decimal("0.01"))
        history_source = (source or "Импорт").strip()[:255]
        if not CatalogPriceHistory.objects.filter(
            catalog_item=item,
            unit_price=unit_price,
            effective_date=effective_date,
            source=history_source,
        ).exists():
            CatalogPriceHistory.objects.create(
                catalog_item=item,
                unit_price=unit_price,
                effective_date=effective_date,
                source=history_source,
                changed_by=changed_by,
            )

        # Purchase/import prices are history first, not an instruction to silently
        # rewrite a price the user already confirmed for the whole nomenclature.
        # A first known price is accepted automatically. A later differing price
        # preserves the current accounting price and flags the catalog item for
        # review, so one deliberate edit changes the shared price for every unit.
        if item.unit_price is None:
            item.unit_price = unit_price
            item.price_needs_review = False
            item.save(update_fields=["unit_price", "price_needs_review", "updated_at"])
        elif item.unit_price != unit_price:
            if not item.price_needs_review:
                item.price_needs_review = True
                item.save(update_fields=["price_needs_review", "updated_at"])
    return item


def equipment_price(item, overrides=None):
    if overrides and item.pk in overrides:
        return overrides[item.pk]
    return item.unit_price


def return_price_overrides(employee, equipment, act_date=None):
    """Reuse the most recent issue snapshot price when returning equipment."""
    from .models import Act, ActItem

    equipment = list(equipment)
    ids = [item.pk for item in equipment]
    if not ids or employee is None:
        return {}
    qs = ActItem.objects.filter(
        equipment_id__in=ids,
        act__employee=employee,
        act__act_type=Act.ActType.ISSUE,
    ).exclude(unit_price=None).select_related("act").order_by("equipment_id", "-act__act_date", "-act_id", "-pk")
    if act_date:
        qs = qs.filter(act__act_date__lte=act_date)
    result = {}
    for snapshot in qs:
        result.setdefault(snapshot.equipment_id, snapshot.unit_price)
    return result


def snapshot_act_items(act, equipment, *, price_overrides=None, replace=False):
    """Freeze names/identifiers/prices so later catalog edits cannot rewrite an act."""
    from .models import ActItem

    equipment = list(equipment)
    if replace:
        act.items.all().delete()
    existing = set(act.items.values_list("equipment_id", flat=True))
    snapshots = []
    position = act.items.count() + 1
    for item in equipment:
        if item.pk in existing:
            continue
        price = equipment_price(item, price_overrides)
        quantity = item.quantity or 1
        snapshots.append(ActItem(
            act=act,
            equipment=item,
            catalog_item=item.catalog_item if item.catalog_item_id else None,
            position=position,
            item_name=item.display_name,
            manufacturer=item.display_manufacturer,
            model=item.display_model,
            internal_code=item.internal_code,
            serial_number=item.serial_number,
            condition=item.get_condition_display(),
            quantity=quantity,
            unit_price=price,
            line_total=(price * quantity) if price is not None else None,
        ))
        position += 1
    if snapshots:
        ActItem.objects.bulk_create(snapshots)
    return snapshots
