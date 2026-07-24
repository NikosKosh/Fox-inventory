import csv
from io import BytesIO, StringIO
from pathlib import Path
from django.db import transaction
from openpyxl import Workbook, load_workbook
from .models import Category, Employee, Equipment, EquipmentMovement, Location, Organization, Room

IMPORT_COLUMNS = [
    "organization", "organization_prefix", "organization_kind", "category", "category_code", "accounting_group",
    "name", "manufacturer", "model", "serial_number", "mac_address", "internal_code", "hostname",
    "status", "condition", "employee", "position", "phone", "address", "location_label", "room", "room_type",
    "freeform_location", "quantity", "notes", "network_address", "network_username",
]


def _clean(value):
    return str(value).strip() if value is not None else ""



TECHNICAL_CATEGORY_CODES = {"SW", "R", "AP", "CAM", "NVR", "HDD", "MB", "SFP", "CAB", "UPS", "ACS", "CT", "TL"}
TECHNICAL_KEYWORDS = (
    "коммутатор", "маршрутизатор", "точка доступа", "видеокамер", "камера видеонаблюдения",
    "видеорегистратор", "монтажная коробка", "sfp", "сетевой шкаф", "антивандальный шкаф",
    "пластиковый шкаф", "источник бесперебойного питания", "ибп", "скуд", "контроллер доступа",
    "считыватель", "кабельный тестер", "инструмент для обжима",
)


def infer_accounting_group(category_code="", category_name="", name="", model=""):
    code = _clean(category_code).upper()
    text = " ".join([_clean(category_name), _clean(name), _clean(model)]).casefold().replace("ё", "е")
    if code in TECHNICAL_CATEGORY_CODES or any(word in text for word in TECHNICAL_KEYWORDS):
        return Equipment.AccountingGroup.TECHNICAL
    return Equipment.AccountingGroup.EMPLOYEE


def read_rows(uploaded_file):
    ext = Path(uploaded_file.name).suffix.lower()
    if ext == ".xlsx":
        wb = load_workbook(uploaded_file, read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        headers = [_clean(v).lower() for v in next(rows)]
        for raw in rows:
            yield dict(zip(headers, [_clean(v) for v in raw]))
    else:
        content = uploaded_file.read().decode("utf-8-sig")
        dialect = csv.Sniffer().sniff(content[:2048], delimiters=";,	,")
        reader = csv.DictReader(StringIO(content), dialect=dialect)
        for raw in reader:
            yield {str(k).strip().lower(): _clean(v) for k, v in raw.items()}


@transaction.atomic
def import_equipment(uploaded_file, user=None):
    created = 0
    updated = 0
    errors = []
    for index, row in enumerate(read_rows(uploaded_file), start=2):
        try:
            org_name = row.get("organization") or row.get("организация")
            if not org_name:
                raise ValueError("не указана organization")
            prefix = row.get("organization_prefix") or row.get("префикс") or "ORG"
            org = Organization.objects.filter(name=org_name).first()
            if not org:
                requested_prefix = prefix.upper()
                if Organization.objects.filter(prefix=requested_prefix).exists():
                    requested_prefix = f"{requested_prefix}{index}"[:12]
                org = Organization.objects.create(
                    name=org_name, prefix=requested_prefix,
                    kind=row.get("organization_kind") or Organization.Kind.COMPANY,
                )
            category_name = row.get("category") or row.get("категория") or "Другое"
            category_code = (row.get("category_code") or row.get("код категории") or "OT").upper()
            category = Category.objects.filter(name=category_name).first() or Category.objects.filter(code=category_code).first()
            if not category:
                category = Category.objects.create(name=category_name, code=category_code)
            employee = None
            employee_name = row.get("employee") or row.get("сотрудник")
            if employee_name:
                employee, _ = Employee.objects.get_or_create(
                    full_name=employee_name,
                    organization=org,
                    defaults={"position": row.get("position", ""), "phone": row.get("phone", "")},
                )
            location = None
            address = row.get("address") or row.get("адрес")
            if address:
                location, _ = Location.objects.get_or_create(
                    organization=org,
                    address=address,
                    defaults={"label": row.get("location_label", "")},
                )
            room = None
            room_name = row.get("room") or row.get("помещение")
            if room_name and location:
                room, _ = Room.objects.get_or_create(
                    location=location, name=room_name,
                    defaults={"room_type": row.get("room_type") or Room.RoomType.OTHER},
                )
            defaults = {
                "category": category,
                "accounting_group": row.get("accounting_group") or row.get("контур учёта") or infer_accounting_group(category.code, category.name, row.get("name", ""), row.get("model", "")),
                "name": row.get("name") or row.get("наименование") or category_name,
                "manufacturer": row.get("manufacturer", ""),
                "model": row.get("model", ""),
                "serial_number": row.get("serial_number", ""),
                "mac_address": row.get("mac_address") or row.get("mac") or row.get("mac-адрес") or row.get("mac адрес") or "",
                "hostname": row.get("hostname", ""),
                "owner": org,
                "responsible_employee": employee,
                "location": location,
                "room": room,
                "freeform_location": row.get("freeform_location", ""),
                "usage_status": row.get("status") or (Equipment.UsageStatus.EMPLOYEE if employee else Equipment.UsageStatus.STOCK),
                "condition": row.get("condition") or Equipment.Condition.USED,
                "quantity": int(row.get("quantity") or 1),
                "notes": row.get("notes", ""),
                "network_address": row.get("network_address", ""),
                "network_username": row.get("network_username", ""),
            }
            internal_code = row.get("internal_code", "")
            serial = defaults["serial_number"]
            if internal_code:
                obj, was_created = Equipment.objects.update_or_create(internal_code=internal_code, defaults=defaults)
            elif serial:
                obj, was_created = Equipment.objects.update_or_create(owner=org, serial_number=serial, defaults=defaults)
            else:
                obj = Equipment.objects.create(**defaults)
                was_created = True
            EquipmentMovement.objects.create(
                equipment=obj,
                movement_type=EquipmentMovement.MovementType.IMPORT,
                to_employee=employee,
                to_organization=org,
                to_status=obj.usage_status,
                notes=f"Импорт, строка {index}",
                created_by=user,
            )
            created += int(was_created)
            updated += int(not was_created)
        except Exception as exc:
            errors.append(f"Строка {index}: {exc}")
    return created, updated, errors


def equipment_export_workbook(queryset):
    wb = Workbook()
    ws = wb.active
    ws.title = "Оборудование"
    headers = [
        "Код", "Контур учёта", "Категория", "Наименование", "Производитель", "Модель", "Серийный номер", "MAC-адрес", "Hostname",
        "Владелец", "Сотрудник", "Организация сотрудника", "Статус", "Состояние", "Адрес", "Помещение", "Шкаф",
        "Место текстом", "Количество", "Адрес управления", "Логин", "Комментарий",
    ]
    ws.append(headers)
    for obj in queryset.select_related("category", "owner", "responsible_employee__organization", "location", "room", "cabinet"):
        ws.append([
            obj.internal_code, obj.get_accounting_group_display(), obj.category.name, obj.name, obj.manufacturer, obj.model, obj.serial_number, obj.mac_address, obj.hostname,
            str(obj.owner), obj.responsible_employee.full_name if obj.responsible_employee else "",
            str(obj.responsible_employee.organization) if obj.responsible_employee else "",
            obj.get_usage_status_display(), obj.get_condition_display(), str(obj.location) if obj.location else "",
            obj.room.name if obj.room else "", obj.cabinet.name if obj.cabinet else "", obj.freeform_location, obj.quantity,
            obj.network_address, obj.network_username, obj.notes,
        ])
    for col in ws.columns:
        width = min(max(len(str(cell.value or "")) for cell in col) + 2, 50)
        ws.column_dimensions[col[0].column_letter].width = width
    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream


def import_template_workbook():
    wb = Workbook()
    ws = wb.active
    ws.title = "Импорт"
    ws.append(IMPORT_COLUMNS)
    ws.append([
        "ООО Компания", "ORG", "company", "Ноутбук", "N", "employee", "Ноутбук", "Lenovo", "ThinkPad",
        "SN123", "AA:BB:CC:DD:EE:FF", "ORG-N-001", "org-n-001", "employee", "used", "Иванов Иван Иванович",
        "Инженер", "+7...", "г. Город, ул. Примерная, д. 1", "Главный офис", "Переговорная", "meeting", "", 1, "", "", "",
    ])
    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream
