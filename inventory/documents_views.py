import calendar
import mimetypes
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from .document_forms import (
    ContractForm,
    CounterpartyForm,
    DocumentEditForm,
    DocumentOperationForm,
    DocumentTypeForm,
    DocumentUploadForm,
    InboxUploadForm,
    ReminderForm,
)
from .models import (
    Contract,
    Counterparty,
    DocumentOperation,
    DocumentRecord,
    DocumentType,
    Equipment,
    Location,
    Organization,
    Reminder,
)


def _organization_queryset():
    return Organization.objects.filter(archived=False, kind=Organization.Kind.COMPANY).order_by("name")


def _selected_organization(request):
    raw = request.GET.get("organization", "").strip()
    if not raw:
        return None
    try:
        return _organization_queryset().get(pk=int(raw))
    except (ValueError, Organization.DoesNotExist):
        return None



def _counterparty_ids_for_organization(organization):
    return list(
        Counterparty.objects.filter(
            archived=False,
            linked_organization=organization,
        ).values_list("pk", flat=True)
    )


def _organization_for_counterparty(counterparty):
    if counterparty is None:
        return None
    return counterparty.linked_organization


def _contracts_for_organization(organization, archived=False):
    return Contract.objects.filter(archived=archived).filter(
        Q(organization=organization)
        | Q(counterparty__linked_organization=organization)
    ).distinct()


def _documents_for_organization(organization):
    return DocumentRecord.objects.filter(trashed_at__isnull=True).filter(
        Q(organization=organization)
        | Q(counterparty__linked_organization=organization)
        | Q(contract__counterparty__linked_organization=organization)
    ).distinct()


def _reminders_for_organization(organization):
    return Reminder.objects.filter(active=True).filter(
        Q(organization=organization)
        | Q(counterparty__linked_organization=organization)
        | Q(contract__counterparty__linked_organization=organization)
    ).distinct()


def _operations_for_organization(organization):
    return DocumentOperation.objects.filter(
        Q(organization=organization)
        | Q(counterparty__linked_organization=organization)
        | Q(contract__counterparty__linked_organization=organization)
    ).distinct()


def _relative_party_descriptor(viewpoint, owner_organization, counterparty):
    if owner_organization is None:
        return None

    if owner_organization.pk == viewpoint.pk:
        if counterparty is None:
            return None
        linked = counterparty.linked_organization
        if linked is not None:
            if linked.pk == viewpoint.pk:
                return None
            return {
                "key": f"org:{linked.pk}",
                "kind": "organization",
                "name": str(linked),
                "internal": True,
                "organization_id": linked.pk,
                "counterparty_id": counterparty.pk,
                "form_counterparty_id": counterparty.pk,
            }
        return {
            "key": f"cp:{counterparty.pk}",
            "kind": "counterparty",
            "name": str(counterparty),
            "internal": False,
            "organization_id": None,
            "counterparty_id": counterparty.pk,
            "form_counterparty_id": counterparty.pk,
        }

    if (
        counterparty is not None
        and counterparty.linked_organization_id == viewpoint.pk
    ):
        partner_profile = getattr(owner_organization, "counterparty_profile", None)
        return {
            "key": f"org:{owner_organization.pk}",
            "kind": "organization",
            "name": str(owner_organization),
            "internal": True,
            "organization_id": owner_organization.pk,
            "counterparty_id": partner_profile.pk if partner_profile else None,
            "form_counterparty_id": partner_profile.pk if partner_profile else None,
        }

    return None


def _relative_party_key(viewpoint, item):
    descriptor = _relative_party_descriptor(
        viewpoint,
        getattr(item, "organization", None),
        getattr(item, "counterparty", None),
    )
    return descriptor["key"] if descriptor else None


def _build_party_options(organization, contracts, documents, operations, reminders):
    rows = {}

    def add(item, counter_name, date_value):
        descriptor = _relative_party_descriptor(
            organization,
            getattr(item, "organization", None),
            getattr(item, "counterparty", None),
        )
        if descriptor is None:
            return

        row = rows.get(descriptor["key"])
        if row is None:
            initials = "".join(
                token[:1]
                for token in descriptor["name"].replace("«", " ").replace("»", " ").split()
                if token
            )[:2].upper()
            row = {
                **descriptor,
                "initials": initials or "•",
                "contract_count": 0,
                "operation_count": 0,
                "document_count": 0,
                "reminder_count": 0,
                "last_date": None,
            }
            rows[descriptor["key"]] = row

        row[counter_name] += 1
        if date_value and (row["last_date"] is None or date_value > row["last_date"]):
            row["last_date"] = date_value

    for item in contracts:
        add(item, "contract_count", item.contract_date)
    for item in operations:
        add(item, "operation_count", item.operation_date)
    for item in documents:
        add(item, "document_count", item.document_date)
    for item in reminders:
        add(item, "reminder_count", item.effective_due_date)

    result = sorted(rows.values(), key=lambda row: row["name"].casefold())
    for row in result:
        row["href"] = (
            reverse("organization_document_workspace", args=[organization.pk])
            + f"?party={row['key']}"
        )
    return result


@login_required
def operation_detail(request, pk):
    obj = get_object_or_404(
        DocumentOperation.objects.select_related(
            "organization", "counterparty", "contract", "location", "created_by"
        ),
        pk=pk,
    )
    documents = list(
        obj.documents.filter(trashed_at__isnull=True)
        .select_related("document_type", "organization", "counterparty", "contract")
        .order_by("document_date", "pk")
    )
    return render(
        request,
        "inventory/documents/operation_detail.html",
        {"object": obj, "documents": documents},
    )


@login_required
def operation_form(request, pk=None):
    obj = get_object_or_404(DocumentOperation, pk=pk) if pk else None
    initial = {}

    if obj is None and request.GET.get("contract"):
        contract = get_object_or_404(
            Contract.objects.select_related("organization", "counterparty", "location"),
            pk=request.GET["contract"],
            archived=False,
        )
        initial.update(
            {
                "organization": contract.organization_id,
                "counterparty": contract.counterparty_id,
                "contract": contract.pk,
                "location": contract.location_id,
                "operation_date": timezone.localdate(),
            }
        )
    elif obj is None and request.GET.get("organization"):
        organization = get_object_or_404(
            _organization_queryset(),
            pk=request.GET["organization"],
        )
        initial.update(
            {
                "organization": organization.pk,
                "operation_date": timezone.localdate(),
            }
        )

    if obj is None and request.GET.get("counterparty"):
        initial["counterparty"] = get_object_or_404(
            Counterparty.objects.filter(archived=False),
            pk=request.GET["counterparty"],
        ).pk

    form = DocumentOperationForm(
        request.POST or None,
        instance=obj,
        initial=initial,
    )
    if form.is_valid():
        saved = form.save(commit=False)
        if saved.pk is None:
            saved.created_by = request.user
        saved.save()
        messages.success(request, "Операция сохранена.")
        return redirect(saved)

    if obj:
        cancel_url = obj.get_absolute_url()
    elif initial.get("contract"):
        cancel_url = reverse("contract_detail", args=[initial["contract"]])
    elif initial.get("organization"):
        cancel_url = reverse(
            "organization_document_workspace",
            args=[initial["organization"]],
        )
    else:
        cancel_url = reverse("document_center")

    return render(
        request,
        "inventory/documents/operation_form.html",
        {
            "form": form,
            "title": "Изменить операцию" if obj else "Новая операция",
            "cancel_url": cancel_url,
            "selected_documents": [],
        },
    )


@login_required
@require_POST
def operation_from_documents(request):
    ids = []
    for value in request.POST.getlist("selected_documents"):
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue

    documents = list(
        DocumentRecord.objects.filter(
            pk__in=ids,
            trashed_at__isnull=True,
        )
        .select_related(
            "organization",
            "counterparty",
            "contract",
            "location",
            "document_type",
        )
        .order_by("pk")
    )

    if not documents:
        messages.error(request, "Выберите хотя бы один документ.")
        return redirect("document_list")

    first = documents[0]
    incompatible = [
        item
        for item in documents
        if item.organization_id != first.organization_id
        or item.counterparty_id != first.counterparty_id
        or item.contract_id != first.contract_id
    ]
    if incompatible:
        messages.error(
            request,
            "В одну операцию можно объединить документы одной организации, контрагента и договора.",
        )
        return redirect("document_list")

    operation_date = first.document_date or timezone.localdate()
    initial = {
        "organization": first.organization_id,
        "counterparty": first.counterparty_id,
        "contract": first.contract_id,
        "location": first.location_id,
        "operation_date": operation_date,
    }

    if first.contract_id and first.contract.category == Contract.Category.SUPPLY:
        initial["title"] = f"Поставка от {operation_date:%d.%m.%Y}"
    elif first.contract_id:
        initial["title"] = f"Операция — {first.contract.title}"
    else:
        initial["title"] = f"Операция от {operation_date:%d.%m.%Y}"

    has_operation_fields = bool(request.POST.get("title"))
    form = DocumentOperationForm(
        request.POST if has_operation_fields else None,
        initial=initial,
    )

    if has_operation_fields and form.is_valid():
        with transaction.atomic():
            operation = form.save(commit=False)
            operation.created_by = request.user
            operation.save()

            for document in documents:
                document.operation = operation
                document.save()

        messages.success(
            request,
            f"Создана операция. Документов в пакете: {len(documents)}.",
        )
        return redirect(operation)

    return render(
        request,
        "inventory/documents/operation_form.html",
        {
            "form": form,
            "title": "Создать операцию из документов",
            "cancel_url": reverse("document_list"),
            "selected_documents": documents,
        },
    )


@login_required
def organization_document_workspace(request, pk):
    organization = get_object_or_404(_organization_queryset(), pk=pk)

    contracts = list(
        _contracts_for_organization(organization)
        .select_related(
            "organization",
            "counterparty",
            "counterparty__linked_organization",
            "location",
            "responsible_employee",
        )
        .order_by("-contract_date", "-pk")
    )
    documents = list(
        _documents_for_organization(organization)
        .select_related(
            "organization",
            "counterparty",
            "counterparty__linked_organization",
            "document_type",
            "contract",
            "operation",
            "location",
        )
        .order_by("-document_date", "-created_at", "-pk")
    )
    operations = list(
        _operations_for_organization(organization)
        .select_related(
            "organization",
            "counterparty",
            "counterparty__linked_organization",
            "contract",
            "location",
        )
        .order_by("-operation_date", "-created_at", "-pk")
    )
    reminders = list(
        _reminders_for_organization(organization)
        .select_related(
            "organization",
            "counterparty",
            "counterparty__linked_organization",
            "contract",
            "location",
        )
        .order_by("next_due_date", "pk")
    )

    party_options = _build_party_options(
        organization,
        contracts,
        documents,
        operations,
        reminders,
    )
    requested_party = request.GET.get("party", "").strip()
    selected_party = next(
        (row for row in party_options if row["key"] == requested_party),
        None,
    )

    context = {
        "organization": organization,
        "organizations": _organization_queryset(),
        "party_options": party_options,
        "selected_party": selected_party,
        "parties_total": len(party_options),
        "contracts_total": len(contracts),
        "documents_total": len(documents),
        "operations_total": len(operations),
        "reminders_total": len(reminders),
        "selected_contracts_total": 0,
        "selected_documents_total": 0,
        "selected_operations_total": 0,
        "selected_reminders_total": 0,
        "contract_groups": [],
        "no_contract_operations": [],
        "no_contract_documents": [],
    }

    if selected_party is None:
        return render(
            request,
            "inventory/documents/organization_workspace.html",
            context,
        )

    party_key = selected_party["key"]
    party_contracts = [
        item for item in contracts
        if _relative_party_key(organization, item) == party_key
    ]
    party_documents = [
        item for item in documents
        if _relative_party_key(organization, item) == party_key
    ]
    party_operations = [
        item for item in operations
        if _relative_party_key(organization, item) == party_key
    ]
    party_reminders = [
        item for item in reminders
        if _relative_party_key(organization, item) == party_key
    ]

    docs_by_operation = {}
    for document in party_documents:
        if document.operation_id:
            docs_by_operation.setdefault(document.operation_id, []).append(document)

    contract_groups = []
    for contract in party_contracts:
        contract_documents = [
            item for item in party_documents
            if item.contract_id == contract.pk and item.operation_id is None
        ]
        contract_operations = [
            item for item in party_operations
            if item.contract_id == contract.pk
        ]
        operation_rows = [
            {
                "object": operation,
                "documents": docs_by_operation.get(operation.pk, []),
                "document_count": len(docs_by_operation.get(operation.pk, [])),
            }
            for operation in contract_operations
        ]
        contract_groups.append({
            "contract": contract,
            "documents": contract_documents,
            "contract_document_count": len(contract_documents)
                + (1 if contract.main_file else 0),
            "operations": operation_rows,
            "operation_count": len(operation_rows),
        })

    no_contract_operation_rows = []
    for operation in party_operations:
        if operation.contract_id is None:
            no_contract_operation_rows.append({
                "object": operation,
                "documents": docs_by_operation.get(operation.pk, []),
                "document_count": len(docs_by_operation.get(operation.pk, [])),
            })

    no_contract_documents = [
        item for item in party_documents
        if item.contract_id is None and item.operation_id is None
    ]

    context.update({
        "selected_contracts_total": len(party_contracts),
        "selected_documents_total": len(party_documents),
        "selected_operations_total": len(party_operations),
        "selected_reminders_total": len(party_reminders),
        "contract_groups": contract_groups,
        "no_contract_operations": no_contract_operation_rows,
        "no_contract_documents": no_contract_documents,
    })

    return render(
        request,
        "inventory/documents/organization_workspace.html",
        context,
    )


def _paginate(request, queryset, per_page=50):
    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return page_obj, params.urlencode()


def _safe_next(request, fallback="reminder_list"):
    candidate = request.POST.get("next", "").strip()
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return reverse(fallback)


def _next_month(value):
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _next_year(value):
    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        return value.replace(year=value.year + 1, month=2, day=28)


def _advance_reminder(reminder):
    completed_due = reminder.next_due_date
    reminder.last_completed_at = timezone.now()
    reminder.snoozed_until = None
    today = timezone.localdate()
    if reminder.recurrence == Reminder.Recurrence.ONCE:
        reminder.active = False
    else:
        next_due = reminder.next_due_date
        while True:
            if reminder.recurrence == Reminder.Recurrence.MONTHLY:
                next_due = _next_month(next_due)
            elif reminder.recurrence == Reminder.Recurrence.YEARLY:
                next_due = _next_year(next_due)
            else:
                next_due = next_due + timedelta(days=reminder.interval_days or 1)
            if next_due > today:
                break
        reminder.next_due_date = next_due
    reminder.save(update_fields=["last_completed_at", "snoozed_until", "active", "next_due_date", "updated_at"])
    return completed_due


def reminder_rows(queryset, today=None):
    today = today or timezone.localdate()
    rows = []
    for reminder in queryset:
        due = reminder.effective_due_date
        visible_from = due - timedelta(days=reminder.remind_days_before)
        if visible_from > today:
            state = "later"
        elif due < today:
            state = "overdue"
        elif due == today:
            state = "today"
        else:
            state = "soon"
        rows.append({
            "reminder": reminder,
            "due": due,
            "visible_from": visible_from,
            "state": state,
            "days": (due - today).days,
        })
    return rows


@login_required
def document_center(request):
    organization = _selected_organization(request)
    documents = DocumentRecord.objects.filter(trashed_at__isnull=True)
    contracts = Contract.objects.filter(archived=False)
    reminders = Reminder.objects.filter(active=True).select_related("organization", "counterparty", "contract", "location")
    if organization:
        documents = documents.filter(organization=organization)
        contracts = contracts.filter(organization=organization)
        reminders = reminders.filter(organization=organization)

    today = timezone.localdate()
    visible_reminders = reminder_rows(
        reminders.order_by("next_due_date", "pk")[:8], today=today
    )

    counterparties = Counterparty.objects.filter(archived=False)
    if organization:
        counterparties = counterparties.filter(
            Q(contracts__organization=organization, contracts__archived=False)
            | Q(documents__organization=organization, documents__trashed_at__isnull=True)
        ).distinct()

    return render(request, "inventory/documents/center.html", {
        "organizations": _organization_queryset(),
        "selected_organization": organization,
        "documents_total": documents.count(),
        "contracts_total": contracts.count(),
        "counterparties_total": counterparties.count(),
        "inbox_total": documents.filter(document_type__isnull=True).count(),
        "reminders_total": reminders.count(),
        "recent_documents": documents.select_related("organization", "document_type", "counterparty", "contract").order_by("-created_at")[:8],
        "recent_contracts": contracts.select_related("organization", "counterparty", "location")[:6],
        "reminder_rows": visible_reminders,
    })


@login_required
def document_list(request):
    qs = DocumentRecord.objects.filter(trashed_at__isnull=True).select_related(
        "organization", "document_type", "counterparty", "contract", "operation", "location"
    )
    q = request.GET.get("q", "").strip()
    organization = request.GET.get("organization", "").strip()
    document_type = request.GET.get("type", "").strip()
    counterparty = request.GET.get("counterparty", "").strip()
    contract = request.GET.get("contract", "").strip()
    year = request.GET.get("year", "").strip()
    if q:
        qs = qs.filter(
            Q(title__icontains=q) | Q(number__icontains=q) | Q(original_name__icontains=q)
            | Q(notes__icontains=q) | Q(counterparty__name__icontains=q)
            | Q(contract__title__icontains=q) | Q(contract__number__icontains=q) | Q(operation__title__icontains=q)
        )
    if organization:
        qs = qs.filter(organization_id=organization)
    if document_type == "inbox":
        qs = qs.filter(document_type__isnull=True)
    elif document_type:
        qs = qs.filter(document_type_id=document_type)
    if counterparty:
        qs = qs.filter(counterparty_id=counterparty)
    if contract:
        qs = qs.filter(contract_id=contract)
    if year.isdigit():
        qs = qs.filter(document_date__year=int(year))

    years = list(
        DocumentRecord.objects.filter(trashed_at__isnull=True, document_date__isnull=False)
        .dates("document_date", "year", order="DESC")
    )
    page_obj, query_string = _paginate(request, qs, 60)
    return render(request, "inventory/documents/document_list.html", {
        "objects": page_obj.object_list,
        "page_obj": page_obj,
        "query_string": query_string,
        "q": q,
        "selected_organization": organization,
        "selected_type": document_type,
        "selected_counterparty": counterparty,
        "selected_contract": contract,
        "selected_year": year,
        "organizations": _organization_queryset(),
        "document_types": DocumentType.objects.filter(archived=False),
        "counterparties": Counterparty.objects.filter(archived=False),
        "contracts": Contract.objects.filter(archived=False).select_related("organization", "counterparty"),
        "years": years,
    })


@login_required
def document_inbox(request):
    form = InboxUploadForm(request.POST or None, request.FILES or None)
    if form.is_valid():
        organization = form.cleaned_data["organization"]
        files = form.cleaned_data["files"]
        with transaction.atomic():
            for uploaded in files:
                DocumentRecord.objects.create(
                    organization=organization,
                    file=uploaded,
                    original_name=uploaded.name,
                    created_by=request.user,
                )
        messages.success(request, f"В неразобранные добавлено файлов: {len(files)}.")
        return redirect("document_inbox")
    objects = DocumentRecord.objects.filter(trashed_at__isnull=True, document_type__isnull=True).select_related("organization", "counterparty")[:100]
    return render(request, "inventory/documents/inbox.html", {"form": form, "objects": objects})


@login_required
def document_upload(request):
    initial_contract = None
    initial_operation = None
    initial_organization = None
    initial_counterparty = None
    initial_location = None
    initial_equipment = None
    if request.GET.get("operation"):
        initial_operation = get_object_or_404(DocumentOperation.objects.select_related("organization", "counterparty", "contract", "location"), pk=request.GET["operation"])
    elif request.GET.get("contract"):
        initial_contract = get_object_or_404(Contract.objects.select_related("organization", "counterparty", "location"), pk=request.GET["contract"])
    elif request.GET.get("equipment"):
        initial_equipment = get_object_or_404(Equipment.objects.select_related("owner", "location"), pk=request.GET["equipment"], archived=False)
    elif request.GET.get("location"):
        initial_location = get_object_or_404(Location.objects.select_related("organization"), pk=request.GET["location"], archived=False)
    elif request.GET.get("organization"):
        initial_organization = get_object_or_404(_organization_queryset(), pk=request.GET["organization"])
    if request.GET.get("counterparty"):
        initial_counterparty = get_object_or_404(
            Counterparty.objects.filter(archived=False),
            pk=request.GET["counterparty"],
        )
    form = DocumentUploadForm(
        request.POST or None,
        request.FILES or None,
        initial_contract=initial_contract,
        initial_operation=initial_operation,
        initial_organization=initial_organization,
        initial_counterparty=initial_counterparty,
        initial_location=initial_location,
        initial_equipment=initial_equipment,
    )
    if form.is_valid():
        data = form.cleaned_data
        files = data["files"]
        equipment = list(data["equipment"])
        created = []
        with transaction.atomic():
            for uploaded in files:
                document = DocumentRecord.objects.create(
                    organization=data["organization"],
                    document_type=data.get("document_type"),
                    counterparty=data.get("counterparty"),
                    contract=data.get("contract"),
                    operation=data.get("operation"),
                    location=data.get("location"),
                    title=data.get("title", ""),
                    number=data.get("number", ""),
                    document_date=data.get("document_date"),
                    amount=data.get("amount"),
                    file=uploaded,
                    original_name=uploaded.name,
                    notes=data.get("notes", ""),
                    created_by=request.user,
                )
                if equipment:
                    document.equipment.set(equipment)
                created.append(document)
        messages.success(request, f"Загружено документов: {len(created)}.")
        if len(created) == 1:
            return redirect(created[0])
        return redirect("document_list")
    return render(request, "inventory/documents/document_form.html", {"form": form, "title": "Загрузить документы", "multiple": True})


def _preview_kind(file_field):
    if not file_field or not getattr(file_field, "name", ""):
        return "none"
    extension = Path(file_field.name).suffix.lower()
    if extension == ".pdf":
        return "pdf"
    if extension in {".jpg", ".jpeg", ".png", ".webp"}:
        return "image"
    return "unsupported"


def _preview_label(kind, extension):
    if kind == "pdf":
        return "PDF · встроенный просмотр"
    if kind == "image":
        return "Изображение · встроенный просмотр"
    if kind == "unsupported":
        return f"{extension.upper() or 'Файл'} · доступен оригинал"
    return "Файл недоступен"


def _relationship_viewpoint(request, owner_organization, counterparty):
    viewpoint = owner_organization
    raw = request.GET.get("view", "").strip()
    if not raw.isdigit():
        return viewpoint

    candidate = _organization_queryset().filter(pk=int(raw)).first()
    if candidate is None:
        return viewpoint

    allowed_ids = {owner_organization.pk}
    if counterparty is not None and counterparty.linked_organization_id:
        allowed_ids.add(counterparty.linked_organization_id)

    if candidate.pk in allowed_ids:
        return candidate
    return viewpoint


def _relationship_url(viewpoint, owner_organization, counterparty):
    url = reverse("organization_document_workspace", args=[viewpoint.pk])
    descriptor = _relative_party_descriptor(
        viewpoint,
        owner_organization,
        counterparty,
    )
    if descriptor:
        url += f"?party={descriptor['key']}"
    return url, descriptor


def _storage_file_response(file_field, disposition, filename=None):
    if (
        not file_field
        or not getattr(file_field, "name", "")
        or not default_storage.exists(file_field.name)
    ):
        raise Http404

    safe_filename = filename or Path(file_field.name).name
    content_type = mimetypes.guess_type(safe_filename)[0] or "application/octet-stream"
    response = FileResponse(
        default_storage.open(file_field.name, "rb"),
        content_type=content_type,
    )
    response["Content-Disposition"] = (
        f"{disposition}; filename*=UTF-8''{quote(safe_filename)}"
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def document_detail(request, pk):
    obj = get_object_or_404(
        DocumentRecord.objects.select_related(
            "organization",
            "document_type",
            "counterparty",
            "counterparty__linked_organization",
            "contract",
            "operation",
            "location",
            "created_by",
        ).prefetch_related("equipment"),
        pk=pk,
        trashed_at__isnull=True,
    )

    viewpoint = _relationship_viewpoint(
        request,
        obj.organization,
        obj.counterparty,
    )
    relationship_url, other_party = _relationship_url(
        viewpoint,
        obj.organization,
        obj.counterparty,
    )

    view_query = f"?view={viewpoint.pk}"
    if obj.operation_id:
        back_url = obj.operation.get_absolute_url() + view_query
        back_label = "К операции"
        sequence_qs = (
            DocumentRecord.objects.filter(
                operation_id=obj.operation_id,
                trashed_at__isnull=True,
            )
            .select_related("document_type")
            .order_by("document_date", "pk")
        )
        sequence_label = str(obj.operation)
    elif obj.contract_id:
        back_url = obj.contract.get_absolute_url() + view_query
        back_label = "К договору"
        sequence_qs = (
            DocumentRecord.objects.filter(
                contract_id=obj.contract_id,
                operation__isnull=True,
                trashed_at__isnull=True,
            )
            .select_related("document_type")
            .order_by("document_date", "pk")
        )
        sequence_label = "Документы договора"
    else:
        back_url = relationship_url
        back_label = "К документам организации"
        sequence_qs = (
            DocumentRecord.objects.filter(
                organization_id=obj.organization_id,
                counterparty_id=obj.counterparty_id,
                contract__isnull=True,
                operation__isnull=True,
                trashed_at__isnull=True,
            )
            .select_related("document_type")
            .order_by("document_date", "pk")
        )
        sequence_label = "Документы без договора"

    sequence = list(sequence_qs)
    sequence_ids = [item.pk for item in sequence]
    try:
        sequence_index = sequence_ids.index(obj.pk)
    except ValueError:
        sequence_index = 0

    previous_document = (
        sequence[sequence_index - 1]
        if sequence_index > 0
        else None
    )
    next_document = (
        sequence[sequence_index + 1]
        if sequence_index + 1 < len(sequence)
        else None
    )

    file_exists = bool(
        obj.file
        and obj.file.name
        and default_storage.exists(obj.file.name)
    )
    kind = _preview_kind(obj.file) if file_exists else "none"
    extension = Path(obj.file.name).suffix.lower().lstrip(".") if obj.file else ""

    return render(request, "inventory/documents/document_detail.html", {
        "object": obj,
        "viewpoint": viewpoint,
        "other_party": other_party,
        "relationship_url": relationship_url,
        "back_url": back_url,
        "back_label": back_label,
        "preview_kind": kind,
        "preview_available": kind in {"pdf", "image"},
        "preview_label": _preview_label(kind, extension),
        "file_extension": extension,
        "previous_document": previous_document,
        "next_document": next_document,
        "sequence_position": sequence_index + 1 if sequence else 1,
        "sequence_total": len(sequence) if sequence else 1,
        "sequence_label": sequence_label,
    })


@login_required
def document_edit(request, pk):
    obj = get_object_or_404(DocumentRecord, pk=pk, trashed_at__isnull=True)
    form = DocumentEditForm(request.POST or None, request.FILES or None, instance=obj)
    if form.is_valid():
        form.save()
        messages.success(request, "Документ сохранён.")
        return redirect(obj)
    return render(request, "inventory/documents/document_form.html", {
        "form": form,
        "title": "Изменить документ",
        "object": obj,
    })


@login_required
@xframe_options_sameorigin
def document_preview(request, pk):
    obj = get_object_or_404(
        DocumentRecord,
        pk=pk,
        trashed_at__isnull=True,
    )
    if _preview_kind(obj.file) not in {"pdf", "image"}:
        raise Http404
    filename = obj.original_name or Path(obj.file.name).name
    return _storage_file_response(obj.file, "inline", filename)


@login_required
def document_download(request, pk):
    obj = get_object_or_404(
        DocumentRecord,
        pk=pk,
        trashed_at__isnull=True,
    )
    filename = obj.original_name or Path(obj.file.name).name
    return _storage_file_response(obj.file, "attachment", filename)


@login_required
def contract_file_view(request, pk):
    obj = get_object_or_404(
        Contract.objects.select_related(
            "organization",
            "counterparty",
            "counterparty__linked_organization",
            "location",
        ),
        pk=pk,
    )
    if not obj.main_file:
        raise Http404

    viewpoint = _relationship_viewpoint(
        request,
        obj.organization,
        obj.counterparty,
    )
    relationship_url, other_party = _relationship_url(
        viewpoint,
        obj.organization,
        obj.counterparty,
    )

    file_exists = bool(
        obj.main_file
        and obj.main_file.name
        and default_storage.exists(obj.main_file.name)
    )
    kind = _preview_kind(obj.main_file) if file_exists else "none"
    extension = Path(obj.main_file.name).suffix.lower().lstrip(".")
    filename = Path(obj.main_file.name).name

    return render(request, "inventory/documents/contract_file_detail.html", {
        "object": obj,
        "viewpoint": viewpoint,
        "other_party": other_party,
        "relationship_url": relationship_url,
        "preview_kind": kind,
        "preview_available": kind in {"pdf", "image"},
        "preview_label": _preview_label(kind, extension),
        "file_extension": extension,
        "filename": filename,
    })


@login_required
@xframe_options_sameorigin
def contract_file_preview(request, pk):
    obj = get_object_or_404(Contract, pk=pk)
    if _preview_kind(obj.main_file) not in {"pdf", "image"}:
        raise Http404
    return _storage_file_response(
        obj.main_file,
        "inline",
        Path(obj.main_file.name).name,
    )


@login_required
def contract_file_download(request, pk):
    obj = get_object_or_404(Contract, pk=pk)
    if not obj.main_file:
        raise Http404
    return _storage_file_response(
        obj.main_file,
        "attachment",
        Path(obj.main_file.name).name,
    )


@login_required
@require_POST
def document_trash(request, pk):
    obj = get_object_or_404(DocumentRecord, pk=pk, trashed_at__isnull=True)
    obj.trashed_at = timezone.now()
    obj.save(update_fields=["trashed_at", "updated_at"])
    messages.success(request, "Документ перемещён в корзину.")
    return redirect("document_list")


@login_required
def document_trash_list(request):
    objects = DocumentRecord.objects.filter(trashed_at__isnull=False).select_related("organization", "document_type", "counterparty", "contract")[:200]
    return render(request, "inventory/documents/trash.html", {"objects": objects})


@login_required
@require_POST
def document_restore(request, pk):
    obj = get_object_or_404(DocumentRecord, pk=pk, trashed_at__isnull=False)
    obj.trashed_at = None
    obj.save(update_fields=["trashed_at", "updated_at"])
    messages.success(request, "Документ восстановлен.")
    return redirect(obj)


@login_required
@require_POST
def document_delete_permanently(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden("Недостаточно прав.")
    obj = get_object_or_404(DocumentRecord, pk=pk, trashed_at__isnull=False)
    file_name = obj.file.name if obj.file else ""
    obj.delete()
    if file_name and default_storage.exists(file_name):
        default_storage.delete(file_name)
    messages.success(request, "Документ удалён окончательно.")
    return redirect("document_trash_list")


@login_required
def counterparty_list(request):
    q = request.GET.get("q", "").strip()
    show_archived = request.GET.get("archived") == "1"
    qs = Counterparty.objects.filter(archived=show_archived).select_related("linked_organization").annotate(
        contract_count=Count("contracts", filter=Q(contracts__archived=False), distinct=True),
        document_count=Count("documents", filter=Q(documents__trashed_at__isnull=True), distinct=True),
    )
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(short_name__icontains=q) | Q(inn__icontains=q))
    return render(request, "inventory/documents/counterparty_list.html", {"objects": qs, "q": q, "show_archived": show_archived})


@login_required
def counterparty_detail(request, pk):
    obj = get_object_or_404(Counterparty, pk=pk)
    return render(request, "inventory/documents/counterparty_detail.html", {
        "object": obj,
        "contracts": obj.contracts.select_related("organization", "location").filter(archived=False),
        "documents": obj.documents.select_related("organization", "document_type", "contract").filter(trashed_at__isnull=True)[:30],
    })


@login_required
def counterparty_form(request, pk=None):
    obj = get_object_or_404(Counterparty, pk=pk) if pk else None
    form = CounterpartyForm(request.POST or None, instance=obj)
    if form.is_valid():
        saved = form.save()
        messages.success(request, "Контрагент сохранён.")
        return redirect(saved)
    return render(request, "inventory/documents/simple_form.html", {"form": form, "title": "Контрагент", "cancel_url": obj.get_absolute_url() if obj else reverse("counterparty_list")})


@login_required
def contract_list(request):
    q = request.GET.get("q", "").strip()
    organization = request.GET.get("organization", "").strip()
    category = request.GET.get("category", "").strip()
    show_archived = request.GET.get("archived") == "1"
    qs = Contract.objects.filter(archived=show_archived).select_related("organization", "counterparty", "location", "responsible_employee").annotate(
        document_count=Count("documents", filter=Q(documents__trashed_at__isnull=True), distinct=True),
        reminder_count=Count("reminders", filter=Q(reminders__active=True), distinct=True),
    )
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(number__icontains=q) | Q(counterparty__name__icontains=q))
    if organization:
        qs = qs.filter(organization_id=organization)
    if category in dict(Contract.Category.choices):
        qs = qs.filter(category=category)
    return render(request, "inventory/documents/contract_list.html", {
        "objects": qs,
        "q": q,
        "organizations": _organization_queryset(),
        "selected_organization": organization,
        "categories": Contract.Category.choices,
        "selected_category": category,
        "show_archived": show_archived,
    })


@login_required
def contract_detail(request, pk):
    obj = get_object_or_404(
        Contract.objects.select_related(
            "organization",
            "counterparty",
            "counterparty__linked_organization",
            "location",
            "responsible_employee",
            "created_by",
        ),
        pk=pk,
    )

    viewpoint = obj.organization
    requested_view = request.GET.get("view", "").strip()
    if requested_view.isdigit():
        candidate = _organization_queryset().filter(pk=int(requested_view)).first()
        if candidate is not None and (
            candidate.pk == obj.organization_id
            or candidate.pk == obj.counterparty.linked_organization_id
        ):
            viewpoint = candidate

    other_party = _relative_party_descriptor(
        viewpoint,
        obj.organization,
        obj.counterparty,
    )
    if other_party is None:
        viewpoint = obj.organization
        other_party = _relative_party_descriptor(
            viewpoint,
            obj.organization,
            obj.counterparty,
        )

    relationship_back_url = reverse(
        "organization_document_workspace",
        args=[viewpoint.pk],
    )
    if other_party:
        relationship_back_url += f"?party={other_party['key']}"

    operations = obj.operations.select_related(
        "organization", "counterparty", "location"
    ).annotate(
        document_count=Count(
            "documents",
            filter=Q(documents__trashed_at__isnull=True),
            distinct=True,
        )
    )
    contract_documents = (
        obj.documents.filter(
            trashed_at__isnull=True,
            operation__isnull=True,
        )
        .select_related("document_type", "organization", "counterparty")
        .order_by("-document_date", "-created_at", "-pk")
    )
    reminders = obj.reminders.filter(active=True).select_related(
        "organization", "counterparty", "location"
    )
    return render(request, "inventory/documents/contract_detail.html", {
        "object": obj,
        "operations": operations,
        "contract_documents": contract_documents,
        "reminder_rows": reminder_rows(reminders),
        "viewpoint": viewpoint,
        "other_party": other_party,
        "relationship_back_url": relationship_back_url,
    })


@login_required
def contract_form(request, pk=None):
    obj = get_object_or_404(Contract, pk=pk) if pk else None
    initial = {}
    if obj is None and request.GET.get("organization"):
        initial["organization"] = request.GET["organization"]
    if obj is None and request.GET.get("counterparty"):
        initial["counterparty"] = request.GET["counterparty"]
    form = ContractForm(request.POST or None, request.FILES or None, instance=obj, initial=initial)
    if form.is_valid():
        saved = form.save(commit=False)
        if saved.pk is None:
            saved.created_by = request.user
        saved.save()
        form.save_m2m()
        messages.success(request, "Договор сохранён.")
        return redirect(saved)
    return render(request, "inventory/documents/simple_form.html", {"form": form, "title": "Договор", "cancel_url": obj.get_absolute_url() if obj else reverse("contract_list"), "multipart": True})


@login_required
def document_type_list(request):
    objects = DocumentType.objects.all()
    return render(request, "inventory/documents/document_type_list.html", {"objects": objects})


@login_required
def document_type_form(request, pk=None):
    obj = get_object_or_404(DocumentType, pk=pk) if pk else None
    form = DocumentTypeForm(request.POST or None, instance=obj)
    if form.is_valid():
        form.save()
        messages.success(request, "Тип документа сохранён.")
        return redirect("document_type_list")
    return render(request, "inventory/documents/simple_form.html", {"form": form, "title": "Тип документа", "cancel_url": reverse("document_type_list")})


@login_required
def reminder_list(request):
    organization = request.GET.get("organization", "").strip()
    show_inactive = request.GET.get("inactive") == "1"
    qs = Reminder.objects.filter(active=not show_inactive).select_related("organization", "counterparty", "contract", "location")
    if organization:
        qs = qs.filter(organization_id=organization)
    rows = reminder_rows(qs)
    rows.sort(key=lambda row: (row["due"], row["reminder"].pk))
    return render(request, "inventory/documents/reminder_list.html", {
        "reminder_rows": rows,
        "organizations": _organization_queryset(),
        "selected_organization": organization,
        "show_inactive": show_inactive,
    })


@login_required
def reminder_form(request, pk=None):
    obj = get_object_or_404(Reminder, pk=pk) if pk else None
    initial = {}
    if obj is None:
        if request.GET.get("contract"):
            contract = get_object_or_404(Contract, pk=request.GET["contract"])
            initial.update({
                "contract": contract.pk,
                "organization": contract.organization_id,
                "counterparty": contract.counterparty_id,
                "location": contract.location_id,
            })
        elif request.GET.get("organization"):
            initial["organization"] = request.GET["organization"]
    form = ReminderForm(request.POST or None, instance=obj, initial=initial)
    if form.is_valid():
        saved = form.save(commit=False)
        if saved.pk is None:
            saved.created_by = request.user
        saved.save()
        messages.success(request, "Напоминание сохранено.")
        return redirect("reminder_list")
    return render(request, "inventory/documents/simple_form.html", {"form": form, "title": "Напоминание", "cancel_url": reverse("reminder_list")})


@login_required
@require_POST
def reminder_done(request, pk):
    reminder = get_object_or_404(Reminder, pk=pk, active=True)
    _advance_reminder(reminder)
    messages.success(request, "Готово. Напоминание обновлено.")
    return redirect(_safe_next(request))


@login_required
@require_POST
def reminder_snooze(request, pk):
    reminder = get_object_or_404(Reminder, pk=pk, active=True)
    try:
        days = int(request.POST.get("days", "1"))
    except ValueError:
        days = 1
    if days not in {1, 3, 7}:
        days = 1
    reminder.snoozed_until = timezone.localdate() + timedelta(days=days)
    reminder.save(update_fields=["snoozed_until", "updated_at"])
    messages.success(request, f"Напоминание отложено на {days} дн.")
    return redirect(_safe_next(request))
