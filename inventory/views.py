import mimetypes
from pathlib import Path
from urllib.parse import quote
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, F, Q, Value
from django.db.models.functions import Coalesce, Lower
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
    ActForm, AssignmentForm, CabinetForm, CategoryForm, EmployeeActDocumentForm,
    EmployeeEquipmentActWorkflowForm, EmployeeEquipmentOperationForm, EmployeeForm, EquipmentForm, ImportForm, LoanForm,
    LocationForm, OrganizationForm, RepairForm, RoomForm, RoomEquipmentAssignForm,
)
from .models import Act, Cabinet, Category, Employee, Equipment, EquipmentLoan, EquipmentMovement, Location, Organization, RepairRecord, Room
from .services import equipment_export_workbook, import_equipment, import_template_workbook
from .documents import build_employee_transfer_docx, short_person_name


BUSINESS_MOVEMENT_TYPES = [
    EquipmentMovement.MovementType.ASSIGNED,
    EquipmentMovement.MovementType.RETURNED,
    EquipmentMovement.MovementType.INSTALLED,
    EquipmentMovement.MovementType.LOANED,
    EquipmentMovement.MovementType.LOAN_RETURN,
    EquipmentMovement.MovementType.REPAIR,
    EquipmentMovement.MovementType.DISPOSED,
    EquipmentMovement.MovementType.ACT,
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
    candidate = request.GET.get("back", "").strip()
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        request.session[session_key] = candidate
    return request.session.get(session_key, default_url)






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
        .select_related("category", "owner")[:limit]
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
        equipment = list(employee.equipment.filter(archived=False).select_related("category", "owner"))
        if not equipment:
            continue
        state = _employee_equipment_set(equipment, [])
        if state["requirements"] and not state["complete"] and len(incomplete) < limit:
            incomplete.append({"employee": employee, "missing": state["missing"]})
        documented = _current_assignment_document_status(employee, equipment)
        missing_docs = [item for item in equipment if item.pk not in documented]
        if missing_docs and len(without_act) < limit:
            without_act.append({"employee": employee, "items": missing_docs})

    total = len(unlinked) + len(misplaced) + len(duplicate_serials) + len(incomplete) + len(without_act)
    return {
        "unlinked_employees": unlinked,
        "misplaced_equipment": misplaced,
        "duplicate_serials": duplicate_serials,
        "incomplete_sets": incomplete,
        "without_act": without_act,
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
    context.update({
        "equipment_total": Equipment.objects.filter(archived=False).count(),
        "employees_total": Employee.objects.filter(archived=False).count(),
        "organizations_total": Organization.objects.filter(archived=False).count(),
        "acts_total": Act.objects.count(),
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
    )
    qs, sort, direction = _apply_sorting(qs, request, {
        "name": (Lower("name"),),
        "prefix": (Lower("prefix"), Lower("name")),
        "kind": ("kind", Lower("name")),
        "employees": (F("employee_count"), Lower("name")),
        "equipment": (F("equipment_count"), Lower("name")),
    }, "name")
    return render(request, "inventory/organization_list.html", {"objects": qs, "sort": sort, "direction": direction})


@login_required
def organization_form(request, pk=None):
    obj = get_object_or_404(Organization, pk=pk) if pk else None
    form = OrganizationForm(request.POST or None, instance=obj)
    if form.is_valid():
        obj = form.save()
        messages.success(request, "Владелец сохранён.")
        return redirect("organization_list")
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
        obj.equipment.filter(archived=False).select_related("category", "owner")
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
            ).select_related("category", "owner", "location")
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
    if is_new and request.GET.get("location"):
        initial["workplace_location"] = request.GET.get("location")
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
        stream = build_employee_transfer_docx(
            employee=employee,
            equipment=selected,
            act_date=act_date,
            city=form.cleaned_data["city"],
            organization_name=form.cleaned_data["organization_name"],
            representative_position=form.cleaned_data["representative_position"],
            representative_name=form.cleaned_data["representative_name"],
            act_type=operation,
        )
        filename = _safe_act_filename(employee, act_date, operation)
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
        stream = build_employee_transfer_docx(
            employee=employee,
            equipment=equipment,
            act_date=act_date,
            city=form.cleaned_data["city"],
            organization_name=form.cleaned_data["organization_name"],
            representative_position=form.cleaned_data["representative_position"],
            representative_name=form.cleaned_data["representative_name"],
            act_type=act_type,
        )
        filename = _safe_act_filename(employee, act_date, act_type)

        if act_type == "issue":
            with transaction.atomic():
                locked = list(
                    Equipment.objects.select_for_update().filter(
                        pk__in=[item.pk for item in equipment],
                        archived=False,
                        responsible_employee=employee,
                    ).order_by("pk")
                )
                if len(locked) != len(equipment):
                    messages.error(request, "Часть оборудования больше не закреплена за сотрудником. Обновите страницу.")
                    return redirect(request.path)

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
    if tab not in {"overview", "employees", "rooms", "equipment", "history"}:
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
).select_related("category", "owner", "responsible_employee", "room", "cabinet", "location").distinct()
    q = request.GET.get("q", "").strip()
    group = request.GET.get("group", "")
    status = request.GET.get("status", "")
    if q:
        equipment = equipment.filter(
            Q(internal_code__icontains=q) | Q(name__icontains=q) | Q(manufacturer__icontains=q)
            | Q(model__icontains=q) | Q(serial_number__icontains=q) | Q(network_address__icontains=q)
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
        assigned = list(employee.equipment.filter(archived=False).select_related("category", "owner"))
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
    return render(request, "inventory/location_detail.html", {
        "object": obj, "tab": tab, "employees": employees_qs, "employee_cards": employee_cards,
        "equipment": equipment,
        "technical_equipment": equipment.filter(accounting_group=Equipment.AccountingGroup.TECHNICAL),
        "employee_equipment": equipment.filter(accounting_group=Equipment.AccountingGroup.EMPLOYEE),
        "technical_count": equipment.filter(accounting_group=Equipment.AccountingGroup.TECHNICAL).count(),
        "employee_equipment_count": equipment.filter(accounting_group=Equipment.AccountingGroup.EMPLOYEE).count(),
        "rooms": rooms, "cabinets": cabinets, "history": history, "incomplete_count": incomplete_count,
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
    qs = Equipment.objects.filter(
        archived=False, responsible_employee__isnull=True,
        usage_status__in=[Equipment.UsageStatus.STOCK, Equipment.UsageStatus.RESERVE],
    ).select_related("category", "owner", "location", "room", "cabinet")
    q = request.GET.get("q", "").strip(); owner = request.GET.get("owner", "")
    category = request.GET.get("category", ""); group = request.GET.get("group", "")
    if q:
        qs = qs.filter(Q(internal_code__icontains=q) | Q(name__icontains=q) | Q(manufacturer__icontains=q)
                       | Q(model__icontains=q) | Q(serial_number__icontains=q) | Q(hostname__icontains=q))
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
    form = LocationForm(request.POST or None, instance=obj)
    if form.is_valid():
        form.save()
        messages.success(request, "Адрес сохранён.")
        return redirect("location_list")
    return render(request, "inventory/model_form.html", {"form": form, "title": "Адрес", "cancel_url": reverse("location_list")})


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
    return render(request, "inventory/cabinet_detail.html", {"object": obj, "equipment": obj.equipment.filter(archived=False).select_related("category", "owner", "responsible_employee")})


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
    qs = Equipment.objects.select_related("category", "owner", "responsible_employee", "location", "room", "cabinet").filter(archived=False)
    q = request.GET.get("q", "").strip(); owner = request.GET.get("owner", ""); category = request.GET.get("category", "")
    status = request.GET.get("status", ""); group = request.GET.get("group", ""); location = request.GET.get("location", "")
    if q:
        qs = qs.filter(Q(internal_code__icontains=q) | Q(name__icontains=q) | Q(manufacturer__icontains=q)
                       | Q(model__icontains=q) | Q(serial_number__icontains=q) | Q(hostname__icontains=q)
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
    obj = get_object_or_404(Equipment.objects.select_related("category", "owner", "responsible_employee__organization", "location", "room", "cabinet"), pk=pk)
    back_url = _remember_back_url(request, "equipment_list_back_url", reverse("equipment_list"))
    return render(request, "inventory/equipment_detail.html", {
        "object": obj,
        "movements": _visible_movements(obj.movements.select_related("from_employee", "to_employee", "from_organization", "to_organization", "created_by", "act"))[:100],
        "acts": obj.acts.select_related("employee").all(),
        "loans": obj.loans.select_related("borrower", "responsible_employee").all(),
        "repairs": obj.repairs.all(),
        "back_url": back_url,
    })


@login_required
def equipment_form(request, pk=None):
    obj = get_object_or_404(Equipment, pk=pk) if pk else None
    previous = None
    if obj:
        previous = {"employee": obj.responsible_employee, "status": obj.usage_status, "owner": obj.owner}
    initial = {}
    if obj is None and request.GET.get("location"):
        initial["location"] = request.GET.get("location")
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
    return render(request, "inventory/model_form.html", {"form": form, "title": "Оборудование", "cancel_url": obj.get_absolute_url() if obj else reverse("equipment_list")})


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
    form = LoanForm(request.POST or None, request.FILES or None, equipment=obj)
    if form.is_valid():
        loan = form.save(commit=False)
        loan.equipment = obj
        loan.lender = obj.owner
        loan.save()
        old_status = obj.usage_status
        obj.usage_status = Equipment.UsageStatus.LOANED
        obj.responsible_employee = loan.responsible_employee
        obj.save()
        EquipmentMovement.objects.create(
            equipment=obj, movement_type=EquipmentMovement.MovementType.LOANED,
            from_organization=obj.owner, to_organization=loan.borrower, to_employee=loan.responsible_employee,
            from_status=old_status, to_status=obj.usage_status,
            notes=("Без документов. " if loan.undocumented else "") + loan.notes, created_by=request.user,
        )
        messages.success(request, "Временная передача зарегистрирована.")
        return redirect(obj)
    return render(request, "inventory/model_form.html", {"form": form, "title": f"Передать: {obj}", "cancel_url": obj.get_absolute_url(), "multipart": True})


@login_required
@require_POST
def loan_return(request, pk):
    loan = get_object_or_404(EquipmentLoan.objects.select_related("equipment"), pk=pk, status=EquipmentLoan.Status.ACTIVE)
    loan.status = EquipmentLoan.Status.RETURNED
    loan.returned_at = timezone.localdate()
    loan.save(update_fields=["status", "returned_at", "updated_at"])
    obj = loan.equipment
    old_status = obj.usage_status
    obj.usage_status = Equipment.UsageStatus.STOCK
    obj.responsible_employee = None
    obj.save()
    EquipmentMovement.objects.create(
        equipment=obj, movement_type=EquipmentMovement.MovementType.LOAN_RETURN,
        from_organization=loan.borrower, to_organization=loan.lender,
        from_status=old_status, to_status=obj.usage_status, created_by=request.user,
    )
    messages.success(request, "Оборудование возвращено владельцу.")
    return redirect(obj)


@login_required
@require_POST
def equipment_archive(request, pk):
    obj = get_object_or_404(Equipment, pk=pk)
    obj.archived = not obj.archived
    obj.save(update_fields=["archived", "updated_at"])
    messages.success(request, "Архивный статус оборудования изменён.")
    return redirect(obj)


@login_required
def reveal_network_password(request, pk):
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
    obj = get_object_or_404(Act.objects.select_related("employee", "from_organization", "to_organization").prefetch_related("equipment"), pk=pk)
    return render(request, "inventory/act_detail.html", {
        "object": obj,
        "movements": _visible_movements(obj.movements.select_related("equipment", "from_employee", "to_employee")),
        "auto_download": request.GET.get("download") == "1",
    })


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
    obj = get_object_or_404(Act.objects.select_related("employee").prefetch_related("equipment"), public_token=token, public_enabled=True)
    return render(request, "inventory/public_act.html", {"object": obj})


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
        Equipment.objects.select_related("category", "owner", "responsible_employee", "location", "room", "cabinet"), pk=pk
    )
    return render(request, "inventory/_equipment_preview.html", {"object": obj})


@login_required
def global_search(request):
    q = request.GET.get("q", "").strip()
    equipment = employees = locations = rooms = acts = []
    if q:
        equipment = Equipment.objects.filter(archived=False).filter(
            Q(internal_code__icontains=q) | Q(name__icontains=q) | Q(model__icontains=q)
            | Q(serial_number__icontains=q) | Q(hostname__icontains=q)
        ).select_related("category", "owner", "responsible_employee")[:20]
        employees = Employee.objects.filter(archived=False).filter(
            Q(full_name__icontains=q) | Q(position__icontains=q) | Q(phone__icontains=q)
        ).select_related("organization", "workplace_location")[:20]
        locations = Location.objects.filter(archived=False).filter(
            Q(label__icontains=q) | Q(address__icontains=q) | Q(organization__name__icontains=q)
        ).select_related("organization")[:10]
        rooms = Room.objects.filter(archived=False).filter(
            Q(name__icontains=q) | Q(floor__icontains=q) | Q(location__label__icontains=q) | Q(location__address__icontains=q)
        ).select_related("location", "location__organization")[:10]
        acts = Act.objects.filter(Q(number__icontains=q) | Q(employee__full_name__icontains=q)).select_related("employee")[:10]
    return render(request, "inventory/search_results.html", {
        "q": q, "equipment": equipment, "employees": employees, "locations": locations, "rooms": rooms, "acts": acts,
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
