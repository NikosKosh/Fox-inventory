import mimetypes
from decimal import Decimal
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, F, Q, Sum, Value
from django.db.models.functions import Coalesce, Lower
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from .forms import (
    ActForm, AssignmentForm, CabinetForm, CatalogItemForm, CategoryForm, EmployeeActDocumentForm,
    EmployeeEquipmentActWorkflowForm, EmployeeEquipmentOperationForm, EmployeeForm, EquipmentForm, ImportForm, LoanForm,
    LocationForm, OrganizationForm, RepairForm, RoomForm, RoomEquipmentAssignForm,
    ProjectForm, ProjectStageForm, MaterialReceiptForm, MaterialAdjustmentForm, ProjectStageOperationForm, CatalogConvertMaterialForm,
)
from .models import (Act, ActItem, Cabinet, CatalogItem, CatalogPriceHistory, Category, Contract, DocumentRecord, Employee, Equipment, EquipmentLoan, EquipmentMovement, Location, Organization, Reminder, RepairRecord, Room, Warehouse, MaterialStock, MaterialTransaction, Project, ProjectStage, ProjectOperation, ProjectOperationLine)
from .services import equipment_export_workbook, import_equipment, import_template_workbook
from .documents import build_employee_transfer_docx, short_person_name
from .catalog import ensure_catalog_item, format_money, return_price_overrides, snapshot_act_items


BUSINESS_MOVEMENT_TYPES = [
    EquipmentMovement.MovementType.ASSIGNED,
    EquipmentMovement.MovementType.RETURNED,
    EquipmentMovement.MovementType.INSTALLED,
    EquipmentMovement.MovementType.LOANED,
    EquipmentMovement.MovementType.LOAN_RETURN,
    EquipmentMovement.MovementType.REPAIR,
    EquipmentMovement.MovementType.DISPOSED,
    EquipmentMovement.MovementType.ACT,
    EquipmentMovement.MovementType.PROJECT_INSTALLED,
    EquipmentMovement.MovementType.PROJECT_ROLLBACK,
]


def _visible_movements(queryset):
    return queryset.filter(movement_type__in=BUSINESS_MOVEMENT_TYPES)


def _safe_act_filename(employee, act_date, operation):
    kind = "передачи" if operation == "issue" else "возврата"
    safe_employee = short_person_name(employee.full_name).replace(" ", "-").replace(".", "")
    return f"Акт-{kind}-{safe_employee}-{act_date:%Y-%m-%d}.docx"



def _current_assignment_document_status(employee, equipment):
    """Return equipment IDs whose current assignment is supported by an issue act."""
    equipment_ids = [item.pk for item in equipment]
    if not equipment_ids:
        return set()

    latest = {}
    movements = (
        EquipmentMovement.objects.filter(
            equipment_id__in=equipment_ids,
            to_employee=employee,
            movement_type=EquipmentMovement.MovementType.ASSIGNED,
        )
        .select_related("act")
        .order_by("equipment_id", "-created_at", "-pk")
    )
    for movement in movements:
        latest.setdefault(movement.equipment_id, movement)

    documented_fallback = set(
        Act.objects.filter(
            employee=employee,
            act_type=Act.ActType.ISSUE,
            equipment__in=equipment_ids,
        ).values_list("equipment", flat=True)
    )
    result = set()
    for equipment_id in equipment_ids:
        movement = latest.get(equipment_id)
        if movement is not None:
            if movement.act_id:
                result.add(equipment_id)
        elif equipment_id in documented_fallback:
            result.add(equipment_id)
    return result


@login_required
def protected_media(request, path):
    if not default_storage.exists(path):
        raise Http404
    content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return FileResponse(default_storage.open(path, "rb"), content_type=content_type)


def health(request):
    return JsonResponse({"status": "ok"})


def _base_context():
    return {
        "active_loans_count": EquipmentLoan.objects.filter(status=EquipmentLoan.Status.ACTIVE).count(),
        "archived_employees_count": Employee.objects.filter(archived=True).count(),
    }


def _remember_back_url(request, session_key, default_url):
    # Back-navigation belongs to the current URL, not to shared session state.
    # This keeps two open tabs from overwriting each other's return target.
    candidate = request.GET.get("back", "").strip()
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return default_url






def _apply_sorting(queryset, request, allowed, default_sort, default_direction="asc"):
    """Apply a whitelisted, stable server-side sort while preserving filters."""
    sort = request.GET.get("sort", default_sort)
    if sort not in allowed:
        sort = default_sort
    direction = request.GET.get("dir", default_direction)
    if direction not in {"asc", "desc"}:
        direction = "asc"

    ordering = []
    for expression in allowed[sort]:
        expression = F(expression) if isinstance(expression, str) else expression
        ordering.append(
            expression.desc(nulls_last=True) if direction == "desc"
            else expression.asc(nulls_last=True)
        )
    ordering.append(F("pk").asc())
    return queryset.order_by(*ordering), sort, direction


def _sort_object_list(objects, request, allowed, default_sort, default_direction="asc"):
    """Sort a small in-memory object list, used for object cards with calculated counters."""
    sort = request.GET.get("sort", default_sort)
    if sort not in allowed:
        sort = default_sort
    direction = request.GET.get("dir", default_direction)
    if direction not in {"asc", "desc"}:
        direction = "asc"
    objects.sort(key=allowed[sort], reverse=direction == "desc")
    return objects, sort, direction

def _paginate(request, queryset, per_page=50):
    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return page_obj, params.urlencode()


def _employees_for_location(location):
    return Employee.objects.filter(archived=False).filter(
        Q(workplace_location=location)
        | Q(workplace__iexact=location.address)
        | Q(workplace__iexact=location.label)
    ).distinct()


def _attention_snapshot(limit=30):
    unlinked = list(
        Employee.objects.filter(archived=False, workplace_location__isnull=True)
        .exclude(workplace="")
        .select_related("organization")[:limit]
    )
    misplaced = list(
        Equipment.objects.filter(archived=False, usage_status=Equipment.UsageStatus.OBJECT)
        .filter(location__isnull=True, cabinet__isnull=True, freeform_location="")
        .select_related("category", "catalog_item", "owner")[:limit]
    )
    duplicate_serial_values = list(
        Equipment.objects.filter(archived=False).exclude(serial_number="")
        .values("serial_number").annotate(total=Count("id")).filter(total__gt=1)
        .order_by("serial_number")[:limit]
    )
    duplicate_serials = []
    for row in duplicate_serial_values:
        duplicate_serials.append({
            "serial": row["serial_number"],
            "items": list(Equipment.objects.filter(archived=False, serial_number=row["serial_number"])[:10]),
        })

    incomplete = []
    without_act = []
    employees = Employee.objects.filter(archived=False).select_related("organization", "workplace_location")
    for employee in employees:
        equipment = list(employee.equipment.filter(archived=False).select_related("category", "catalog_item", "owner"))
        if not equipment:
            continue
        state = _employee_equipment_set(equipment, [])
        if state["requirements"] and not state["complete"] and len(incomplete) < limit:
            incomplete.append({"employee": employee, "missing": state["missing"]})
        documented = _current_assignment_document_status(employee, equipment)
        missing_docs = [item for item in equipment if item.pk not in documented]
        if missing_docs and len(without_act) < limit:
            without_act.append({"employee": employee, "items": missing_docs})

    catalog_price_issues = list(
        CatalogItem.objects.filter(archived=False)
        .filter(Q(unit_price__isnull=True) | Q(price_needs_review=True))
        .select_related("category")
        .order_by("category__name", "manufacturer", "model", "pk")[:limit]
    )

    total = (
        len(unlinked) + len(misplaced) + len(duplicate_serials)
        + len(incomplete) + len(without_act) + len(catalog_price_issues)
    )
    return {
        "unlinked_employees": unlinked,
        "misplaced_equipment": misplaced,
        "duplicate_serials": duplicate_serials,
        "incomplete_sets": incomplete,
        "without_act": without_act,
        "catalog_price_issues": catalog_price_issues,
        "total": total,
    }

def _employee_equipment_set(assigned_equipment, shared_equipment):
    combined = [*assigned_equipment, *shared_equipment]
    codes = {item.category.code.upper() for item in combined if item.category_id}
    has_nettop = "W" in codes
    has_laptop = "N" in codes
    if has_nettop:
        requirements = [("Монитор", "M"), ("Клавиатура", "KB"), ("Мышь", "MS")]
    elif has_laptop:
        requirements = [("Мышь", "MS")]
    else:
        requirements = []
    rows = [
        {"label": label, "code": code, "present": code in codes}
        for label, code in requirements
    ]
    return {
        "kind": "Неттоп / рабочее место" if has_nettop else ("Ноутбук" if has_laptop else "Без основного компьютера"),
        "requirements": rows,
        "missing": [row["label"] for row in rows if not row["present"]],
        "complete": bool(rows) and all(row["present"] for row in rows),
    }


@login_required
def dashboard(request):
    context = _base_context()
    status_labels = dict(Equipment.UsageStatus.choices)
    status_stats = list(Equipment.objects.filter(archived=False).values("usage_status").annotate(total=Count("id")).order_by("-total"))
    for row in status_stats:
        row["label"] = status_labels.get(row["usage_status"], row["usage_status"])
    attention = _attention_snapshot(limit=8)
    object_cards = []
    for location in Location.objects.filter(archived=False).select_related("organization")[:8]:
        employees = _employees_for_location(location)
        equipment = Equipment.objects.filter(
            Q(location=location) | Q(cabinet__location=location) | Q(responsible_employee__in=employees),
            archived=False,
        ).distinct()
        object_cards.append({
            "location": location,
            "employees": employees.count(),
            "equipment": equipment.count(),
        })
    today = timezone.localdate()
    dashboard_reminders = []
    for reminder in Reminder.objects.filter(active=True).select_related("organization", "counterparty", "contract", "location").order_by("next_due_date", "pk"):
        due = reminder.effective_due_date
        visible_from = due - timedelta(days=reminder.remind_days_before)
        if visible_from <= today:
            dashboard_reminders.append({
                "reminder": reminder,
                "due": due,
                "overdue": due < today,
                "today": due == today,
            })
        if len(dashboard_reminders) >= 6:
            break

    priced_equipment = list(
        Equipment.objects.filter(archived=False).select_related("catalog_item")
    )
    inventory_value = sum((item.total_value or 0) for item in priced_equipment)
    catalog_missing_prices = CatalogItem.objects.filter(archived=False, unit_price__isnull=True).count()
    catalog_review_prices = CatalogItem.objects.filter(archived=False, unit_price__isnull=False, price_needs_review=True).count()

    context.update({
        "equipment_total": Equipment.objects.filter(archived=False).count(),
        "employees_total": Employee.objects.filter(archived=False).count(),
        "organizations_total": Organization.objects.filter(archived=False).count(),
        "acts_total": Act.objects.count(),
        "documents_total": DocumentRecord.objects.filter(trashed_at__isnull=True).count(),
        "contracts_total": Contract.objects.filter(archived=False).count(),
        "inventory_value": inventory_value,
        "catalog_total": CatalogItem.objects.filter(archived=False).count(),
        "catalog_missing_prices": catalog_missing_prices,
        "catalog_review_prices": catalog_review_prices,
        "dashboard_reminders": dashboard_reminders,
        "locations_total": Location.objects.filter(archived=False).count(),
        "warehouse_employee_count": Equipment.objects.filter(
            archived=False, responsible_employee__isnull=True,
            usage_status__in=[Equipment.UsageStatus.STOCK, Equipment.UsageStatus.RESERVE],
            accounting_group=Equipment.AccountingGroup.EMPLOYEE,
        ).count(),
        "warehouse_technical_count": Equipment.objects.filter(
            archived=False, responsible_employee__isnull=True,
            usage_status__in=[Equipment.UsageStatus.STOCK, Equipment.UsageStatus.RESERVE],
            accounting_group=Equipment.AccountingGroup.TECHNICAL,
        ).count(),
        "status_stats": status_stats,
        "recent_movements": _visible_movements(EquipmentMovement.objects.select_related("equipment", "to_employee", "created_by", "act"))[:8],
        "recent_acts": Act.objects.select_related("employee")[:5],
        "attention_total": attention["total"],
        "object_cards": object_cards,
    })
    return render(request, "inventory/dashboard.html", context)

@login_required
def organization_list(request):
    qs = Organization.objects.annotate(
        equipment_count=Count("owned_equipment", distinct=True),
        employee_count=Count("employees", distinct=True),
        contract_count=Count("contracts", filter=Q(contracts__archived=False), distinct=True),
        document_count=Count("document_records", filter=Q(document_records__trashed_at__isnull=True), distinct=True),
    )
    qs, sort, direction = _apply_sorting(qs, request, {
        "name": (Lower("name"),),
        "prefix": (Lower("prefix"), Lower("name")),
        "kind": ("kind", Lower("name")),
        "employees": (F("employee_count"), Lower("name")),
        "equipment": (F("equipment_count"), Lower("name")),
        "contracts": (F("contract_count"), Lower("name")),
        "documents": (F("document_count"), Lower("name")),
    }, "name")
    return render(request, "inventory/organization_list.html", {"objects": qs, "sort": sort, "direction": direction})


@login_required
def organization_detail(request, pk):
    obj = get_object_or_404(Organization, pk=pk)
    tab = request.GET.get("tab", "overview")
    if tab not in {"overview", "objects", "employees", "equipment", "warehouse", "projects", "catalog", "history"}:
        tab = "overview"

    employees = obj.employees.filter(archived=False).select_related("workplace_location", "room")
    equipment = obj.owned_equipment.filter(archived=False).select_related(
        "catalog_item", "category", "responsible_employee", "location", "room", "cabinet"
    )
    locations = list(obj.locations.filter(archived=False).select_related("responsible_employee"))
    for location in locations:
        loc_employees = _employees_for_location(location)
        loc_equipment = equipment.filter(
            Q(location=location) | Q(cabinet__location=location) | Q(responsible_employee__in=loc_employees)
        ).distinct()
        location.employee_count = loc_employees.count()
        location.equipment_count = loc_equipment.count()
        location.inventory_value = sum((x.total_value or 0 for x in loc_equipment), Decimal("0"))
        location.active_project_count = location.projects.filter(status=Project.Status.ACTIVE).count()

    material_stocks = MaterialStock.objects.filter(
        warehouse__organization=obj, warehouse__archived=False, catalog_item__archived=False
    ).select_related("warehouse", "catalog_item", "catalog_item__category")
    projects = obj.projects.select_related("location", "responsible_employee").prefetch_related("stages__operations__lines")
    active_projects = projects.filter(status=Project.Status.ACTIVE)
    inventory_value = sum((x.total_value or 0 for x in equipment), Decimal("0"))
    material_value = sum((x.total_value or 0 for x in material_stocks), Decimal("0"))
    project_cost = sum((p.total_cost for p in projects), Decimal("0"))
    # «Без места» — только реальные ошибки размещения. Склад и резерв сами по себе
    # являются корректным состоянием и не должны засорять блок внимания организации.
    unplaced = equipment.filter(
        Q(
            usage_status=Equipment.UsageStatus.OBJECT,
            location__isnull=True, room__isnull=True, cabinet__isnull=True, freeform_location="",
        )
        | Q(usage_status=Equipment.UsageStatus.EMPLOYEE, responsible_employee__isnull=True)
    ).count()
    attention = {
        "unplaced": unplaced,
        "repairs": equipment.filter(usage_status=Equipment.UsageStatus.REPAIR).count(),
        "missing_prices": CatalogItem.objects.filter(
            Q(equipment__owner=obj) | Q(material_stocks__warehouse__organization=obj), archived=False, unit_price__isnull=True
        ).distinct().count(),
    }
    org_catalog = list(
        CatalogItem.objects.filter(
            Q(equipment__owner=obj, equipment__archived=False)
            | Q(material_stocks__warehouse__organization=obj, material_stocks__warehouse__archived=False),
            archived=False,
        ).select_related("category").distinct().order_by("category__name", "name")
    )
    for catalog in org_catalog:
        if catalog.inventory_kind == CatalogItem.InventoryKind.EQUIPMENT:
            catalog.org_quantity = catalog.equipment.filter(owner=obj, archived=False).aggregate(total=Sum("quantity"))["total"] or 0
        else:
            catalog.org_quantity = catalog.material_stocks.filter(
                warehouse__organization=obj, warehouse__archived=False
            ).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
        catalog.org_value = catalog.unit_price * catalog.org_quantity if catalog.unit_price is not None else None

    recent_material = MaterialTransaction.objects.filter(warehouse__organization=obj).select_related("catalog_item", "warehouse", "project_line__operation__stage__project")[:12]
    recent_equipment = _visible_movements(
        EquipmentMovement.objects.filter(equipment__owner=obj).select_related("equipment", "to_employee", "created_by", "project_stage__project")
    )[:12]
    return render(request, "inventory/organization_detail.html", {
        "object": obj, "tab": tab, "employees": employees, "equipment": equipment, "locations": locations,
        "material_stocks": material_stocks, "projects": projects, "active_projects": active_projects, "org_catalog": org_catalog,
        "inventory_value": inventory_value, "material_value": material_value, "project_cost": project_cost,
        "attention": attention, "recent_material": recent_material, "recent_equipment": recent_equipment,
    })


@login_required
def organization_form(request, pk=None):
    obj = get_object_or_404(Organization, pk=pk) if pk else None
    form = OrganizationForm(request.POST or None, instance=obj)
    if form.is_valid():
        obj = form.save()
        Warehouse.objects.get_or_create(organization=obj, name="Основной склад", defaults={"is_default": True})
        messages.success(request, "Организация сохранена.")
        return redirect(obj)
    return render(request, "inventory/model_form.html", {"form": form, "title": "Организация / владелец", "cancel_url": reverse("organization_list")})


@login_required
def employee_list(request):
    show_archived = request.GET.get("archived") == "1"
    qs = Employee.objects.select_related("organization", "workplace_location").annotate(
        equipment_count=Count("equipment", filter=Q(equipment__archived=False), distinct=True)
    ).filter(archived=show_archived)
    q = request.GET.get("q", "").strip()
    location = request.GET.get("location", "")
    if q:
        qs = qs.filter(
            Q(full_name__icontains=q) | Q(position__icontains=q) | Q(department__icontains=q)
            | Q(workplace__icontains=q) | Q(phone__icontains=q) | Q(organization__name__icontains=q)
        )
    if location:
        qs = qs.filter(workplace_location_id=location)
    qs, sort, direction = _apply_sorting(qs, request, {
        "full_name": (Lower("full_name"),),
        "organization": (Coalesce(Lower("organization__name"), Value("")), Lower("full_name")),
        "department": (Coalesce(Lower("department"), Value("")), Coalesce(Lower("position"), Value("")), Lower("full_name")),
        "location": (Coalesce(Lower("workplace_location__label"), Lower("workplace_location__address"), Lower("workplace"), Value("")), Lower("full_name")),
        "phone": (Coalesce(Lower("phone"), Value("")), Lower("full_name")),
        "equipment": (F("equipment_count"), Lower("full_name")),
    }, "full_name")
    page_obj, query_string = _paginate(request, qs, 50)
    return render(request, "inventory/employee_list.html", {
        "objects": page_obj.object_list, "page_obj": page_obj, "query_string": query_string,
        "show_archived": show_archived, "q": q, "selected_location": location,
        "locations": Location.objects.filter(archived=False).select_related("organization"),
        "sort": sort, "direction": direction,
    })

@login_required
def employee_detail(request, pk):
    obj = get_object_or_404(Employee.objects.select_related("organization"), pk=pk)
    back_url = _remember_back_url(request, "employee_list_back_url", reverse("employee_list"))
    history = _visible_movements(
        EquipmentMovement.objects.filter(Q(from_employee=obj) | Q(to_employee=obj))
        .select_related("equipment", "from_employee", "to_employee", "created_by", "act")
        .distinct()
        .order_by("-created_at", "-pk")
    )[:100]
    equipment = list(
        obj.equipment.filter(archived=False).select_related("category", "catalog_item", "owner")
    )
    documented_equipment_ids = _current_assignment_document_status(obj, equipment)
    for item in equipment:
        item.current_assignment_has_act = item.pk in documented_equipment_ids
    equipment_without_act_count = sum(1 for item in equipment if not item.current_assignment_has_act)
    surname = (obj.full_name or "").split()[0] if (obj.full_name or "").split() else ""
    shared_equipment = []
    if surname:
        shared_equipment = list(
            Equipment.objects.filter(
                archived=False,
                responsible_employee__isnull=True,
                usage_status=Equipment.UsageStatus.OBJECT,
                freeform_location__icontains=surname,
            ).select_related("category", "catalog_item", "owner", "location")
        )
    return render(request, "inventory/employee_detail.html", {
        "object": obj,
        "equipment": equipment,
        "shared_equipment": shared_equipment,
        "equipment_set": _employee_equipment_set(equipment, shared_equipment),
        "equipment_without_act_count": equipment_without_act_count,
        "acts": obj.acts.prefetch_related("equipment"),
        "history": history,
        "back_url": back_url,
    })


@login_required
def employee_form(request, pk=None):
    obj = get_object_or_404(Employee, pk=pk) if pk else None
    is_new = obj is None
    initial = {}
    if is_new and request.GET.get("organization"):
        initial["organization"] = request.GET.get("organization")
    if is_new and request.GET.get("location"):
        location = get_object_or_404(Location, pk=request.GET.get("location"), archived=False)
        initial["workplace_location"] = location.pk
        initial["organization"] = location.organization_id
    if is_new and request.GET.get("room"):
        room = get_object_or_404(Room, pk=request.GET.get("room"), archived=False)
        initial["room"] = room.pk
        initial["workplace_location"] = room.location_id
    form = EmployeeForm(request.POST or None, instance=obj, initial=initial)
    if form.is_valid():
        obj = form.save()
        if is_new:
            messages.success(request, "Сотрудник создан. Теперь можно закрепить фактическое оборудование без акта или оформить выдачу сразу с документом.")
            return redirect("employee_assign_without_act", pk=obj.pk)
        messages.success(request, "Сотрудник сохранён.")
        return redirect(obj)
    return render(request, "inventory/model_form.html", {"form": form, "title": "Сотрудник", "cancel_url": reverse("employee_list")})

@login_required
@require_POST
def employee_archive(request, pk):
    obj = get_object_or_404(Employee, pk=pk)
    if not obj.archived:
        assigned = obj.equipment.filter(archived=False).count()
        active_loans = obj.organization_loans.filter(status=EquipmentLoan.Status.ACTIVE).count()
        if assigned or active_loans:
            parts = []
            if assigned:
                parts.append(f"закреплено оборудования: {assigned}")
            if active_loans:
                parts.append(f"активных временных передач: {active_loans}")
            messages.error(
                request,
                "Нельзя отправить сотрудника в архив: " + ", ".join(parts) + ". Сначала завершите эти операции.",
            )
            return redirect("employee_detail", pk=pk)
    obj.archived = not obj.archived
    obj.save(update_fields=["archived", "updated_at"])
    messages.success(request, "Статус сотрудника изменён.")
    return redirect("employee_detail", pk=pk)


@login_required
@require_POST
def employee_delete(request, pk):
    obj = get_object_or_404(Employee, pk=pk)
    if obj.equipment.exists() or obj.acts.exists() or obj.movements_from.exists() or obj.movements_to.exists():
        messages.error(request, "Удаление запрещено: у сотрудника есть оборудование, акты или история. Используйте архивирование.")
    else:
        obj.delete()
        messages.success(request, "Сотрудник удалён.")
        return redirect("employee_list")
    return redirect("employee_detail", pk=pk)


@login_required
def employee_assign_without_act(request, pk):
    employee = get_object_or_404(Employee.objects.select_related("organization"), pk=pk)
    form = EmployeeEquipmentOperationForm(
        request.POST or None,
        employee=employee,
        operation="issue",
    )
    if form.is_valid():
        selected = list(form.cleaned_data["equipment"])
        notes = form.cleaned_data.get("notes", "").strip()
        with transaction.atomic():
            locked = list(
                Equipment.objects.select_for_update().select_related("owner")
                .filter(pk__in=[item.pk for item in selected])
                .order_by("pk")
            )
            if len(locked) != len(selected):
                messages.error(request, "Часть выбранного оборудования не найдена. Обновите страницу.")
                return redirect(request.path)
            invalid = [
                item for item in locked
                if item.responsible_employee_id is not None
                or item.usage_status not in {Equipment.UsageStatus.STOCK, Equipment.UsageStatus.RESERVE}
            ]
            if invalid:
                messages.error(request, f"Оборудование уже изменилось: {invalid[0]}. Обновите страницу.")
                return redirect(request.path)

            for item in locked:
                old_status = item.usage_status
                item.responsible_employee = employee
                item.usage_status = Equipment.UsageStatus.EMPLOYEE
                item.location = None
                item.cabinet = None
                item.freeform_location = ""
                item.save()
                movement_note = "Фактически закреплено за сотрудником без акта. Акт будет оформлен позднее."
                if notes:
                    movement_note += f" {notes}"
                EquipmentMovement.objects.create(
                    equipment=item,
                    movement_type=EquipmentMovement.MovementType.ASSIGNED,
                    from_employee=None,
                    to_employee=employee,
                    from_organization=item.owner,
                    to_organization=item.owner,
                    from_status=old_status,
                    to_status=item.usage_status,
                    notes=movement_note,
                    created_by=request.user,
                )

        messages.success(
            request,
            f"Без акта закреплено оборудования: {len(selected)}. Позже его можно включить в акт передачи из карточки сотрудника.",
        )
        return redirect(employee)

    return render(request, "inventory/employee_equipment_operation.html", {
        "object": employee,
        "form": form,
        "operation": "issue",
        "without_act": True,
        "title": "Добавить оборудование без акта",
        "selected_equipment_ids": request.POST.getlist("equipment") if request.method == "POST" else [],
    })


def _employee_equipment_act_workflow(request, pk, operation):
    if operation not in {"issue", "return"}:
        raise Http404
    employee = get_object_or_404(Employee.objects.select_related("organization"), pk=pk)
    form = EmployeeEquipmentActWorkflowForm(
        request.POST or None,
        employee=employee,
        operation=operation,
    )
    if form.is_valid():
        selected = list(form.cleaned_data["equipment"])
        act_date = form.cleaned_data["act_date"]
        filename = _safe_act_filename(employee, act_date, operation)
        notes = form.cleaned_data.get("notes", "").strip()

        with transaction.atomic():
            locked = list(
                Equipment.objects.select_for_update().select_related("owner", "catalog_item", "category")
                .filter(pk__in=[item.pk for item in selected])
                .order_by("pk")
            )
            if len(locked) != len(selected):
                messages.error(request, "Часть выбранного оборудования не найдена. Обновите страницу.")
                return redirect(request.path)

            if operation == "issue":
                invalid = [
                    item for item in locked
                    if item.responsible_employee_id is not None
                    or item.usage_status not in {Equipment.UsageStatus.STOCK, Equipment.UsageStatus.RESERVE}
                ]
            else:
                invalid = [item for item in locked if item.responsible_employee_id != employee.pk]
            if invalid:
                messages.error(request, f"Оборудование уже изменилось: {invalid[0]}. Обновите страницу.")
                return redirect(request.path)

            price_overrides = (
                return_price_overrides(employee, locked, act_date)
                if operation == "return"
                else {item.pk: item.unit_price for item in locked if item.unit_price is not None}
            )
            # Generate the document from the same locked rows and the same explicit
            # price map that will be written into ActItem snapshots. This prevents
            # a concurrent catalog price edit from making the DOCX and DB history differ.
            stream = build_employee_transfer_docx(
                employee=employee,
                equipment=locked,
                act_date=act_date,
                city=form.cleaned_data["city"],
                organization_name=form.cleaned_data["organization_name"],
                representative_position=form.cleaned_data["representative_position"],
                representative_name=form.cleaned_data["representative_name"],
                act_type=operation,
                price_overrides=price_overrides,
            )

            act = Act(
                act_type=Act.ActType.ISSUE if operation == "issue" else Act.ActType.RETURN,
                act_date=act_date,
                employee=employee,
                from_organization=employee.organization,
                notes=notes or (
                    "Выдача оборудования и формирование акта из карточки сотрудника."
                    if operation == "issue"
                    else "Возврат оборудования на склад и формирование акта из карточки сотрудника."
                ),
            )
            act.document.save(filename, ContentFile(stream.getvalue()), save=False)
            act.save()
            act.equipment.set(locked)
            snapshot_act_items(act, locked, price_overrides=price_overrides)

            for item in locked:
                old_employee = item.responsible_employee
                old_status = item.usage_status
                if operation == "issue":
                    item.responsible_employee = employee
                    item.usage_status = Equipment.UsageStatus.EMPLOYEE
                    movement_type = EquipmentMovement.MovementType.ASSIGNED
                    movement_note = f"Выдано по акту от {act_date:%d.%m.%Y}."
                else:
                    item.responsible_employee = None
                    item.usage_status = Equipment.UsageStatus.STOCK
                    item.location = None
                    item.cabinet = None
                    item.freeform_location = ""
                    movement_type = EquipmentMovement.MovementType.RETURNED
                    movement_note = f"Возвращено на склад по акту от {act_date:%d.%m.%Y}."
                item.save()
                EquipmentMovement.objects.create(
                    equipment=item,
                    movement_type=movement_type,
                    from_employee=old_employee,
                    to_employee=item.responsible_employee,
                    from_organization=item.owner,
                    to_organization=item.owner,
                    from_status=old_status,
                    to_status=item.usage_status,
                    notes=(movement_note + (f" {notes}" if notes else "")),
                    created_by=request.user,
                    act=act,
                )

        action = "выдано" if operation == "issue" else "возвращено на склад"
        messages.success(request, f"Оборудование {action}: {len(selected)}. Акт сформирован и сохранён в истории.")
        return redirect(f"{act.get_absolute_url()}?download=1")

    return render(request, "inventory/employee_equipment_act_workflow.html", {
        "object": employee,
        "form": form,
        "operation": operation,
        "title": "Выдать оборудование и сформировать акт" if operation == "issue" else "Забрать оборудование и сформировать акт",
        "selected_equipment_ids": request.POST.getlist("equipment") if request.method == "POST" else [
            str(pk) for pk in form.initial.get("equipment", [])
        ],
    })


@login_required
def employee_issue_equipment(request, pk):
    return _employee_equipment_act_workflow(request, pk, "issue")


@login_required
def employee_return_equipment(request, pk):
    return _employee_equipment_act_workflow(request, pk, "return")


@login_required
def employee_generate_act(request, pk, act_type):
    if act_type not in {"issue", "return"}:
        raise Http404
    employee = get_object_or_404(Employee.objects.select_related("organization"), pk=pk)
    form = EmployeeActDocumentForm(request.POST or None, employee=employee, act_type=act_type)

    if request.method == "GET" and act_type == "issue":
        current_equipment = list(form.fields["equipment"].queryset)
        documented_ids = _current_assignment_document_status(employee, current_equipment)
        form.initial["equipment"] = [item.pk for item in current_equipment if item.pk not in documented_ids]

    if form.is_valid():
        equipment = list(form.cleaned_data["equipment"])
        act_date = form.cleaned_data["act_date"]
        filename = _safe_act_filename(employee, act_date, act_type)

        if act_type == "issue":
            with transaction.atomic():
                locked = list(
                    Equipment.objects.select_for_update().select_related("catalog_item", "category", "owner").filter(
                        pk__in=[item.pk for item in equipment],
                        archived=False,
                        responsible_employee=employee,
                    ).order_by("pk")
                )
                if len(locked) != len(equipment):
                    messages.error(request, "Часть оборудования больше не закреплена за сотрудником. Обновите страницу.")
                    return redirect(request.path)

                price_overrides = {item.pk: item.unit_price for item in locked if item.unit_price is not None}
                stream = build_employee_transfer_docx(
                    employee=employee,
                    equipment=locked,
                    act_date=act_date,
                    city=form.cleaned_data["city"],
                    organization_name=form.cleaned_data["organization_name"],
                    representative_position=form.cleaned_data["representative_position"],
                    representative_name=form.cleaned_data["representative_name"],
                    act_type=act_type,
                    price_overrides=price_overrides,
                )

                act = Act(
                    act_type=Act.ActType.ISSUE,
                    act_date=act_date,
                    employee=employee,
                    from_organization=employee.organization,
                    notes="Акт передачи сформирован после фактического закрепления оборудования.",
                )
                act.document.save(filename, ContentFile(stream.getvalue()), save=False)
                act.save()
                act.equipment.set(locked)
                snapshot_act_items(act, locked, price_overrides=price_overrides)

                linked = 0
                for item in locked:
                    movement = (
                        EquipmentMovement.objects.select_for_update()
                        .filter(
                            equipment=item,
                            to_employee=employee,
                            movement_type=EquipmentMovement.MovementType.ASSIGNED,
                            act__isnull=True,
                        )
                        .order_by("-created_at", "-pk")
                        .first()
                    )
                    if movement:
                        movement.act = act
                        suffix = f" Акт оформлен {act_date:%d.%m.%Y}."
                        if suffix.strip() not in movement.notes:
                            movement.notes = (movement.notes.rstrip() + suffix).strip()
                        movement.save(update_fields=["act", "notes"])
                        linked += 1

            messages.success(
                request,
                f"Акт сохранён и связан с оборудованием: {len(equipment)}. Записей выдачи дополнено актом: {linked}.",
            )
            return redirect(f"{act.get_absolute_url()}?download=1")

        price_overrides = return_price_overrides(employee, equipment, act_date)
        stream = build_employee_transfer_docx(
            employee=employee,
            equipment=equipment,
            act_date=act_date,
            city=form.cleaned_data["city"],
            organization_name=form.cleaned_data["organization_name"],
            representative_position=form.cleaned_data["representative_position"],
            representative_name=form.cleaned_data["representative_name"],
            act_type=act_type,
            price_overrides=price_overrides,
        )
        kind = "возврата"
        safe_employee = short_person_name(employee.full_name).replace(" ", "-").replace(".", "")
        download_name = f"Акт-{kind}-{safe_employee}-{act_date:%Y-%m-%d}.docx"
        response = HttpResponse(
            stream.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(download_name)}"
        return response

    return render(request, "inventory/employee_act_generate.html", {
        "object": employee, "form": form, "act_type": act_type,
        "title": "Акт передачи сотруднику" if act_type == "issue" else "Акт возврата от сотрудника",
        "selected_equipment_ids": request.POST.getlist("equipment") if request.method == "POST" else [str(pk) for pk in form.initial.get("equipment", [])],
    })


@login_required
def location_list(request):
    qs = Location.objects.select_related("organization").filter(archived=False).annotate(
        cabinet_count=Count("cabinets", filter=Q(cabinets__archived=False), distinct=True),
        room_count=Count("rooms", filter=Q(rooms__archived=False), distinct=True),
        linked_employee_count=Count("employees", filter=Q(employees__archived=False), distinct=True),
    )
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(label__icontains=q) | Q(address__icontains=q) | Q(organization__name__icontains=q))
    objects = list(qs)
    for location in objects:
        employees = _employees_for_location(location)
        equipment = Equipment.objects.filter(
            Q(location=location) | Q(cabinet__location=location) | Q(responsible_employee__in=employees),
            archived=False,
        ).distinct()
        location.equipment_count = equipment.count()
        location.technical_count = equipment.filter(accounting_group=Equipment.AccountingGroup.TECHNICAL).count()
        location.employee_equipment_count = equipment.filter(accounting_group=Equipment.AccountingGroup.EMPLOYEE).count()
        location.employee_count = employees.count()
    objects, sort, direction = _sort_object_list(objects, request, {
        "name": lambda item: ((item.label or item.address or "").casefold(), item.pk),
        "organization": lambda item: ((str(item.organization) or "").casefold(), (item.label or item.address or "").casefold(), item.pk),
        "employees": lambda item: (item.employee_count, (item.label or item.address or "").casefold(), item.pk),
        "equipment": lambda item: (item.equipment_count, (item.label or item.address or "").casefold(), item.pk),
        "technical": lambda item: (item.technical_count, (item.label or item.address or "").casefold(), item.pk),
        "rooms": lambda item: (item.room_count, (item.label or item.address or "").casefold(), item.pk),
        "cabinets": lambda item: (item.cabinet_count, (item.label or item.address or "").casefold(), item.pk),
    }, "name")
    return render(request, "inventory/location_list.html", {"objects": objects, "q": q, "sort": sort, "direction": direction})

@login_required
def location_detail(request, pk):
    obj = get_object_or_404(Location.objects.select_related("organization"), pk=pk)
    tab = request.GET.get("tab", "overview")
    if tab not in {"overview", "employees", "rooms", "equipment", "projects", "documents", "history"}:
        tab = "overview"
    employees_qs = _employees_for_location(obj).select_related("organization", "workplace_location").annotate(
        equipment_count=Count("equipment", filter=Q(equipment__archived=False), distinct=True)
    )
    employee_q = request.GET.get("employee_q", "").strip()
    if employee_q:
        employees_qs = employees_qs.filter(
            Q(full_name__icontains=employee_q) | Q(position__icontains=employee_q) | Q(department__icontains=employee_q)
        )
    employees_qs, employee_sort, employee_direction = _apply_sorting(employees_qs, request, {
        "full_name": (Lower("full_name"),),
        "organization": (Coalesce(Lower("organization__name"), Value("")), Lower("full_name")),
        "department": (Coalesce(Lower("department"), Value("")), Coalesce(Lower("position"), Value("")), Lower("full_name")),
        "equipment": (F("equipment_count"), Lower("full_name")),
    }, "full_name")
    all_employee_ids = list(_employees_for_location(obj).values_list("id", flat=True))
    equipment = Equipment.objects.filter(
        Q(location=obj) | Q(cabinet__location=obj) | Q(responsible_employee_id__in=all_employee_ids),
        archived=False,
).select_related("category", "catalog_item", "owner", "responsible_employee", "room", "cabinet", "location").distinct()
    q = request.GET.get("q", "").strip()
    group = request.GET.get("group", "")
    status = request.GET.get("status", "")
    if q:
        equipment = equipment.filter(
            Q(internal_code__icontains=q) | Q(name__icontains=q) | Q(manufacturer__icontains=q)
            | Q(model__icontains=q) | Q(serial_number__icontains=q) | Q(mac_address__icontains=q) | Q(network_address__icontains=q)
            | Q(responsible_employee__full_name__icontains=q)
        )
    if group in dict(Equipment.AccountingGroup.choices):
        equipment = equipment.filter(accounting_group=group)
    if status in dict(Equipment.UsageStatus.choices):
        equipment = equipment.filter(usage_status=status)
    equipment, equipment_sort, equipment_direction = _apply_sorting(equipment, request, {
        "group": ("accounting_group", Lower("internal_code"), Lower("name")),
        "code": (Coalesce(Lower("internal_code"), Value("")), Lower("name")),
        "name": (Lower("name"), Coalesce(Lower("model"), Value(""))),
        "assignment": (Coalesce(Lower("responsible_employee__full_name"), Lower("cabinet__name"), Lower("freeform_location"), Value("")), Lower("name")),
        "status": ("usage_status", Lower("name")),
        "condition": ("condition", Lower("name")),
    }, "code")

    employee_cards = []
    incomplete_count = 0
    for employee in employees_qs:
        assigned = list(employee.equipment.filter(archived=False).select_related("category", "catalog_item", "owner"))
        set_state = _employee_equipment_set(assigned, [])
        if set_state["requirements"] and not set_state["complete"]:
            incomplete_count += 1
        employee_cards.append({"employee": employee, "equipment": assigned, "set": set_state})

    rooms = obj.rooms.filter(archived=False).annotate(
        equipment_count=Count("equipment", filter=Q(equipment__archived=False), distinct=True),
        employee_count=Count("employees", filter=Q(employees__archived=False), distinct=True),
        cabinet_count=Count("cabinets", filter=Q(cabinets__archived=False), distinct=True),
    )
    cabinets = obj.cabinets.filter(archived=False).select_related("room").annotate(
        equipment_count=Count("equipment", filter=Q(equipment__archived=False), distinct=True)
    )
    equipment_ids = list(equipment.values_list("id", flat=True))
    history = _visible_movements(
        EquipmentMovement.objects.filter(equipment_id__in=equipment_ids)
        .select_related("equipment", "from_employee", "to_employee", "act", "created_by")
    )[:60]
    projects = obj.projects.select_related("organization", "responsible_employee").prefetch_related("stages__operations__lines")
    linked_contracts = obj.contracts.filter(archived=False).select_related("counterparty", "organization")
    linked_documents = obj.document_records.filter(trashed_at__isnull=True).select_related(
        "document_type", "counterparty", "contract", "organization"
    )
    return render(request, "inventory/location_detail.html", {
        "object": obj, "tab": tab, "employees": employees_qs, "employee_cards": employee_cards,
        "equipment": equipment,
        "technical_equipment": equipment.filter(accounting_group=Equipment.AccountingGroup.TECHNICAL),
        "employee_equipment": equipment.filter(accounting_group=Equipment.AccountingGroup.EMPLOYEE),
        "technical_count": equipment.filter(accounting_group=Equipment.AccountingGroup.TECHNICAL).count(),
        "employee_equipment_count": equipment.filter(accounting_group=Equipment.AccountingGroup.EMPLOYEE).count(),
        "rooms": rooms, "cabinets": cabinets, "history": history, "incomplete_count": incomplete_count,
        "projects": projects, "linked_contracts": linked_contracts, "linked_documents": linked_documents,
        "q": q, "employee_q": employee_q, "selected_group": group, "selected_status": status,
        "group_choices": Equipment.AccountingGroup.choices, "statuses": Equipment.UsageStatus.choices,
        "employee_sort": employee_sort, "employee_direction": employee_direction,
        "equipment_sort": equipment_sort, "equipment_direction": equipment_direction,
    })

@login_required
def room_detail(request, pk):
    obj = get_object_or_404(Room.objects.select_related("location__organization"), pk=pk)
    employees = obj.employees.filter(archived=False).select_related("organization", "workplace_location")
    equipment = obj.equipment.filter(archived=False).select_related(
        "category", "owner", "responsible_employee", "location", "room", "cabinet"
    ).order_by("accounting_group", "category__name", "internal_code", "name")
    cabinets = obj.cabinets.filter(archived=False).annotate(
        equipment_count=Count("equipment", filter=Q(equipment__archived=False), distinct=True)
    )
    return render(request, "inventory/room_detail.html", {
        "object": obj, "employees": employees, "equipment": equipment, "cabinets": cabinets,
        "technical_count": equipment.filter(accounting_group=Equipment.AccountingGroup.TECHNICAL).count(),
        "employee_count": equipment.filter(accounting_group=Equipment.AccountingGroup.EMPLOYEE).count(),
    })


@login_required
def room_form(request, pk=None):
    obj = get_object_or_404(Room, pk=pk) if pk else None
    initial = {}
    if obj is None and request.GET.get("owner"):
        initial["owner"] = request.GET.get("owner")
    if obj is None and request.GET.get("location"):
        initial["location"] = request.GET.get("location")
    form = RoomForm(request.POST or None, instance=obj, initial=initial)
    if form.is_valid():
        room = form.save()
        messages.success(request, "Помещение сохранено.")
        return redirect(room)
    cancel_url = obj.get_absolute_url() if obj else (reverse("location_detail", args=[initial["location"]]) if initial.get("location") else reverse("location_list"))
    return render(request, "inventory/model_form.html", {"form": form, "title": "Помещение / комната", "cancel_url": cancel_url})


@login_required
def room_assign_equipment(request, pk):
    room = get_object_or_404(Room.objects.select_related("location"), pk=pk, archived=False)
    form = RoomEquipmentAssignForm(request.POST or None, room=room)
    if form.is_valid():
        items = list(form.cleaned_data["equipment"])
        with transaction.atomic():
            for item in items:
                old_status = item.usage_status
                old_location = item.location
                item.responsible_employee = None
                item.location = room.location
                item.room = room
                item.cabinet = None
                item.freeform_location = ""
                item.usage_status = Equipment.UsageStatus.OBJECT
                item.save()
                old_place = old_location.label or old_location.address if old_location else "склад"
                notes = f"{form.cleaned_data['notes']} Перемещено: {old_place} → {room.name}.".strip()
                EquipmentMovement.objects.create(
                    equipment=item, movement_type=EquipmentMovement.MovementType.INSTALLED,
                    from_status=old_status, to_status=item.usage_status, notes=notes, created_by=request.user,
                )
        messages.success(request, f"В помещение добавлено оборудования: {len(items)}.")
        return redirect(room)
    return render(request, "inventory/room_assign_equipment.html", {"object": room, "form": form})


@login_required
def warehouse(request):
    tab = request.GET.get("tab", "equipment")
    if tab == "materials":
        return redirect("material_stock_list")
    qs = Equipment.objects.filter(
        archived=False, responsible_employee__isnull=True,
        usage_status__in=[Equipment.UsageStatus.STOCK, Equipment.UsageStatus.RESERVE],
    ).select_related("category", "catalog_item", "owner", "location", "room", "cabinet")
    q = request.GET.get("q", "").strip(); owner = request.GET.get("owner", "")
    category = request.GET.get("category", ""); group = request.GET.get("group", "")
    if q:
        qs = qs.filter(Q(internal_code__icontains=q) | Q(name__icontains=q) | Q(manufacturer__icontains=q)
                       | Q(model__icontains=q) | Q(serial_number__icontains=q) | Q(mac_address__icontains=q) | Q(hostname__icontains=q) | Q(network_address__icontains=q))
    if owner: qs = qs.filter(owner_id=owner)
    if category: qs = qs.filter(category_id=category)
    employee_count = qs.filter(accounting_group=Equipment.AccountingGroup.EMPLOYEE).count()
    technical_count = qs.filter(accounting_group=Equipment.AccountingGroup.TECHNICAL).count()
    if group in dict(Equipment.AccountingGroup.choices): qs = qs.filter(accounting_group=group)
    qs, sort, direction = _apply_sorting(qs, request, {
        "group": ("accounting_group", Lower("internal_code"), Lower("name")),
        "code": (Coalesce(Lower("internal_code"), Value("")), Lower("name")),
        "name": (Lower("name"), Coalesce(Lower("model"), Value(""))),
        "owner": (Coalesce(Lower("owner__name"), Value("")), Lower("name")),
        "status": ("usage_status", Lower("name")),
        "condition": ("condition", Lower("name")),
    }, "code")
    page_obj, query_string = _paginate(request, qs, 60)
    return render(request, "inventory/warehouse.html", {
        "objects": page_obj.object_list, "page_obj": page_obj, "query_string": query_string,
        "q": q, "selected_owner": owner, "selected_category": category, "selected_group": group,
        "owners": Organization.objects.filter(archived=False), "categories": Category.objects.filter(archived=False),
        "group_choices": Equipment.AccountingGroup.choices, "employee_count": employee_count, "technical_count": technical_count,
        "sort": sort, "direction": direction,
    })

@login_required
def location_form(request, pk=None):
    obj = get_object_or_404(Location, pk=pk) if pk else None
    initial = {}
    if obj is None and request.GET.get("organization"):
        initial["organization"] = request.GET.get("organization")
    form = LocationForm(request.POST or None, instance=obj, initial=initial)
    if form.is_valid():
        saved = form.save()
        messages.success(request, "Объект сохранён.")
        return redirect(saved)
    return render(request, "inventory/model_form.html", {"form": form, "title": "Объект", "cancel_url": obj.get_absolute_url() if obj else reverse("location_list")})


@login_required
def cabinet_list(request):
    qs = Cabinet.objects.select_related("location__organization").annotate(equipment_count=Count("equipment"))
    qs, sort, direction = _apply_sorting(qs, request, {
        "organization": (Lower("location__organization__name"), Lower("location__address"), Lower("name")),
        "location": (Lower("location__address"), Lower("name")),
        "name": (Lower("name"),),
        "equipment": (F("equipment_count"), Lower("name")),
    }, "organization")
    return render(request, "inventory/cabinet_list.html", {"objects": qs, "sort": sort, "direction": direction})


@login_required
def cabinet_detail(request, pk):
    obj = get_object_or_404(Cabinet.objects.select_related("location__organization", "room"), pk=pk)
    return render(request, "inventory/cabinet_detail.html", {"object": obj, "equipment": obj.equipment.filter(archived=False).select_related("category", "catalog_item", "owner", "responsible_employee")})


@login_required
def cabinet_form(request, pk=None):
    obj = get_object_or_404(Cabinet, pk=pk) if pk else None
    initial = {}
    if obj is None and request.GET.get("location"):
        initial["location"] = request.GET.get("location")
    if obj is None and request.GET.get("room"):
        room = get_object_or_404(Room, pk=request.GET.get("room"), archived=False)
        initial["room"] = room.pk
        initial["location"] = room.location_id
    form = CabinetForm(request.POST or None, instance=obj, initial=initial)
    if form.is_valid():
        obj = form.save()
        messages.success(request, "Шкаф сохранён.")
        return redirect(obj)
    return render(request, "inventory/model_form.html", {"form": form, "title": "Коммутационный шкаф", "cancel_url": reverse("cabinet_list")})


@login_required
def category_list(request):
    qs = Category.objects.filter(archived=False).annotate(equipment_count=Count("equipment"))
    qs, sort, direction = _apply_sorting(qs, request, {
        "name": (Lower("name"),),
        "code": (Lower("code"),),
        "tracking": ("tracking_mode", Lower("name")),
        "equipment": (F("equipment_count"), Lower("name")),
    }, "name")
    return render(request, "inventory/category_list.html", {"objects": qs, "sort": sort, "direction": direction})


@login_required
def category_form(request, pk=None):
    obj = get_object_or_404(Category, pk=pk) if pk else None
    form = CategoryForm(request.POST or None, instance=obj)
    if form.is_valid():
        form.save()
        messages.success(request, "Категория сохранена.")
        return redirect("category_list")
    return render(request, "inventory/model_form.html", {"form": form, "title": "Категория", "cancel_url": reverse("category_list")})


@login_required
def catalog_list(request):
    qs = CatalogItem.objects.filter(archived=False).select_related("category").annotate(
        equipment_count=Count("equipment", filter=Q(equipment__archived=False), distinct=True),
        unit_count=Sum("equipment__quantity", filter=Q(equipment__archived=False)),
    )
    q = request.GET.get("q", "").strip()
    category = request.GET.get("category", "")
    price_state = request.GET.get("price", "")
    kind = request.GET.get("kind", "")
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(manufacturer__icontains=q) | Q(model__icontains=q) | Q(sku__icontains=q)
        )
    if category:
        qs = qs.filter(category_id=category)
    if kind in dict(CatalogItem.InventoryKind.choices):
        qs = qs.filter(inventory_kind=kind)
    if price_state == "missing":
        qs = qs.filter(unit_price__isnull=True)
    elif price_state == "review":
        qs = qs.filter(unit_price__isnull=False, price_needs_review=True)
    elif price_state == "ok":
        qs = qs.filter(unit_price__isnull=False, price_needs_review=False)
    qs = qs.order_by("category__name", "manufacturer", "model", "name")

    objects = list(qs)
    total_value = 0
    missing_price_count = 0
    review_count = 0
    for item in objects:
        item.unit_count = item.unit_count or 0
        material_qty = item.material_stocks.aggregate(total=Sum("quantity"))["total"] or Decimal("0")
        item.stock_quantity = material_qty
        effective_qty = material_qty if item.inventory_kind != CatalogItem.InventoryKind.EQUIPMENT else item.unit_count
        item.inventory_value = item.unit_price * effective_qty if item.unit_price is not None else None
        if item.inventory_value is not None:
            total_value += item.inventory_value
        if item.unit_price is None:
            missing_price_count += 1
        elif item.price_needs_review:
            review_count += 1

    return render(request, "inventory/catalog_list.html", {
        "objects": objects,
        "q": q,
        "categories": Category.objects.filter(archived=False),
        "selected_category": category,
        "selected_price": price_state,
        "selected_kind": kind,
        "kinds": CatalogItem.InventoryKind.choices,
        "total_value": total_value,
        "missing_price_count": missing_price_count,
        "review_count": review_count,
    })


@login_required
def catalog_detail(request, pk):
    obj = get_object_or_404(CatalogItem.objects.select_related("category"), pk=pk)
    equipment = obj.equipment.filter(archived=False).select_related(
        "owner", "responsible_employee", "location", "room", "cabinet", "category", "catalog_item"
    ).order_by("internal_code", "pk")
    unit_count = sum(item.quantity for item in equipment)
    material_stocks = obj.material_stocks.select_related("warehouse", "warehouse__organization").all()
    material_quantity = sum((x.quantity for x in material_stocks), Decimal("0"))
    effective_quantity = material_quantity if obj.inventory_kind != CatalogItem.InventoryKind.EQUIPMENT else unit_count
    inventory_value = obj.unit_price * effective_quantity if obj.unit_price is not None else None
    return render(request, "inventory/catalog_detail.html", {
        "object": obj,
        "equipment": equipment,
        "price_history": obj.price_history.select_related("changed_by").all(),
        "unit_count": unit_count,
        "inventory_value": inventory_value,
        "material_stocks": material_stocks,
        "material_quantity": material_quantity,
        "can_convert_to_material": not obj.equipment.filter(archived=False).exclude(usage_status=Equipment.UsageStatus.DISPOSED).exists(),
    })


@login_required
def catalog_form(request, pk=None):
    if not request.user.is_staff:
        raise PermissionDenied
    obj = get_object_or_404(CatalogItem, pk=pk) if pk else None
    old_price = obj.unit_price if obj else None
    form = CatalogItemForm(request.POST or None, instance=obj)
    if form.is_valid():
        catalog = form.save(commit=False)
        if catalog.unit_price is not None:
            catalog.price_needs_review = False
        catalog.save()
        form.save_m2m()

        if catalog.unit_price is not None and old_price != catalog.unit_price:
            CatalogPriceHistory.objects.create(
                catalog_item=catalog,
                unit_price=catalog.unit_price,
                effective_date=timezone.localdate(),
                source="Ручное изменение в FOX Inventory",
                changed_by=request.user,
            )

        catalog.equipment.update(
            category=catalog.category,
            accounting_group=catalog.accounting_group,
            name=catalog.name,
            manufacturer=catalog.manufacturer,
            model=catalog.model,
        )
        messages.success(request, "Номенклатура сохранена. Цена применяется ко всем связанным экземплярам.")
        return redirect(catalog)
    return render(request, "inventory/model_form.html", {
        "form": form,
        "title": "Номенклатура",
        "cancel_url": obj.get_absolute_url() if obj else reverse("catalog_list"),
    })


@login_required
def catalog_convert_to_material(request, pk):
    if not request.user.is_staff:
        raise PermissionDenied
    obj = get_object_or_404(CatalogItem.objects.select_related("category"), pk=pk)
    if obj.inventory_kind != CatalogItem.InventoryKind.EQUIPMENT:
        messages.info(request, "Эта номенклатура уже ведётся количественно.")
        return redirect(obj)

    active_units = obj.equipment.filter(archived=False).exclude(usage_status=Equipment.UsageStatus.DISPOSED).select_related("owner")
    unsafe_units = []
    convertible_units = []
    for item in active_units:
        unsafe_reason = None
        if item.usage_status not in {Equipment.UsageStatus.STOCK, Equipment.UsageStatus.RESERVE}:
            unsafe_reason = f"статус «{item.get_usage_status_display()}»"
        elif item.responsible_employee_id or item.location_id or item.room_id or item.cabinet_id or item.freeform_location:
            unsafe_reason = "есть действующее назначение/местоположение"
        elif item.serial_number or item.mac_address or item.hostname or item.network_address or item.network_username or item.network_password_encrypted:
            unsafe_reason = "есть индивидуальные идентификаторы или сетевые реквизиты"
        elif item.origin_project_id or item.origin_project_stage_id:
            unsafe_reason = "есть проект происхождения"
        elif item.loans.filter(status=EquipmentLoan.Status.ACTIVE).exists():
            unsafe_reason = "есть активная временная передача"
        if unsafe_reason:
            item.convert_block_reason = unsafe_reason
            unsafe_units.append(item)
        else:
            convertible_units.append(item)

    preview_by_org = {}
    for item in convertible_units:
        bucket = preview_by_org.setdefault(item.owner_id, {"organization": item.owner, "quantity": Decimal("0"), "cards": 0})
        bucket["quantity"] += Decimal(item.quantity)
        bucket["cards"] += 1
    preview = list(preview_by_org.values())

    form = CatalogConvertMaterialForm(request.POST or None, initial={"unit_of_measure": obj.unit_of_measure})
    if request.method == "POST" and unsafe_units:
        messages.error(request, "Преобразование заблокировано: есть действующие индивидуальные экземпляры, которые нельзя безопасно считать материалом.")
    elif form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            catalog = CatalogItem.objects.select_for_update().get(pk=obj.pk)
            if catalog.inventory_kind != CatalogItem.InventoryKind.EQUIPMENT:
                messages.info(request, "Номенклатура уже была преобразована другим пользователем.")
                return redirect(catalog)

            locked_units = list(
                Equipment.objects.select_for_update().filter(catalog_item=catalog, archived=False)
                .exclude(usage_status=Equipment.UsageStatus.DISPOSED)
                .select_related("owner")
            )
            for item in locked_units:
                if (
                    item.usage_status not in {Equipment.UsageStatus.STOCK, Equipment.UsageStatus.RESERVE}
                    or item.responsible_employee_id or item.location_id or item.room_id or item.cabinet_id or item.freeform_location
                    or item.serial_number or item.mac_address or item.hostname or item.network_address or item.network_username or item.network_password_encrypted
                    or item.origin_project_id or item.origin_project_stage_id
                    or item.loans.filter(status=EquipmentLoan.Status.ACTIVE).exists()
                ):
                    raise PermissionDenied(f"Экземпляр {item} изменился и больше не подходит для безопасного преобразования.")

            totals = {}
            for item in locked_units:
                totals[item.owner_id] = totals.get(item.owner_id, Decimal("0")) + Decimal(item.quantity)

            catalog.inventory_kind = data["inventory_kind"]
            catalog.unit_of_measure = data["unit_of_measure"]
            catalog.save(update_fields=["inventory_kind", "unit_of_measure", "updated_at"])

            for organization_id, quantity in totals.items():
                warehouse = Warehouse.objects.select_for_update().filter(
                    organization_id=organization_id, is_default=True, archived=False
                ).first()
                if warehouse is None:
                    warehouse = Warehouse.objects.create(
                        organization_id=organization_id, name="Основной склад", is_default=True
                    )
                stock, _ = MaterialStock.objects.select_for_update().get_or_create(
                    warehouse=warehouse, catalog_item=catalog, defaults={"quantity": Decimal("0")}
                )
                stock.quantity += quantity
                stock.save(update_fields=["quantity", "updated_at"])
                price = catalog.unit_price
                MaterialTransaction.objects.create(
                    warehouse=warehouse, catalog_item=catalog,
                    transaction_type=MaterialTransaction.TransactionType.CONVERSION,
                    quantity=quantity, balance_after=stock.quantity,
                    unit_price_snapshot=price,
                    line_total_snapshot=price * quantity if price is not None else None,
                    source="Преобразование из индивидуального учёта FOX Inventory 1.7",
                    note=f"Перенесено карточек: {sum(1 for x in locked_units if x.owner_id == organization_id)}",
                    created_by=request.user,
                )

            stamp = timezone.now().strftime("%d.%m.%Y %H:%M")
            for item in locked_units:
                suffix = f"[MATERIAL_CONVERSION {stamp}] Перенесено в количественный складской учёт; исходное количество: {item.quantity}."
                item.notes = (item.notes.rstrip() + "\n" + suffix).strip()
                item.archived = True
                item.save(update_fields=["notes", "archived", "updated_at"])

        messages.success(
            request,
            f"Номенклатура переведена в количественный учёт. Перенесено исторических карточек: {len(locked_units)}. "
            "Их исходные записи сохранены в архиве, остаток доступен на складе.",
        )
        return redirect(catalog)

    return render(request, "inventory/catalog_convert_material.html", {
        "object": obj,
        "form": form,
        "convertible_units": convertible_units,
        "unsafe_units": unsafe_units,
        "preview": preview,
    })


@login_required
@require_POST
def catalog_confirm_price(request, pk):
    if not request.user.is_staff:
        raise PermissionDenied
    obj = get_object_or_404(CatalogItem, pk=pk)
    if obj.unit_price is None:
        messages.error(request, "Сначала укажите учётную цену.")
    else:
        obj.price_needs_review = False
        obj.save(update_fields=["price_needs_review", "updated_at"])
        messages.success(request, "Текущая цена подтверждена.")
    return redirect(obj)


@login_required
def material_stock_list(request):
    org = request.GET.get("organization", "")
    q = request.GET.get("q", "").strip()
    qs = MaterialStock.objects.filter(warehouse__archived=False, catalog_item__archived=False).select_related(
        "warehouse", "warehouse__organization", "catalog_item", "catalog_item__category"
    )
    if org:
        qs = qs.filter(warehouse__organization_id=org)
    if q:
        qs = qs.filter(Q(catalog_item__name__icontains=q) | Q(catalog_item__manufacturer__icontains=q) | Q(catalog_item__model__icontains=q) | Q(catalog_item__sku__icontains=q))
    rows = list(qs.order_by("warehouse__organization__name", "catalog_item__name"))
    total_value = sum((row.total_value or 0 for row in rows), Decimal("0"))
    return render(request, "inventory/material_stock_list.html", {
        "objects": rows, "organizations": Organization.objects.filter(archived=False), "selected_organization": org,
        "q": q, "total_value": total_value,
    })


@login_required
def material_receipt(request):
    initial = {}
    if request.GET.get("organization"):
        initial["organization"] = request.GET["organization"]
    form = MaterialReceiptForm(request.POST or None, initial=initial)
    if form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            warehouse = Warehouse.objects.select_for_update().get(pk=data["warehouse"].pk)
            catalog = CatalogItem.objects.select_for_update().get(pk=data["catalog_item"].pk)
            stock, _ = MaterialStock.objects.select_for_update().get_or_create(warehouse=warehouse, catalog_item=catalog, defaults={"quantity": Decimal("0")})
            stock.quantity += data["quantity"]
            stock.save(update_fields=["quantity", "updated_at"])
            price = data.get("unit_price") if data.get("unit_price") is not None else catalog.unit_price
            line_total = price * data["quantity"] if price is not None else None
            MaterialTransaction.objects.create(
                warehouse=warehouse, catalog_item=catalog, transaction_type=MaterialTransaction.TransactionType.RECEIPT,
                quantity=data["quantity"], balance_after=stock.quantity, unit_price_snapshot=price,
                line_total_snapshot=line_total, source=data.get("source", ""), note=data.get("note", ""), created_by=request.user,
            )
            if data.get("update_catalog_price") and data.get("unit_price") is not None and catalog.unit_price != data["unit_price"]:
                catalog.unit_price = data["unit_price"]
                catalog.price_needs_review = False
                catalog.save(update_fields=["unit_price", "price_needs_review", "updated_at"])
                CatalogPriceHistory.objects.create(
                    catalog_item=catalog, unit_price=data["unit_price"], effective_date=timezone.localdate(),
                    source=data.get("source") or "Поступление материала", changed_by=request.user,
                )
        messages.success(request, f"Поступление проведено. Остаток: {stock.quantity:g} {catalog.get_unit_of_measure_display()}.")
        return redirect(f"{reverse('material_stock_list')}?organization={warehouse.organization_id}")
    return render(request, "inventory/material_receipt.html", {"form": form})


@login_required
def material_adjustment(request):
    if not request.user.is_staff:
        raise PermissionDenied
    form = MaterialAdjustmentForm(request.POST or None)
    if form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            stock = MaterialStock.objects.select_for_update().select_related("catalog_item", "warehouse").get(pk=data["stock"].pk)
            qty = data["quantity"]
            if data["direction"] == "minus":
                if qty > stock.quantity:
                    form.add_error("quantity", f"Остаток изменился. Доступно {stock.quantity:g}.")
                    return render(request, "inventory/material_adjustment.html", {"form": form})
                stock.quantity -= qty
                tx_type = MaterialTransaction.TransactionType.ADJUSTMENT_MINUS
            else:
                stock.quantity += qty
                tx_type = MaterialTransaction.TransactionType.ADJUSTMENT_PLUS
            stock.save(update_fields=["quantity", "updated_at"])
            price = stock.catalog_item.unit_price
            MaterialTransaction.objects.create(
                warehouse=stock.warehouse, catalog_item=stock.catalog_item, transaction_type=tx_type,
                quantity=qty, balance_after=stock.quantity, unit_price_snapshot=price,
                line_total_snapshot=price * qty if price is not None else None,
                source="Ручная корректировка остатка", note=data["reason"], created_by=request.user,
            )
        messages.success(request, f"Остаток скорректирован: {stock.quantity:g} {stock.catalog_item.get_unit_of_measure_display()}.")
        return redirect("material_stock_list")
    return render(request, "inventory/material_adjustment.html", {"form": form})


@login_required
def project_list(request):
    org = request.GET.get("organization", "")
    status = request.GET.get("status", "")
    qs = Project.objects.select_related("organization", "location", "responsible_employee").prefetch_related("stages__operations__lines")
    if org:
        qs = qs.filter(organization_id=org)
    if status in dict(Project.Status.choices):
        qs = qs.filter(status=status)
    return render(request, "inventory/project_list.html", {
        "objects": qs, "organizations": Organization.objects.filter(archived=False), "selected_organization": org,
        "selected_status": status, "statuses": Project.Status.choices,
    })


@login_required
def project_form(request, pk=None):
    obj = get_object_or_404(Project, pk=pk) if pk else None
    if obj and obj.status in {Project.Status.COMPLETED, Project.Status.ARCHIVED}:
        messages.error(request, "Завершённый или архивный проект нельзя редактировать. Сначала возобновите проект.")
        return redirect(obj)
    initial = {}
    if obj is None:
        if request.GET.get("organization"):
            initial["organization"] = request.GET["organization"]
        if request.GET.get("location"):
            initial["location"] = request.GET["location"]
    form = ProjectForm(request.POST or None, instance=obj, initial=initial)
    if form.is_valid():
        project = form.save(commit=False)
        if not project.pk:
            project.created_by = request.user
        project.save()
        messages.success(request, "Проект сохранён.")
        return redirect(project)
    return render(request, "inventory/model_form.html", {"form": form, "title": "Проект", "cancel_url": obj.get_absolute_url() if obj else reverse("project_list")})


@login_required
def project_detail(request, pk):
    obj = get_object_or_404(Project.objects.select_related("organization", "location", "responsible_employee"), pk=pk)
    stages = obj.stages.prefetch_related("operations__lines").all()
    material_cost = Decimal("0")
    equipment_cost = Decimal("0")
    for stage in stages:
        for op in stage.operations.all():
            if op.voided_at:
                continue
            for line in op.lines.all():
                if line.line_type == ProjectOperationLine.LineType.MATERIAL:
                    material_cost += line.line_total_snapshot or 0
                else:
                    equipment_cost += line.line_total_snapshot or 0
    installed = Equipment.objects.filter(origin_project=obj, archived=False).select_related("catalog_item", "location", "room")
    return render(request, "inventory/project_detail.html", {
        "object": obj, "stages": stages, "material_cost": material_cost, "equipment_cost": equipment_cost,
        "total_cost": material_cost + equipment_cost, "installed_equipment": installed,
    })


@login_required
def project_stage_form(request, project_pk, pk=None):
    project = get_object_or_404(Project, pk=project_pk)
    obj = get_object_or_404(ProjectStage, pk=pk, project=project) if pk else None
    if project.status in {Project.Status.COMPLETED, Project.Status.ARCHIVED}:
        messages.error(request, "Нельзя менять этапы завершённого или архивного проекта. Сначала возобновите проект.")
        return redirect(project)
    if obj and obj.status == ProjectStage.Status.COMPLETED:
        messages.error(request, "Завершённый этап нельзя редактировать. Сначала возобновите этап.")
        return redirect(obj)
    initial = {"number": (project.stages.order_by("-number").values_list("number", flat=True).first() or 0) + 1}
    form = ProjectStageForm(request.POST or None, instance=obj, initial=initial)
    if form.is_valid():
        stage = form.save(commit=False)
        stage.project = project
        stage.save()
        messages.success(request, "Этап сохранён.")
        return redirect(stage)
    return render(request, "inventory/model_form.html", {"form": form, "title": "Этап проекта", "cancel_url": obj.get_absolute_url() if obj else project.get_absolute_url()})


@login_required
def project_stage_detail(request, pk):
    obj = get_object_or_404(ProjectStage.objects.select_related("project__organization", "project__location"), pk=pk)
    operations = obj.operations.select_related("created_by").prefetch_related("lines__catalog_item", "lines__equipment", "lines__warehouse")
    material_lines = ProjectOperationLine.objects.filter(operation__stage=obj, operation__voided_at__isnull=True, line_type=ProjectOperationLine.LineType.MATERIAL).select_related("catalog_item")
    equipment_lines = ProjectOperationLine.objects.filter(operation__stage=obj, operation__voided_at__isnull=True, line_type=ProjectOperationLine.LineType.EQUIPMENT).select_related("catalog_item", "equipment")
    return render(request, "inventory/project_stage_detail.html", {
        "object": obj, "operations": operations, "material_lines": material_lines, "equipment_lines": equipment_lines,
        "material_cost": sum((x.line_total_snapshot or 0 for x in material_lines), Decimal("0")),
        "equipment_cost": sum((x.line_total_snapshot or 0 for x in equipment_lines), Decimal("0")),
    })


@login_required
def project_stage_add_items(request, pk):
    stage = get_object_or_404(ProjectStage.objects.select_related("project__organization", "project__location"), pk=pk)
    if stage.project.status in {Project.Status.COMPLETED, Project.Status.ARCHIVED}:
        messages.error(request, "Проект завершён или архивирован. Сначала возобновите проект.")
        return redirect(stage.project)
    if stage.status == ProjectStage.Status.COMPLETED:
        messages.error(request, "Завершённый этап нельзя изменять. Возобновите его или создайте новый этап.")
        return redirect(stage)
    form = ProjectStageOperationForm(request.POST or None, stage=stage)
    material_rows = [{"stock": stock, "field": form[f"material_{stock.pk}"]} for stock in form.material_stocks]
    selected_equipment_ids = set(request.POST.getlist("equipment")) if request.method == "POST" else set()
    equipment_rows = []
    for item in form.fields["equipment"].queryset:
        equipment_rows.append({"item": item, "checked": str(item.pk) in selected_equipment_ids})
    if form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            locked_stage = ProjectStage.objects.select_for_update().select_related("project__organization", "project__location").get(pk=stage.pk)
            if locked_stage.status == ProjectStage.Status.COMPLETED:
                raise PermissionDenied("Этап уже завершён.")
            if locked_stage.project.status in {Project.Status.COMPLETED, Project.Status.ARCHIVED}:
                raise PermissionDenied("Проект уже завершён или архивирован.")
            op = ProjectOperation.objects.create(stage=locked_stage, operation_date=data["operation_date"], note=data.get("note", ""), created_by=request.user)
            for stock, qty in form.selected_materials:
                locked = MaterialStock.objects.select_for_update().select_related("catalog_item", "warehouse").get(pk=stock.pk)
                if qty > locked.quantity:
                    raise ValueError(f"Недостаточный остаток {locked.catalog_item}")
                price = locked.catalog_item.unit_price
                total = price * qty
                locked.quantity -= qty
                locked.save(update_fields=["quantity", "updated_at"])
                line = ProjectOperationLine.objects.create(
                    operation=op, line_type=ProjectOperationLine.LineType.MATERIAL, catalog_item=locked.catalog_item,
                    warehouse=locked.warehouse, quantity=qty, item_name_snapshot=locked.catalog_item.name,
                    unit_snapshot=locked.catalog_item.get_unit_of_measure_display(), unit_price_snapshot=price,
                    line_total_snapshot=total,
                )
                MaterialTransaction.objects.create(
                    warehouse=locked.warehouse, catalog_item=locked.catalog_item,
                    transaction_type=MaterialTransaction.TransactionType.PROJECT_WRITE_OFF, quantity=qty,
                    balance_after=locked.quantity, unit_price_snapshot=price, line_total_snapshot=total,
                    source=f"{locked_stage.project.name} / Этап {locked_stage.number}", note=data.get("note", ""),
                    project_line=line, created_by=request.user,
                )
            for equipment in data.get("equipment", []):
                item = Equipment.objects.select_for_update().select_related("catalog_item", "owner").get(pk=equipment.pk)
                if item.owner_id != locked_stage.project.organization_id or item.usage_status not in [Equipment.UsageStatus.STOCK, Equipment.UsageStatus.RESERVE] or item.responsible_employee_id:
                    raise ValueError(f"Оборудование {item} уже недоступно для установки")
                old_status = item.usage_status
                price = item.unit_price
                previous_state = {
                    "usage_status": item.usage_status,
                    "responsible_employee_id": item.responsible_employee_id,
                    "location_id": item.location_id,
                    "room_id": item.room_id,
                    "cabinet_id": item.cabinet_id,
                    "freeform_location": item.freeform_location,
                    "origin_project_id": item.origin_project_id,
                    "origin_project_stage_id": item.origin_project_stage_id,
                }
                item.responsible_employee = None
                item.location = locked_stage.project.location
                item.room = data.get("room")
                item.cabinet = data.get("cabinet")
                item.freeform_location = data.get("freeform_location", "")
                item.usage_status = Equipment.UsageStatus.OBJECT
                if not item.origin_project_id:
                    item.origin_project = locked_stage.project
                    item.origin_project_stage = locked_stage
                installed_state = {
                    "usage_status": item.usage_status,
                    "responsible_employee_id": item.responsible_employee_id,
                    "location_id": item.location_id,
                    "room_id": item.room_id,
                    "cabinet_id": item.cabinet_id,
                    "freeform_location": item.freeform_location,
                    "origin_project_id": item.origin_project_id,
                    "origin_project_stage_id": item.origin_project_stage_id,
                }
                ProjectOperationLine.objects.create(
                    operation=op, line_type=ProjectOperationLine.LineType.EQUIPMENT, catalog_item=item.catalog_item,
                    equipment=item, quantity=Decimal("1"), item_name_snapshot=item.display_name,
                    unit_snapshot="шт.", unit_price_snapshot=price, line_total_snapshot=price,
                    equipment_previous_state=previous_state, equipment_installed_state=installed_state,
                )
                item.save()
                EquipmentMovement.objects.create(
                    equipment=item, movement_type=EquipmentMovement.MovementType.PROJECT_INSTALLED,
                    from_status=old_status, to_status=item.usage_status, project_stage=locked_stage,
                    notes=f"Проект: {locked_stage.project.name}; этап {locked_stage.number}. {data.get('note', '')}".strip(), created_by=request.user,
                )
            if locked_stage.status == ProjectStage.Status.DRAFT:
                locked_stage.status = ProjectStage.Status.ACTIVE
                if not locked_stage.start_date:
                    locked_stage.start_date = data["operation_date"]
                locked_stage.save(update_fields=["status", "start_date", "updated_at"])
            project = locked_stage.project
            if project.status == Project.Status.DRAFT:
                project.status = Project.Status.ACTIVE
                if not project.start_date:
                    project.start_date = data["operation_date"]
                project.save(update_fields=["status", "start_date", "updated_at"])
        messages.success(request, f"Операция проведена. Стоимость: {op.total_cost:,.2f} ₽.")
        return redirect(stage)
    return render(request, "inventory/project_stage_add_items.html", {"object": stage, "form": form, "material_rows": material_rows, "equipment_rows": equipment_rows})


@login_required
@require_POST
def project_operation_void(request, pk):
    if not request.user.is_staff:
        raise PermissionDenied
    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        messages.error(request, "Укажите причину отмены операции.")
        op = get_object_or_404(ProjectOperation, pk=pk)
        return redirect(op.stage)
    with transaction.atomic():
        op = get_object_or_404(
            ProjectOperation.objects.select_for_update().select_related("stage__project__location"), pk=pk
        )
        if op.voided_at:
            messages.info(request, "Операция уже отменена.")
            return redirect(op.stage)
        if op.stage.project.status in {Project.Status.COMPLETED, Project.Status.ARCHIVED}:
            messages.error(request, "Завершённый или архивный проект защищён от изменений. Сначала возобновите проект.")
            return redirect(op.stage)
        if op.stage.status == ProjectStage.Status.COMPLETED:
            messages.error(request, "Завершённый этап защищён от изменений. Сначала возобновите этап.")
            return redirect(op.stage)
        lines = list(op.lines.select_related("equipment", "catalog_item", "warehouse"))
        # Сначала проверяем оборудование. Если оно уже ушло дальше, автоматический откат небезопасен.
        for line in lines:
            if line.line_type != ProjectOperationLine.LineType.EQUIPMENT or not line.equipment_id:
                continue
            item = Equipment.objects.select_for_update().get(pk=line.equipment_id)
            installed = line.equipment_installed_state or {}
            current_state = {
                "usage_status": item.usage_status,
                "responsible_employee_id": item.responsible_employee_id,
                "location_id": item.location_id,
                "room_id": item.room_id,
                "cabinet_id": item.cabinet_id,
                "freeform_location": item.freeform_location,
                "origin_project_id": item.origin_project_id,
                "origin_project_stage_id": item.origin_project_stage_id,
            }
            if current_state != installed or item.loans.filter(status=EquipmentLoan.Status.ACTIVE).exists():
                messages.error(
                    request,
                    f"Нельзя отменить операцию автоматически: {item} после установки уже меняло состояние или местоположение. "
                    "Сначала разберите последующие движения оборудования.",
                )
                return redirect(op.stage)
        for line in lines:
            if line.line_type == ProjectOperationLine.LineType.MATERIAL:
                stock, _ = MaterialStock.objects.select_for_update().get_or_create(
                    warehouse=line.warehouse,
                    catalog_item=line.catalog_item,
                    defaults={"quantity": Decimal("0")},
                )
                stock.quantity += line.quantity
                stock.save(update_fields=["quantity", "updated_at"])
                MaterialTransaction.objects.create(
                    warehouse=stock.warehouse,
                    catalog_item=stock.catalog_item,
                    transaction_type=MaterialTransaction.TransactionType.ADJUSTMENT_PLUS,
                    quantity=line.quantity,
                    balance_after=stock.quantity,
                    unit_price_snapshot=line.unit_price_snapshot,
                    line_total_snapshot=line.line_total_snapshot,
                    source=f"Отмена проектной операции #{op.pk}",
                    note=reason,
                    created_by=request.user,
                )
            elif line.equipment_id:
                item = Equipment.objects.select_for_update().get(pk=line.equipment_id)
                old_status = item.usage_status
                state = line.equipment_previous_state or {}
                item.usage_status = state.get("usage_status") or Equipment.UsageStatus.STOCK
                item.responsible_employee_id = state.get("responsible_employee_id")
                item.location_id = state.get("location_id")
                item.room_id = state.get("room_id")
                item.cabinet_id = state.get("cabinet_id")
                item.freeform_location = state.get("freeform_location", "")
                item.origin_project_id = state.get("origin_project_id")
                item.origin_project_stage_id = state.get("origin_project_stage_id")
                item.save()
                EquipmentMovement.objects.create(
                    equipment=item,
                    movement_type=EquipmentMovement.MovementType.PROJECT_ROLLBACK,
                    from_status=old_status,
                    to_status=item.usage_status,
                    project_stage=op.stage,
                    notes=f"Отмена операции проекта #{op.pk}: {reason}",
                    created_by=request.user,
                )
        op.voided_at = timezone.now()
        op.voided_by = request.user
        op.void_reason = reason
        op.save(update_fields=["voided_at", "voided_by", "void_reason", "updated_at"])
    messages.success(request, "Операция отменена корректирующей записью. Материалы возвращены на склад, оборудование восстановлено в точное предыдущее состояние.")
    return redirect(op.stage)


@login_required
@require_POST
def project_stage_complete(request, pk):
    with transaction.atomic():
        stage = get_object_or_404(ProjectStage.objects.select_for_update().select_related("project"), pk=pk)
        if stage.project.status in {Project.Status.COMPLETED, Project.Status.ARCHIVED}:
            messages.error(request, "Нельзя менять этап завершённого или архивного проекта.")
            return redirect(stage)
        if stage.status == ProjectStage.Status.COMPLETED:
            messages.info(request, "Этап уже завершён.")
            return redirect(stage)
        if not stage.operations.filter(voided_at__isnull=True).exists():
            messages.error(request, "Нельзя завершить этап без проведённых операций.")
            return redirect(stage)
        stage.status = ProjectStage.Status.COMPLETED
        stage.completed_at = timezone.localdate()
        stage.save(update_fields=["status", "completed_at", "updated_at"])
    messages.success(request, "Этап завершён и зафиксирован. Для изменений потребуется явное возобновление.")
    return redirect(stage)


@login_required
@require_POST
def project_stage_reopen(request, pk):
    if not request.user.is_staff:
        raise PermissionDenied
    with transaction.atomic():
        stage = get_object_or_404(ProjectStage.objects.select_for_update().select_related("project"), pk=pk)
        if stage.project.status in {Project.Status.COMPLETED, Project.Status.ARCHIVED}:
            messages.error(request, "Сначала возобновите проект, затем этап.")
            return redirect(stage)
        if stage.status != ProjectStage.Status.COMPLETED:
            messages.info(request, "Этап уже открыт для работы.")
            return redirect(stage)
        stage.status = ProjectStage.Status.ACTIVE
        stage.completed_at = None
        stage.save(update_fields=["status", "completed_at", "updated_at"])
    messages.success(request, "Этап возобновлён.")
    return redirect(stage)


@login_required
@require_POST
def project_complete(request, pk):
    with transaction.atomic():
        project = get_object_or_404(Project.objects.select_for_update(), pk=pk)
        if project.status == Project.Status.ARCHIVED:
            messages.error(request, "Архивный проект нельзя изменять.")
            return redirect(project)
        if project.status == Project.Status.COMPLETED:
            messages.info(request, "Проект уже завершён.")
            return redirect(project)
        if project.stages.exclude(status=ProjectStage.Status.COMPLETED).exists() or not project.stages.exists():
            messages.error(request, "Сначала завершите все этапы проекта.")
            return redirect(project)
        project.status = Project.Status.COMPLETED
        project.end_date = timezone.localdate()
        project.save(update_fields=["status", "end_date", "updated_at"])
    messages.success(request, "Проект завершён и переведён в режим только для чтения.")
    return redirect(project)


@login_required
@require_POST
def project_reopen(request, pk):
    if not request.user.is_staff:
        raise PermissionDenied
    with transaction.atomic():
        project = get_object_or_404(Project.objects.select_for_update(), pk=pk)
        if project.status == Project.Status.ARCHIVED:
            messages.error(request, "Архивный проект нельзя возобновить этой операцией.")
            return redirect(project)
        if project.status != Project.Status.COMPLETED:
            messages.info(request, "Проект уже открыт для работы.")
            return redirect(project)
        project.status = Project.Status.ACTIVE
        project.end_date = None
        project.save(update_fields=["status", "end_date", "updated_at"])
    messages.success(request, "Проект возобновлён. Завершённые этапы остаются защищёнными до отдельного возобновления.")
    return redirect(project)


@login_required
def archive_list(request):
    employees = Employee.objects.filter(archived=True).select_related("organization").annotate(
        equipment_count=Count("equipment", distinct=True)
    )
    equipment = Equipment.objects.filter(archived=True).select_related(
        "category", "owner", "responsible_employee"
    )
    organizations = Organization.objects.filter(archived=True).annotate(
        equipment_count=Count("owned_equipment", distinct=True),
        employee_count=Count("employees", distinct=True),
    )
    return render(request, "inventory/archive_list.html", {
        "employees": employees,
        "equipment": equipment,
        "organizations": organizations,
    })


@login_required
def equipment_list(request):
    qs = Equipment.objects.select_related("category", "catalog_item", "owner", "responsible_employee", "location", "room", "cabinet").filter(archived=False)
    q = request.GET.get("q", "").strip(); owner = request.GET.get("owner", ""); category = request.GET.get("category", "")
    status = request.GET.get("status", ""); group = request.GET.get("group", ""); location = request.GET.get("location", "")
    if q:
        qs = qs.filter(Q(internal_code__icontains=q) | Q(name__icontains=q) | Q(manufacturer__icontains=q)
                       | Q(model__icontains=q) | Q(serial_number__icontains=q) | Q(mac_address__icontains=q) | Q(hostname__icontains=q) | Q(network_address__icontains=q)
                       | Q(responsible_employee__full_name__icontains=q))
    if owner: qs = qs.filter(owner_id=owner)
    if category: qs = qs.filter(category_id=category)
    if status: qs = qs.filter(usage_status=status)
    if location: qs = qs.filter(Q(location_id=location) | Q(cabinet__location_id=location) | Q(responsible_employee__workplace_location_id=location)).distinct()
    if group in dict(Equipment.AccountingGroup.choices): qs = qs.filter(accounting_group=group)
    qs, sort, direction = _apply_sorting(qs, request, {
        "group": ("accounting_group", Lower("internal_code"), Lower("name")),
        "code": (Coalesce(Lower("internal_code"), Value("")), Lower("name")),
        "name": (Lower("name"), Coalesce(Lower("manufacturer"), Value("")), Coalesce(Lower("model"), Value(""))),
        "assignment": (Coalesce(Lower("responsible_employee__full_name"), Lower("room__name"), Lower("location__label"), Lower("location__address"), Lower("cabinet__name"), Lower("freeform_location"), Value("")), Lower("name")),
        "status": ("usage_status", Lower("name")),
        "condition": ("condition", Lower("name")),
    }, "code")
    page_obj, query_string = _paginate(request, qs, 60)
    return render(request, "inventory/equipment_list.html", {
        "objects": page_obj.object_list, "page_obj": page_obj, "query_string": query_string,
        "q": q, "selected_owner": owner, "selected_category": category, "selected_status": status,
        "selected_group": group, "selected_location": location, "group_choices": Equipment.AccountingGroup.choices,
        "owners": Organization.objects.filter(archived=False), "categories": Category.objects.filter(archived=False),
        "locations": Location.objects.filter(archived=False).select_related("organization"), "statuses": Equipment.UsageStatus.choices,
        "sort": sort, "direction": direction,
    })

@login_required
def equipment_detail(request, pk):
    obj = get_object_or_404(Equipment.objects.select_related("category", "catalog_item", "owner", "responsible_employee__organization", "location", "room", "cabinet"), pk=pk)
    back_url = _remember_back_url(request, "equipment_list_back_url", reverse("equipment_list"))
    loans = obj.loans.select_related("borrower", "responsible_employee").all()
    return render(request, "inventory/equipment_detail.html", {
        "object": obj,
        "movements": _visible_movements(obj.movements.select_related("from_employee", "to_employee", "from_organization", "to_organization", "created_by", "act"))[:100],
        "acts": obj.acts.select_related("employee").all(),
        "loans": loans,
        "active_loan": loans.filter(status=EquipmentLoan.Status.ACTIVE).first(),
        "repairs": obj.repairs.all(),
        "documents": obj.document_records.filter(trashed_at__isnull=True).select_related("document_type", "counterparty", "contract", "organization"),
        "back_url": back_url,
    })


@login_required
def equipment_form(request, pk=None):
    obj = get_object_or_404(Equipment, pk=pk) if pk else None
    previous = None
    if obj:
        previous = {"employee": obj.responsible_employee, "status": obj.usage_status, "owner": obj.owner}
    initial = {}
    if obj is None and request.GET.get("owner"):
        initial["owner"] = request.GET.get("owner")
    if obj is None and request.GET.get("location"):
        location = get_object_or_404(Location, pk=request.GET.get("location"), archived=False)
        initial["location"] = location.pk
        initial["owner"] = location.organization_id
        initial["usage_status"] = Equipment.UsageStatus.OBJECT
    if obj is None and request.GET.get("room"):
        room = get_object_or_404(Room, pk=request.GET.get("room"), archived=False)
        initial["room"] = room.pk
        initial["location"] = room.location_id
        initial["usage_status"] = Equipment.UsageStatus.OBJECT
    if obj is None and request.GET.get("group") in dict(Equipment.AccountingGroup.choices):
        initial["accounting_group"] = request.GET.get("group")
    form = EquipmentForm(request.POST or None, instance=obj, initial=initial)
    if form.is_valid():
        saved = form.save()
        if saved.catalog_item_id is None:
            saved.catalog_item = ensure_catalog_item(
                category=saved.category,
                accounting_group=saved.accounting_group,
                name=saved.name,
                manufacturer=saved.manufacturer,
                model=saved.model,
                changed_by=request.user,
            )
            saved.save()
        EquipmentMovement.objects.create(
            equipment=saved,
            movement_type=EquipmentMovement.MovementType.CREATED if not obj else EquipmentMovement.MovementType.EDITED,
            from_employee=previous["employee"] if previous else None,
            to_employee=saved.responsible_employee,
            from_organization=previous["owner"] if previous else None,
            to_organization=saved.owner,
            from_status=previous["status"] if previous else "",
            to_status=saved.usage_status,
            created_by=request.user,
        )
        messages.success(request, "Карточка оборудования сохранена.")
        return redirect(saved)
    return render(request, "inventory/model_form.html", {"form": form, "title": "Оборудование", "form_kind": "equipment", "cancel_url": obj.get_absolute_url() if obj else reverse("equipment_list")})


@login_required
def equipment_assign(request, pk):
    obj = get_object_or_404(Equipment, pk=pk)
    initial = {"employee": obj.responsible_employee, "status": obj.usage_status, "location": obj.location, "room": obj.room, "cabinet": obj.cabinet, "freeform_location": obj.freeform_location}
    form = AssignmentForm(request.POST or None, initial=initial)
    if form.is_valid():
        old_employee = obj.responsible_employee
        old_status = obj.usage_status
        obj.responsible_employee = form.cleaned_data["employee"]
        obj.usage_status = form.cleaned_data["status"]
        obj.location = form.cleaned_data["location"]
        obj.room = form.cleaned_data["room"]
        obj.cabinet = form.cleaned_data["cabinet"]
        obj.freeform_location = form.cleaned_data["freeform_location"]
        obj.save()
        movement_type = EquipmentMovement.MovementType.ASSIGNED if obj.responsible_employee else EquipmentMovement.MovementType.RETURNED
        EquipmentMovement.objects.create(
            equipment=obj, movement_type=movement_type, from_employee=old_employee, to_employee=obj.responsible_employee,
            from_status=old_status, to_status=obj.usage_status, notes=form.cleaned_data["notes"], created_by=request.user,
        )
        if old_employee and obj.responsible_employee_id and old_employee.id != obj.responsible_employee_id and obj.condition == Equipment.Condition.NEW:
            obj.condition = Equipment.Condition.USED
            obj.save(update_fields=["condition", "updated_at"])
        messages.success(request, "Назначение оборудования обновлено.")
        return redirect(obj)
    return render(request, "inventory/model_form.html", {"form": form, "title": f"Перемещение: {obj}", "cancel_url": obj.get_absolute_url()})


@login_required
def equipment_loan(request, pk):
    obj = get_object_or_404(Equipment, pk=pk)
    if obj.loans.filter(status=EquipmentLoan.Status.ACTIVE).exists():
        messages.error(request, "У этого оборудования уже есть активная временная передача.")
        return redirect(obj)
    form = LoanForm(request.POST or None, request.FILES or None, equipment=obj)
    if form.is_valid():
        with transaction.atomic():
            old_status = obj.usage_status
            old_employee = obj.responsible_employee
            loan = form.save(commit=False)
            loan.equipment = obj
            loan.lender = obj.owner
            loan.previous_state = {
                "usage_status": obj.usage_status,
                "responsible_employee_id": obj.responsible_employee_id,
                "location_id": obj.location_id,
                "room_id": obj.room_id,
                "cabinet_id": obj.cabinet_id,
                "freeform_location": obj.freeform_location,
            }
            loan.save()
            obj.usage_status = Equipment.UsageStatus.LOANED
            obj.responsible_employee = loan.responsible_employee
            obj.location = None
            obj.room = None
            obj.cabinet = None
            obj.freeform_location = ""
            obj.save()
            EquipmentMovement.objects.create(
                equipment=obj, movement_type=EquipmentMovement.MovementType.LOANED,
                from_employee=old_employee, from_organization=obj.owner,
                to_organization=loan.borrower, to_employee=loan.responsible_employee,
                from_status=old_status, to_status=obj.usage_status,
                notes=("Без документов. " if loan.undocumented else "") + loan.notes, created_by=request.user,
            )
        messages.success(request, "Временная передача зарегистрирована.")
        return redirect(obj)
    return render(request, "inventory/model_form.html", {"form": form, "title": f"Передать: {obj}", "cancel_url": obj.get_absolute_url(), "multipart": True})


@login_required
@require_POST
def loan_return(request, pk):
    loan = get_object_or_404(
        EquipmentLoan.objects.select_related("equipment", "borrower", "lender"),
        pk=pk, status=EquipmentLoan.Status.ACTIVE,
    )
    with transaction.atomic():
        obj = Equipment.objects.select_for_update().get(pk=loan.equipment_id)
        old_status = obj.usage_status
        old_employee = obj.responsible_employee
        state = loan.previous_state or {}
        obj.usage_status = state.get("usage_status") or Equipment.UsageStatus.STOCK
        obj.responsible_employee_id = state.get("responsible_employee_id")
        obj.location_id = state.get("location_id")
        obj.room_id = state.get("room_id")
        obj.cabinet_id = state.get("cabinet_id")
        obj.freeform_location = state.get("freeform_location", "")
        obj.save()
        loan.status = EquipmentLoan.Status.RETURNED
        loan.returned_at = timezone.localdate()
        loan.save(update_fields=["status", "returned_at", "updated_at"])
        EquipmentMovement.objects.create(
            equipment=obj, movement_type=EquipmentMovement.MovementType.LOAN_RETURN,
            from_employee=old_employee, to_employee=obj.responsible_employee,
            from_organization=loan.borrower, to_organization=loan.lender,
            from_status=old_status, to_status=obj.usage_status, created_by=request.user,
        )
    messages.success(request, "Оборудование возвращено в предыдущее состояние.")
    return redirect(obj)


@login_required
@require_POST
def equipment_archive(request, pk):
    obj = get_object_or_404(Equipment, pk=pk)
    if not obj.archived:
        reasons = []
        if obj.loans.filter(status=EquipmentLoan.Status.ACTIVE).exists():
            reasons.append("есть активная временная передача")
        if obj.responsible_employee_id:
            reasons.append("оборудование закреплено за сотрудником")
        if obj.usage_status not in {
            Equipment.UsageStatus.STOCK,
            Equipment.UsageStatus.RESERVE,
            Equipment.UsageStatus.WAITING_DISPOSAL,
            Equipment.UsageStatus.DISPOSED,
        }:
            reasons.append(f"текущий статус: {obj.get_usage_status_display()}")
        if reasons:
            messages.error(request, "Нельзя переместить в архив: " + "; ".join(reasons) + ".")
            return redirect(obj)
    obj.archived = not obj.archived
    obj.save(update_fields=["archived", "updated_at"])
    messages.success(request, "Архивный статус оборудования изменён.")
    return redirect(obj)


@login_required
def reveal_network_password(request, pk):
    if not request.user.is_staff:
        raise PermissionDenied
    obj = get_object_or_404(Equipment, pk=pk)
    return JsonResponse({"password": obj.get_network_password()})


@login_required
def repair_form(request, equipment_pk, pk=None):
    equipment = get_object_or_404(Equipment, pk=equipment_pk)
    obj = get_object_or_404(RepairRecord, pk=pk, equipment=equipment) if pk else None
    form = RepairForm(request.POST or None, instance=obj)
    if form.is_valid():
        repair = form.save(commit=False)
        repair.equipment = equipment
        repair.save()
        equipment.usage_status = Equipment.UsageStatus.REPAIR if repair.status == RepairRecord.Status.OPEN else Equipment.UsageStatus.STOCK
        if repair.status == RepairRecord.Status.UNREPAIRABLE:
            equipment.condition = Equipment.Condition.BROKEN
            equipment.usage_status = Equipment.UsageStatus.WAITING_DISPOSAL
        equipment.save()
        EquipmentMovement.objects.create(equipment=equipment, movement_type=EquipmentMovement.MovementType.REPAIR, to_status=equipment.usage_status, notes=repair.problem, created_by=request.user)
        messages.success(request, "Запись ремонта сохранена.")
        return redirect(equipment)
    return render(request, "inventory/model_form.html", {"form": form, "title": f"Ремонт: {equipment}", "cancel_url": equipment.get_absolute_url()})


@login_required
def act_list(request):
    qs = Act.objects.select_related("employee", "from_organization", "to_organization").prefetch_related("equipment")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(employee__full_name__icontains=q) | Q(equipment__internal_code__icontains=q)).distinct()
    qs = qs.annotate(equipment_count=Count("equipment", distinct=True))
    qs, sort, direction = _apply_sorting(qs, request, {
        "date": ("act_date", "created_at"),
        "number": (Coalesce(Lower("number"), Value("")), "act_date"),
        "type": ("act_type", "act_date"),
        "employee": (Coalesce(Lower("employee__full_name"), Value("")), "act_date"),
        "equipment": (F("equipment_count"), "act_date"),
    }, "date", "desc")
    return render(request, "inventory/act_list.html", {"objects": qs, "q": q, "sort": sort, "direction": direction})


@login_required
def act_detail(request, pk):
    obj = get_object_or_404(
        Act.objects.select_related("employee", "from_organization", "to_organization")
        .prefetch_related("equipment", "items__equipment"),
        pk=pk,
    )
    act_items = list(obj.items.all())
    priced_items = [item for item in act_items if item.line_total is not None]
    act_known_total = sum((item.line_total for item in priced_items), Decimal("0.00"))
    act_missing_price_count = len(act_items) - len(priced_items)
    act_total = act_known_total if act_items and not act_missing_price_count else None
    return render(request, "inventory/act_detail.html", {
        "object": obj,
        "act_items": act_items,
        "act_total": act_total,
        "act_known_total": act_known_total,
        "act_missing_price_count": act_missing_price_count,
        "movements": _visible_movements(obj.movements.select_related("equipment", "from_employee", "to_employee")),
        "auto_download": request.GET.get("download") == "1",
    })


def _ensure_staff(request):
    if not request.user.is_staff:
        raise PermissionDenied


def _delete_acts(acts):
    with transaction.atomic():
        for act in acts:
            act.delete()


@login_required
def act_delete(request, pk):
    _ensure_staff(request)
    obj = get_object_or_404(
        Act.objects.select_related("employee", "from_organization", "to_organization")
        .prefetch_related("equipment"),
        pk=pk,
    )
    if request.method == "POST":
        label = str(obj)
        _delete_acts([obj])
        messages.success(
            request,
            f"{label} удалён. Текущее закрепление оборудования и история операций не изменялись.",
        )
        return redirect("act_list")
    return render(request, "inventory/act_confirm_delete.html", {
        "objects": [obj],
        "single_object": obj,
        "bulk": False,
    })


@login_required
@require_POST
def act_bulk_delete(request):
    _ensure_staff(request)
    ids = []
    for value in request.POST.getlist("acts"):
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    acts = list(
        Act.objects.filter(pk__in=ids)
        .select_related("employee", "from_organization", "to_organization")
        .prefetch_related("equipment")
        .order_by("-act_date", "-created_at")
    )
    if not acts:
        messages.warning(request, "Не выбрано ни одного акта.")
        return redirect("act_list")
    if request.POST.get("confirm") != "1":
        return render(request, "inventory/act_confirm_delete.html", {
            "objects": acts,
            "bulk": True,
        })
    count = len(acts)
    _delete_acts(acts)
    messages.success(
        request,
        f"Удалено актов: {count}. Текущее закрепление оборудования и история операций не изменялись.",
    )
    return redirect("act_list")


@login_required
def act_download(request, pk):
    obj = get_object_or_404(Act, pk=pk)
    if not obj.document or not default_storage.exists(obj.document.name):
        raise Http404
    content_type = mimetypes.guess_type(obj.document.name)[0] or "application/octet-stream"
    response = FileResponse(default_storage.open(obj.document.name, "rb"), content_type=content_type)
    filename = Path(obj.document.name).name
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
    return response


@login_required
def act_form(request, pk=None):
    obj = get_object_or_404(Act, pk=pk) if pk else None
    initial = {}
    if obj is None and request.method == "GET" and request.GET.get("employee"):
        initial["employee"] = request.GET.get("employee")
    form = ActForm(request.POST or None, request.FILES or None, instance=obj, initial=initial)
    if form.is_valid():
        with transaction.atomic():
            act = form.save()
            if obj is None:
                act_equipment = list(act.equipment.select_related("catalog_item", "category").all())
                price_overrides = (
                    return_price_overrides(act.employee, act_equipment, act.act_date)
                    if act.act_type == Act.ActType.RETURN and act.employee
                    else {}
                )
                snapshot_act_items(act, act_equipment, price_overrides=price_overrides)
            if form.cleaned_data.get("apply_to_current_state") and act.act_type in {Act.ActType.ISSUE, Act.ActType.RETURN} and act.employee:
                for equipment in act.equipment.all():
                    old_employee = equipment.responsible_employee
                    old_status = equipment.usage_status
                    if act.act_type == Act.ActType.ISSUE:
                        equipment.responsible_employee = act.employee
                        equipment.usage_status = Equipment.UsageStatus.EMPLOYEE
                        movement_type = EquipmentMovement.MovementType.ASSIGNED
                    else:
                        equipment.responsible_employee = None
                        equipment.usage_status = Equipment.UsageStatus.STOCK
                        movement_type = EquipmentMovement.MovementType.RETURNED
                    equipment.save()
                    EquipmentMovement.objects.create(
                        equipment=equipment, movement_type=movement_type,
                        from_employee=old_employee, to_employee=equipment.responsible_employee,
                        from_status=old_status, to_status=equipment.usage_status,
                        notes=f"По акту {act.number or act.pk}", created_by=request.user, act=act,
                    )
        messages.success(request, "Акт сохранён и связан с оборудованием.")
        return redirect(act)
    return render(request, "inventory/model_form.html", {"form": form, "title": "Акт", "cancel_url": obj.get_absolute_url() if obj else reverse("act_list"), "multipart": True})


def public_act(request, token):
    obj = get_object_or_404(
        Act.objects.select_related("employee").prefetch_related("equipment", "items__equipment"),
        public_token=token,
        public_enabled=True,
    )
    act_items = list(obj.items.all())
    priced_items = [item for item in act_items if item.line_total is not None]
    known_total = sum((item.line_total for item in priced_items), Decimal("0.00"))
    missing_price_count = len(act_items) - len(priced_items)
    return render(request, "inventory/public_act.html", {
        "object": obj,
        "act_items": act_items,
        "act_known_total": known_total,
        "act_missing_price_count": missing_price_count,
    })


def public_act_file(request, token):
    obj = get_object_or_404(Act, public_token=token, public_enabled=True)
    if not obj.document or not default_storage.exists(obj.document.name):
        raise Http404
    content_type = mimetypes.guess_type(obj.document.name)[0] or "application/octet-stream"
    response = FileResponse(default_storage.open(obj.document.name, "rb"), content_type=content_type)
    response["Content-Disposition"] = f'inline; filename="{Path(obj.document.name).name}"'
    return response


@login_required
def equipment_preview(request, pk):
    obj = get_object_or_404(
        Equipment.objects.select_related("category", "catalog_item", "owner", "responsible_employee", "location", "room", "cabinet"), pk=pk
    )
    return render(request, "inventory/_equipment_preview.html", {"object": obj})


@login_required
def global_search(request):
    q = request.GET.get("q", "").strip()
    equipment = catalog = employees = locations = rooms = projects = acts = contracts = documents = []
    if q:
        equipment = Equipment.objects.filter(archived=False).filter(
            Q(internal_code__icontains=q) | Q(name__icontains=q) | Q(model__icontains=q)
            | Q(catalog_item__name__icontains=q) | Q(catalog_item__manufacturer__icontains=q)
            | Q(catalog_item__model__icontains=q) | Q(catalog_item__sku__icontains=q)
            | Q(serial_number__icontains=q) | Q(mac_address__icontains=q) | Q(hostname__icontains=q) | Q(network_address__icontains=q)
        ).select_related("category", "catalog_item", "owner", "responsible_employee")[:20]
        catalog = CatalogItem.objects.filter(archived=False).filter(
            Q(name__icontains=q) | Q(manufacturer__icontains=q) | Q(model__icontains=q) | Q(sku__icontains=q)
        ).select_related("category")[:10]
        employees = Employee.objects.filter(archived=False).filter(
            Q(full_name__icontains=q) | Q(position__icontains=q) | Q(phone__icontains=q)
        ).select_related("organization", "workplace_location")[:20]
        locations = Location.objects.filter(archived=False).filter(
            Q(label__icontains=q) | Q(address__icontains=q) | Q(organization__name__icontains=q)
        ).select_related("organization")[:10]
        rooms = Room.objects.filter(archived=False).filter(
            Q(name__icontains=q) | Q(floor__icontains=q) | Q(location__label__icontains=q) | Q(location__address__icontains=q)
        ).select_related("location", "location__organization")[:10]
        projects = Project.objects.filter(
            Q(name__icontains=q) | Q(code__icontains=q) | Q(location__label__icontains=q) | Q(location__address__icontains=q)
        ).select_related("organization", "location")[:10]
        acts = Act.objects.filter(Q(number__icontains=q) | Q(employee__full_name__icontains=q)).select_related("employee")[:10]
        contracts = Contract.objects.filter(archived=False).filter(
            Q(title__icontains=q) | Q(number__icontains=q) | Q(counterparty__name__icontains=q)
        ).select_related("organization", "counterparty")[:10]
        documents = DocumentRecord.objects.filter(trashed_at__isnull=True).filter(
            Q(title__icontains=q) | Q(number__icontains=q) | Q(original_name__icontains=q)
            | Q(counterparty__name__icontains=q) | Q(contract__title__icontains=q)
        ).select_related("organization", "document_type", "counterparty", "contract")[:10]
    return render(request, "inventory/search_results.html", {
        "q": q, "equipment": equipment, "catalog": catalog, "employees": employees, "locations": locations, "rooms": rooms,
        "projects": projects, "acts": acts, "contracts": contracts, "documents": documents,
    })


@login_required
def control_center(request):
    return render(request, "inventory/control_center.html", _attention_snapshot(limit=100))


@login_required
def import_view(request):
    form = ImportForm(request.POST or None, request.FILES or None)
    result = None
    if form.is_valid():
        created, updated, errors = import_equipment(form.cleaned_data["file"], request.user)
        result = {"created": created, "updated": updated, "errors": errors}
        if errors:
            messages.warning(request, f"Импорт завершён с ошибками: {len(errors)}.")
        else:
            messages.success(request, "Импорт завершён.")
    return render(request, "inventory/import.html", {"form": form, "result": result})


@login_required
def import_template(request):
    return FileResponse(import_template_workbook(), as_attachment=True, filename="fox_inventory_import_template.xlsx")


@login_required
def export_equipment(request):
    qs = Equipment.objects.filter(archived=False)
    return FileResponse(equipment_export_workbook(qs), as_attachment=True, filename=f"fox_inventory_{timezone.localdate():%Y-%m-%d}.xlsx")
