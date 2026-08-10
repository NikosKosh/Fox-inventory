import calendar
import mimetypes
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Prefetch, Q
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
    DocumentActivity,
    DocumentFileVersion,
    DocumentOperation,
    DocumentRecord,
    DocumentType,
    Equipment,
    Location,
    Organization,
    OrganizationCounterpartyLink,
    Reminder,
)
from .validators import validate_business_document


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




def _normalize_company_name(value):
    return "".join(
        char
        for char in (value or "").casefold()
        if char.isalnum()
    )


def _ensure_party_link(organization, counterparty, user=None):
    if organization is None or counterparty is None:
        return None
    if counterparty.linked_organization_id == organization.pk:
        return None

    link, _ = OrganizationCounterpartyLink.objects.get_or_create(
        organization=organization,
        counterparty=counterparty,
        defaults={"created_by": user, "archived": False},
    )
    updates = []
    if link.archived:
        link.archived = False
        updates.append("archived")
    if link.created_by_id is None and user is not None:
        link.created_by = user
        updates.append("created_by")
    if updates:
        updates.append("updated_at")
        link.save(update_fields=updates)

    linked_org = counterparty.linked_organization
    owner_profile = getattr(organization, "counterparty_profile", None)
    if linked_org is not None and owner_profile is not None:
        reverse_link, _ = OrganizationCounterpartyLink.objects.get_or_create(
            organization=linked_org,
            counterparty=owner_profile,
            defaults={"created_by": user, "archived": False},
        )
        if reverse_link.archived:
            reverse_link.archived = False
            reverse_link.save(update_fields=["archived", "updated_at"])

    return link


def _find_existing_counterparty(name="", inn=""):
    qs = Counterparty.objects.filter(archived=False)
    inn = (inn or "").strip()
    if inn:
        found = qs.filter(inn=inn).first()
        if found is not None:
            return found

    key = _normalize_company_name(name)
    if not key:
        return None

    for item in qs.only("pk", "name", "short_name", "inn"):
        if key in {
            _normalize_company_name(item.name),
            _normalize_company_name(item.short_name),
        }:
            return item
    return None


def _infer_document_type_from_filename(filename):
    name = (filename or "").casefold()
    patterns = [
        ("invoice-facture", ("счет-фактур", "счёт-фактур", "invoice-facture")),
        ("contract", ("договор", "contract")),
        ("addendum", ("допсоглаш", "доп. соглаш", "доп соглаш", "addendum")),
        ("upd", ("упд",)),
        ("invoice", ("счет", "счёт", "invoice")),
        ("waybill", ("наклад", "waybill")),
        ("specification", ("специф", "specification")),
        ("service-act", ("акт", "service-act")),
    ]
    for code, tokens in patterns:
        if any(token in name for token in tokens):
            found = DocumentType.objects.filter(code=code, archived=False).first()
            if found is not None:
                return found
    return None


def _suggest_operation_title(contract, operation_date):
    operation_date = operation_date or timezone.localdate()
    if contract is None:
        return f"Разовая операция · {operation_date:%d.%m.%Y}"
    if contract.category == Contract.Category.SUPPLY:
        return f"Поставка · {operation_date:%d.%m.%Y}"
    if contract.category in {
        Contract.Category.SERVICES,
        Contract.Category.INTERNET,
        Contract.Category.MAINTENANCE,
        Contract.Category.SOFTWARE,
        Contract.Category.RENT,
    }:
        return f"{contract.get_category_display()} · {date_format(operation_date, 'F Y')}"
    return f"Исполнение · {operation_date:%d.%m.%Y}"


def _relationship_context_label(organization, counterparty, contract=None, operation=None):
    parts = []
    if organization is not None:
        parts.append(str(organization))
    if counterparty is not None:
        parts.append(str(counterparty))
    label = " ↔ ".join(parts)
    if contract is not None:
        label += f" · {contract}"
    if operation is not None:
        label += f" · {operation}"
    return label



def _file_sha256(uploaded):
    import hashlib

    digest = hashlib.sha256()
    for chunk in uploaded.chunks():
        digest.update(chunk)
    try:
        uploaded.seek(0)
    except (AttributeError, OSError):
        pass
    return digest.hexdigest()


def _duplicate_document_for_hash(file_sha256, exclude_pk=None):
    if not file_sha256:
        return None
    qs = DocumentRecord.objects.filter(
        file_sha256=file_sha256,
        trashed_at__isnull=True,
    )
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.select_related(
        "organization",
        "counterparty",
        "contract",
        "operation",
    ).first()


def _log_document_activity(
    *,
    action,
    message,
    user=None,
    document=None,
    operation=None,
    contract=None,
    organization=None,
    counterparty=None,
):
    if document is not None:
        organization = organization or document.organization
        counterparty = counterparty or document.counterparty
        contract = contract or document.contract
        operation = operation or document.operation
    elif operation is not None:
        organization = organization or operation.organization
        counterparty = counterparty or operation.counterparty
        contract = contract or operation.contract

    return DocumentActivity.objects.create(
        action=action,
        message=message[:500],
        actor=user,
        organization=organization,
        counterparty=counterparty,
        contract=contract,
        operation=operation,
        document=document,
    )


def _view_query(request, fallback_organization=None):
    raw = request.GET.get("view", "").strip()
    if raw.isdigit():
        return f"?view={raw}"
    if fallback_organization is not None:
        return f"?view={fallback_organization.pk}"
    return ""


def _safe_return_url(request, fallback):
    candidate = (
        request.POST.get("next", "").strip()
        or request.GET.get("next", "").strip()
    )
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback


def _operation_compatible_documents(operation):
    return DocumentRecord.objects.filter(
        organization_id=operation.organization_id,
        counterparty_id=operation.counterparty_id,
        contract_id=operation.contract_id,
        trashed_at__isnull=True,
    )


def _operation_compatible_operations(operation):
    return (
        DocumentOperation.objects.filter(
            organization_id=operation.organization_id,
            counterparty_id=operation.counterparty_id,
            contract_id=operation.contract_id,
        )
        .exclude(pk=operation.pk)
        .order_by("-operation_date", "-created_at", "-pk")
    )



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


def _build_party_options(
    organization,
    contracts,
    documents,
    operations,
    reminders,
    links=None,
):
    rows = {}

    def ensure(descriptor, counterparty=None):
        if descriptor is None:
            return None
        row = rows.get(descriptor["key"])
        if row is not None:
            return row

        initials = "".join(
            token[:1]
            for token in descriptor["name"]
            .replace("«", " ")
            .replace("»", " ")
            .split()
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
            "inn": counterparty.inn if counterparty is not None else "",
            "search_text": " ".join(
                value
                for value in [
                    descriptor["name"],
                    counterparty.name if counterparty is not None else "",
                    counterparty.short_name if counterparty is not None else "",
                    counterparty.inn if counterparty is not None else "",
                ]
                if value
            ),
        }
        rows[descriptor["key"]] = row
        return row

    for link in links or []:
        descriptor = _relative_party_descriptor(
            organization,
            link.organization,
            link.counterparty,
        )
        ensure(descriptor, link.counterparty)

    def add(item, counter_name, date_value):
        counterparty = getattr(item, "counterparty", None)
        descriptor = _relative_party_descriptor(
            organization,
            getattr(item, "organization", None),
            counterparty,
        )
        row = ensure(descriptor, counterparty)
        if row is None:
            return

        row[counter_name] += 1
        if date_value and (
            row["last_date"] is None or date_value > row["last_date"]
        ):
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
            "organization",
            "counterparty",
            "counterparty__linked_organization",
            "contract",
            "location",
            "created_by",
        ),
        pk=pk,
    )
    documents = list(
        obj.documents.filter(trashed_at__isnull=True)
        .select_related(
            "document_type",
            "organization",
            "counterparty",
            "contract",
        )
        .order_by("document_date", "pk")
    )

    viewpoint = _relationship_viewpoint(
        request,
        obj.organization,
        obj.counterparty,
    )
    relationship_back_url, other_party = _relationship_url(
        viewpoint,
        obj.organization,
        obj.counterparty,
    )

    return render(
        request,
        "inventory/documents/operation_detail.html",
        {
            "object": obj,
            "documents": documents,
            "viewpoint": viewpoint,
            "other_party": other_party,
            "relationship_back_url": relationship_back_url,
            "relationship_back_label": (
                f"{viewpoint} ↔ {other_party['name']}"
                if other_party
                else str(viewpoint)
            ),
            "compatible_operations": _operation_compatible_operations(obj),
            "activity_rows": obj.activity.select_related("actor")[:20],
        },
    )


@login_required
@require_POST
def operation_quick_upload(request, pk):
    obj = get_object_or_404(
        DocumentOperation.objects.select_related(
            "organization",
            "counterparty",
            "contract",
            "location",
        ),
        pk=pk,
    )
    uploaded_files = request.FILES.getlist("files")
    target = obj.get_absolute_url() + _view_query(
        request,
        obj.organization,
    )

    if not uploaded_files:
        messages.error(request, "Выберите хотя бы один файл.")
        return redirect(target)

    prepared = []
    selected_hashes = set()

    for uploaded in uploaded_files:
        try:
            validate_business_document(uploaded)
        except ValidationError as exc:
            messages.error(
                request,
                f"{uploaded.name}: {'; '.join(exc.messages)}",
            )
            return redirect(target)

        file_hash = _file_sha256(uploaded)
        if file_hash in selected_hashes:
            messages.error(
                request,
                f"{uploaded.name}: этот же файл выбран дважды.",
            )
            return redirect(target)

        duplicate = _duplicate_document_for_hash(file_hash)
        if duplicate is not None:
            messages.error(
                request,
                (
                    f"{uploaded.name}: такой файл уже есть в FOX Inventory "
                    f"как «{duplicate.display_title}»."
                ),
            )
            return redirect(target)

        selected_hashes.add(file_hash)
        inferred_type = _infer_document_type_from_filename(
            uploaded.name
        )
        prepared.append(
            (
                uploaded,
                file_hash,
                inferred_type,
            )
        )

    created = []
    with transaction.atomic():
        _ensure_party_link(
            obj.organization,
            obj.counterparty,
            request.user,
        )
        for uploaded, file_hash, inferred_type in prepared:
            document = DocumentRecord.objects.create(
                organization=obj.organization,
                document_type=inferred_type,
                classification_source=(
                    DocumentRecord.ClassificationSource.FILENAME
                    if inferred_type is not None
                    else DocumentRecord.ClassificationSource.UNKNOWN
                ),
                counterparty=obj.counterparty,
                contract=obj.contract,
                operation=obj,
                location=obj.location,
                document_date=None,
                file=uploaded,
                original_name=uploaded.name,
                file_sha256=file_hash,
                created_by=request.user,
            )
            created.append(document)
            _log_document_activity(
                action="uploaded",
                message=f"Файл «{uploaded.name}» добавлен в пакет.",
                user=request.user,
                document=document,
                operation=obj,
            )

    messages.success(
        request,
        (
            f"В пакет добавлено файлов: {len(created)}. "
            "Дата документа не подставлялась автоматически."
        ),
    )
    return redirect(target)


@login_required
@require_POST
def operation_remove_document(request, pk, document_pk):
    operation = get_object_or_404(DocumentOperation, pk=pk)
    document = get_object_or_404(
        DocumentRecord,
        pk=document_pk,
        operation=operation,
        trashed_at__isnull=True,
    )
    document.operation = None
    document.save(update_fields=["operation", "updated_at"])
    _log_document_activity(
        action="removed_from_package",
        message=f"«{document.display_title}» убран из пакета «{operation}».",
        user=request.user,
        document=document,
        operation=operation,
    )
    messages.success(
        request,
        f"{document.display_title} теперь находится вне пакета.",
    )
    return redirect(
        operation.get_absolute_url()
        + _view_query(request, operation.organization)
    )


@login_required
@require_POST
def operation_move_document(request, pk, document_pk):
    source = get_object_or_404(DocumentOperation, pk=pk)
    document = get_object_or_404(
        DocumentRecord,
        pk=document_pk,
        operation=source,
        trashed_at__isnull=True,
    )
    target_raw = request.POST.get("target_operation", "").strip()
    if not target_raw.isdigit():
        messages.error(request, "Выберите пакет назначения.")
        return redirect(
            source.get_absolute_url()
            + _view_query(request, source.organization)
        )

    target = get_object_or_404(DocumentOperation, pk=int(target_raw))
    if (
        target.organization_id != source.organization_id
        or target.counterparty_id != source.counterparty_id
        or target.contract_id != source.contract_id
    ):
        messages.error(
            request,
            "Перемещать документ можно только между пакетами одной пары сторон и одного договора.",
        )
        return redirect(
            source.get_absolute_url()
            + _view_query(request, source.organization)
        )

    document.operation = target
    document.save(update_fields=["operation", "updated_at"])
    _log_document_activity(
        action="moved_package",
        message=f"«{document.display_title}» перемещён из «{source}» в «{target}».",
        user=request.user,
        document=document,
        operation=target,
    )
    messages.success(
        request,
        f"{document.display_title} перемещён в пакет «{target}».",
    )
    return redirect(
        target.get_absolute_url()
        + _view_query(request, source.organization)
    )


@login_required
@require_POST
def operation_disband(request, pk):
    operation = get_object_or_404(
        DocumentOperation.objects.select_related(
            "organization",
            "counterparty",
            "contract",
        ),
        pk=pk,
    )
    viewpoint = _relationship_viewpoint(
        request,
        operation.organization,
        operation.counterparty,
    )
    relationship_url, _ = _relationship_url(
        viewpoint,
        operation.organization,
        operation.counterparty,
    )
    documents = list(
        operation.documents.filter(
            trashed_at__isnull=True
        ).only("pk")
    )
    count = len(documents)

    with transaction.atomic():
        _log_document_activity(
            action="package_disbanded",
            message=f"Пакет «{operation}» разобран. Файлов: {count}.",
            user=request.user,
            operation=operation,
        )
        operation.documents.update(operation=None)
        operation.delete()

    messages.success(
        request,
        (
            f"Пакет разобран. {count} файлов сохранены "
            "и возвращены на уровень договора/стороны."
        ),
    )
    return redirect(relationship_url)


@login_required
def operation_form(request, pk=None):
    obj = get_object_or_404(
        DocumentOperation.objects.select_related(
            "organization",
            "counterparty",
            "contract",
            "location",
        ),
        pk=pk,
    ) if pk else None

    initial = {}
    initial_organization = None
    initial_counterparty = None
    initial_contract = None

    if obj is None and request.GET.get("contract"):
        initial_contract = get_object_or_404(
            Contract.objects.select_related(
                "organization",
                "counterparty",
                "location",
            ),
            pk=request.GET["contract"],
            archived=False,
        )
        initial_organization = initial_contract.organization
        initial_counterparty = initial_contract.counterparty
        initial.update(
            {
                "organization": initial_contract.organization_id,
                "counterparty": initial_contract.counterparty_id,
                "contract": initial_contract.pk,
                "location": initial_contract.location_id,
                "operation_date": timezone.localdate(),
            }
        )
    elif obj is None and request.GET.get("organization"):
        initial_organization = get_object_or_404(
            _organization_queryset(),
            pk=request.GET["organization"],
        )
        initial.update(
            {
                "organization": initial_organization.pk,
                "operation_date": timezone.localdate(),
            }
        )

    if obj is None and request.GET.get("counterparty"):
        initial_counterparty = get_object_or_404(
            Counterparty.objects.filter(archived=False),
            pk=request.GET["counterparty"],
        )
        initial["counterparty"] = initial_counterparty.pk

    if obj is None:
        operation_date = initial.get(
            "operation_date",
            timezone.localdate(),
        )
        initial.setdefault(
            "title",
            _suggest_operation_title(
                initial_contract,
                operation_date,
            ),
        )

    form = DocumentOperationForm(
        request.POST or None,
        instance=obj,
        initial=initial,
    )

    if obj is None:
        if initial.get("organization"):
            form.fields["organization"].disabled = True
        if initial.get("counterparty"):
            form.fields["counterparty"].disabled = True
        if initial.get("contract"):
            form.fields["contract"].disabled = True

    if form.is_valid():
        saved = form.save(commit=False)
        if saved.pk is None:
            saved.created_by = request.user
        saved.save()
        _ensure_party_link(
            saved.organization,
            saved.counterparty,
            request.user,
        )
        _log_document_activity(
            action="package_edited" if obj else "package_created",
            message=f'Пакет «{saved}» сохранён.',
            user=request.user,
            operation=saved,
        )
        messages.success(request, "Пакет сохранён.")

        target = saved.get_absolute_url()
        viewpoint = request.GET.get("view", "").strip()
        if viewpoint.isdigit():
            target += f"?view={viewpoint}"
        return redirect(target)

    if obj:
        cancel_url = obj.get_absolute_url()
        context_label = _relationship_context_label(
            obj.organization,
            obj.counterparty,
            obj.contract,
        )
    elif initial_organization and initial_counterparty:
        descriptor = _relative_party_descriptor(
            initial_organization,
            initial_organization,
            initial_counterparty,
        )
        cancel_url = reverse(
            "organization_document_workspace",
            args=[initial_organization.pk],
        )
        if descriptor:
            cancel_url += f"?party={descriptor['key']}"
        context_label = _relationship_context_label(
            initial_organization,
            initial_counterparty,
            initial_contract,
        )
    elif initial_contract:
        cancel_url = initial_contract.get_absolute_url()
        context_label = _relationship_context_label(
            initial_contract.organization,
            initial_contract.counterparty,
            initial_contract,
        )
    elif initial_organization:
        cancel_url = reverse(
            "organization_document_workspace",
            args=[initial_organization.pk],
        )
        context_label = str(initial_organization)
    else:
        cancel_url = reverse("document_center")
        context_label = ""

    return render(
        request,
        "inventory/documents/operation_form.html",
        {
            "form": form,
            "title": "Изменить пакет" if obj else "Новый пакет",
            "cancel_url": cancel_url,
            "selected_documents": [],
            "context_label": context_label,
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
            "operation",
        )
        .order_by("pk")
    )

    if not documents:
        messages.error(request, "Выберите хотя бы один файл вне пакета.")
        return redirect("document_list")

    already_grouped = [
        item
        for item in documents
        if item.operation_id is not None
    ]
    if already_grouped:
        messages.error(
            request,
            (
                "Один или несколько выбранных файлов уже находятся в пакете. "
                "Для перемещения используйте меню файла внутри пакета."
            ),
        )
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
            (
                "В один пакет можно собрать только файлы одной организации, "
                "одной второй стороны и одного договора."
            ),
        )
        return redirect("document_list")

    operation_date = first.document_date or timezone.localdate()
    initial = {
        "organization": first.organization_id,
        "counterparty": first.counterparty_id,
        "contract": first.contract_id,
        "location": first.location_id,
        "operation_date": operation_date,
        "title": _suggest_operation_title(
            first.contract,
            operation_date,
        ),
    }

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
                document.save(
                    update_fields=[
                        "operation",
                        "updated_at",
                    ]
                )
                _log_document_activity(
                    action="added_to_package",
                    message=(
                        f"«{document.display_title}» добавлен "
                        f"в пакет «{operation}»."
                    ),
                    user=request.user,
                    document=document,
                    operation=operation,
                )

            _log_document_activity(
                action="package_created",
                message=(
                    f"Создан пакет «{operation}». "
                    f"Файлов: {len(documents)}."
                ),
                user=request.user,
                operation=operation,
            )

        messages.success(
            request,
            (
                f"Создан пакет «{operation}». "
                f"Объединено файлов: {len(documents)}."
            ),
        )
        return redirect(operation)

    return render(
        request,
        "inventory/documents/operation_form.html",
        {
            "form": form,
            "title": "Создать пакет из файлов",
            "cancel_url": (
                reverse("document_list")
                + "?mode=grouped&unpacked=1"
            ),
            "selected_documents": documents,
            "context_label": _relationship_context_label(
                first.organization,
                first.counterparty,
                first.contract,
            ),
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
    links = list(
        OrganizationCounterpartyLink.objects.filter(
            organization=organization,
            archived=False,
        )
        .select_related(
            "organization",
            "counterparty",
            "counterparty__linked_organization",
        )
        .order_by("counterparty__name", "pk")
    )

    party_options = _build_party_options(
        organization,
        contracts,
        documents,
        operations,
        reminders,
        links=links,
    )
    requested_party = request.GET.get("party", "").strip()
    selected_party = next(
        (
            row
            for row in party_options
            if row["key"] == requested_party
        ),
        None,
    )

    linked_counterparty_ids = {
        link.counterparty_id
        for link in links
    }
    available_counterparties = (
        Counterparty.objects.filter(archived=False)
        .exclude(pk__in=linked_counterparty_ids)
        .exclude(linked_organization=organization)
        .select_related("linked_organization")
        .order_by("name")
    )

    context = {
        "organization": organization,
        "organizations": _organization_queryset(),
        "party_options": party_options,
        "available_counterparties": available_counterparties,
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
        "relationship_empty": False,
    }

    if selected_party is None:
        return render(
            request,
            "inventory/documents/organization_workspace.html",
            context,
        )

    party_key = selected_party["key"]
    party_contracts = [
        item
        for item in contracts
        if _relative_party_key(organization, item) == party_key
    ]
    party_documents = [
        item
        for item in documents
        if _relative_party_key(organization, item) == party_key
    ]
    party_operations = [
        item
        for item in operations
        if _relative_party_key(organization, item) == party_key
    ]
    party_reminders = [
        item
        for item in reminders
        if _relative_party_key(organization, item) == party_key
    ]

    docs_by_operation = {}
    for document in party_documents:
        if document.operation_id:
            docs_by_operation.setdefault(
                document.operation_id,
                [],
            ).append(document)

    contract_groups = []
    for contract in party_contracts:
        contract_documents = [
            item
            for item in party_documents
            if item.contract_id == contract.pk
            and item.operation_id is None
        ]
        contract_operations = [
            item
            for item in party_operations
            if item.contract_id == contract.pk
        ]
        operation_rows = [
            {
                "object": operation,
                "documents": docs_by_operation.get(
                    operation.pk,
                    [],
                ),
                "document_count": len(
                    docs_by_operation.get(operation.pk, [])
                ),
            }
            for operation in contract_operations
        ]
        contract_groups.append(
            {
                "contract": contract,
                "documents": contract_documents,
                "contract_document_count": (
                    len(contract_documents)
                    + (1 if contract.main_file else 0)
                ),
                "operations": operation_rows,
                "operation_count": len(operation_rows),
            }
        )

    no_contract_operation_rows = [
        {
            "object": operation,
            "documents": docs_by_operation.get(operation.pk, []),
            "document_count": len(
                docs_by_operation.get(operation.pk, [])
            ),
        }
        for operation in party_operations
        if operation.contract_id is None
    ]

    no_contract_documents = [
        item
        for item in party_documents
        if item.contract_id is None
        and item.operation_id is None
    ]

    relationship_empty = not (
        party_contracts
        or party_documents
        or party_operations
        or party_reminders
    )

    context.update(
        {
            "selected_contracts_total": len(party_contracts),
            "selected_documents_total": len(party_documents),
            "selected_operations_total": len(party_operations),
            "selected_reminders_total": len(party_reminders),
            "contract_groups": contract_groups,
            "no_contract_operations": no_contract_operation_rows,
            "no_contract_documents": no_contract_documents,
            "relationship_empty": relationship_empty,
        }
    )

    return render(
        request,
        "inventory/documents/organization_workspace.html",
        context,
    )


@login_required
@require_POST
def organization_party_add(request, pk):
    organization = get_object_or_404(
        _organization_queryset(),
        pk=pk,
    )

    existing_id = request.POST.get(
        "existing_counterparty",
        "",
    ).strip()

    created = False
    reused = False

    if existing_id:
        counterparty = get_object_or_404(
            Counterparty.objects.filter(archived=False),
            pk=existing_id,
        )
        if counterparty.linked_organization_id == organization.pk:
            messages.error(
                request,
                "Нельзя добавить организацию как вторую сторону самой себе.",
            )
            return redirect(
                "organization_document_workspace",
                pk=organization.pk,
            )
    else:
        name = request.POST.get("name", "").strip()
        short_name = request.POST.get(
            "short_name",
            "",
        ).strip()
        inn = request.POST.get("inn", "").strip()
        kpp = request.POST.get("kpp", "").strip()
        email = request.POST.get("email", "").strip()

        if not name:
            messages.error(
                request,
                "Укажите наименование компании или выберите существующую.",
            )
            return redirect(
                "organization_document_workspace",
                pk=organization.pk,
            )

        counterparty = _find_existing_counterparty(
            name=name,
            inn=inn,
        )
        if counterparty is None:
            counterparty = Counterparty.objects.create(
                name=name,
                short_name=short_name,
                inn=inn,
                kpp=kpp,
                email=email,
            )
            created = True
        else:
            reused = True

    _ensure_party_link(
        organization,
        counterparty,
        request.user,
    )

    descriptor = _relative_party_descriptor(
        organization,
        organization,
        counterparty,
    )

    if descriptor is None:
        messages.error(
            request,
            "Не удалось создать связь с этой стороной.",
        )
        return redirect(
            "organization_document_workspace",
            pk=organization.pk,
        )

    if created:
        messages.success(
            request,
            f"{counterparty} добавлен. Теперь выберите, что оформляем.",
        )
    elif reused:
        messages.info(
            request,
            f"Нашлась существующая карточка {counterparty}. Она подключена без дубля.",
        )
    else:
        messages.success(
            request,
            f"{counterparty} подключён к {organization}.",
        )

    target = reverse(
        "organization_document_workspace",
        args=[organization.pk],
    )
    target += f"?party={descriptor['key']}"
    return redirect(target)


@login_required
@require_POST
def organization_party_unlink(request, pk, counterparty_pk):
    organization = get_object_or_404(
        _organization_queryset(),
        pk=pk,
    )
    counterparty = get_object_or_404(
        Counterparty.objects.select_related("linked_organization"),
        pk=counterparty_pk,
        archived=False,
    )
    link = get_object_or_404(
        OrganizationCounterpartyLink,
        organization=organization,
        counterparty=counterparty,
        archived=False,
    )

    if counterparty.linked_organization_id:
        messages.error(
            request,
            "Связь между нашими внутренними организациями является системной и не отключается.",
        )
        return redirect(
            "organization_document_workspace",
            pk=organization.pk,
        )

    descriptor = _relative_party_descriptor(
        organization,
        organization,
        counterparty,
    )
    party_key = descriptor["key"] if descriptor else None

    has_data = DocumentRecord.objects.filter(
        organization=organization,
        counterparty=counterparty,
    ).exists()
    if party_key and not has_data:
        for queryset in (
            _contracts_for_organization(organization),
            _documents_for_organization(organization),
            _operations_for_organization(organization),
            _reminders_for_organization(organization),
        ):
            if any(
                _relative_party_key(organization, item) == party_key
                for item in queryset.select_related(
                    "organization",
                    "counterparty",
                )
            ):
                has_data = True
                break

    if has_data:
        messages.error(
            request,
            (
                "Эту сторону нельзя отключить: с ней уже есть "
                "договоры, пакеты, документы или напоминания."
            ),
        )
        target = reverse(
            "organization_document_workspace",
            args=[organization.pk],
        )
        if descriptor:
            target += f"?party={descriptor['key']}"
        return redirect(target)

    link.archived = True
    link.save(update_fields=["archived", "updated_at"])

    linked_org = counterparty.linked_organization
    owner_profile = getattr(
        organization,
        "counterparty_profile",
        None,
    )
    if linked_org is not None and owner_profile is not None:
        OrganizationCounterpartyLink.objects.filter(
            organization=linked_org,
            counterparty=owner_profile,
            archived=False,
        ).update(archived=True)

    messages.success(
        request,
        f"{counterparty} отключён от {organization}.",
    )
    return redirect(
        "organization_document_workspace",
        pk=organization.pk,
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

    if organization is not None:
        documents = _documents_for_organization(organization)
        contracts = _contracts_for_organization(organization)
        operations = _operations_for_organization(organization)
        reminders = _reminders_for_organization(organization)
        counterparties = Counterparty.objects.filter(
            organization_links__organization=organization,
            organization_links__archived=False,
            archived=False,
        ).distinct()
    else:
        documents = DocumentRecord.objects.filter(
            trashed_at__isnull=True
        )
        contracts = Contract.objects.filter(archived=False)
        operations = DocumentOperation.objects.all()
        reminders = Reminder.objects.filter(active=True)
        counterparties = Counterparty.objects.filter(archived=False)

    reminders = reminders.select_related(
        "organization",
        "counterparty",
        "contract",
        "location",
    )

    today = timezone.localdate()
    visible_reminders = reminder_rows(
        reminders.order_by("next_due_date", "pk")[:8],
        today=today,
    )

    main_contract_files = contracts.exclude(main_file="").count()
    document_files = documents.count()
    attention = documents.filter(
        Q(document_type__isnull=True)
        | Q(
            classification_source=(
                DocumentRecord.ClassificationSource.FILENAME
            )
        )
    ).count()

    return render(
        request,
        "inventory/documents/center.html",
        {
            "organizations": _organization_queryset(),
            "selected_organization": organization,
            "files_total": document_files + main_contract_files,
            "document_records_total": document_files,
            "packages_total": operations.count(),
            "contracts_total": contracts.count(),
            "counterparties_total": counterparties.count(),
            "attention_total": attention,
            "reminders_total": reminders.count(),
            "recent_documents": (
                documents.select_related(
                    "organization",
                    "document_type",
                    "counterparty",
                    "contract",
                    "operation",
                )
                .order_by("-created_at")[:8]
            ),
            "recent_contracts": contracts.select_related(
                "organization",
                "counterparty",
                "location",
            )[:6],
            "reminder_rows": visible_reminders,
        },
    )


@login_required
def document_list(request):
    q = request.GET.get("q", "").strip()
    organization_raw = request.GET.get("organization", "").strip()
    document_type = request.GET.get("type", "").strip()
    counterparty = request.GET.get("counterparty", "").strip()
    contract = request.GET.get("contract", "").strip()
    year = request.GET.get("year", "").strip()
    mode = request.GET.get("mode", "grouped").strip()
    only_unpacked = request.GET.get("unpacked") == "1"
    if mode not in {"grouped", "files"}:
        mode = "grouped"

    selected_org = None
    if organization_raw.isdigit():
        selected_org = _organization_queryset().filter(
            pk=int(organization_raw)
        ).first()

    if selected_org is not None:
        qs = _documents_for_organization(selected_org)
    else:
        qs = DocumentRecord.objects.filter(
            trashed_at__isnull=True
        )

    qs = qs.select_related(
        "organization",
        "document_type",
        "counterparty",
        "counterparty__linked_organization",
        "contract",
        "operation",
        "location",
    )

    if q:
        qs = qs.filter(
            Q(title__icontains=q)
            | Q(number__icontains=q)
            | Q(original_name__icontains=q)
            | Q(notes__icontains=q)
            | Q(counterparty__name__icontains=q)
            | Q(counterparty__inn__icontains=q)
            | Q(contract__title__icontains=q)
            | Q(contract__number__icontains=q)
            | Q(operation__title__icontains=q)
        )
    if document_type == "attention":
        qs = qs.filter(
            Q(document_type__isnull=True)
            | Q(
                classification_source=(
                    DocumentRecord.ClassificationSource.FILENAME
                )
            )
        )
    elif document_type:
        qs = qs.filter(document_type_id=document_type)
    if counterparty:
        qs = qs.filter(counterparty_id=counterparty)
    if contract:
        qs = qs.filter(contract_id=contract)
    if year.isdigit():
        qs = qs.filter(document_date__year=int(year))
    if only_unpacked:
        qs = qs.filter(operation__isnull=True)

    qs = qs.order_by(
        "-document_date",
        "-created_at",
        "-pk",
    )
    matching_files_total = qs.count()

    years = list(
        DocumentRecord.objects.filter(
            trashed_at__isnull=True,
            document_date__isnull=False,
        ).dates(
            "document_date",
            "year",
            order="DESC",
        )
    )

    grouped_rows = []
    page_obj = None
    query_string = ""

    if mode == "files":
        page_obj, query_string = _paginate(request, qs, 60)
        objects = page_obj.object_list
        logical_total = matching_files_total
    else:
        matching_documents = list(qs)
        operation_ids = {
            item.operation_id
            for item in matching_documents
            if item.operation_id
        }
        operations = {
            item.pk: item
            for item in (
                DocumentOperation.objects.filter(
                    pk__in=operation_ids
                )
                .select_related(
                    "organization",
                    "counterparty",
                    "counterparty__linked_organization",
                    "contract",
                )
                .annotate(
                    active_document_count=Count(
                        "documents",
                        filter=Q(
                            documents__trashed_at__isnull=True
                        ),
                        distinct=True,
                    )
                )
            )
        }

        seen_operations = set()
        for document in matching_documents:
            if document.operation_id:
                if document.operation_id in seen_operations:
                    continue
                seen_operations.add(document.operation_id)
                operation = operations.get(document.operation_id)
                if operation is None:
                    continue
                grouped_rows.append(
                    {
                        "kind": "package",
                        "operation": operation,
                        "date": (
                            operation.operation_date
                            or document.document_date
                            or operation.created_at.date()
                        ),
                        "documents": list(
                            operation.documents.filter(
                                trashed_at__isnull=True
                            )
                            .select_related("document_type")
                            .order_by("document_date", "pk")
                        ),
                    }
                )
            else:
                grouped_rows.append(
                    {
                        "kind": "document",
                        "document": document,
                        "date": (
                            document.document_date
                            or document.created_at.date()
                        ),
                    }
                )

        grouped_rows.sort(
            key=lambda row: (
                row["date"],
                (
                    row["operation"].pk
                    if row["kind"] == "package"
                    else row["document"].pk
                ),
            ),
            reverse=True,
        )
        page_obj, query_string = _paginate(
            request,
            grouped_rows,
            40,
        )
        grouped_rows = page_obj.object_list
        objects = []
        logical_total = page_obj.paginator.count

    return render(
        request,
        "inventory/documents/document_list.html",
        {
            "objects": objects,
            "grouped_rows": grouped_rows,
            "page_obj": page_obj,
            "query_string": query_string,
            "mode": mode,
            "q": q,
            "selected_organization": organization_raw,
            "selected_type": document_type,
            "selected_counterparty": counterparty,
            "selected_contract": contract,
            "selected_year": year,
            "only_unpacked": only_unpacked,
            "matching_files_total": matching_files_total,
            "logical_total": logical_total,
            "organizations": _organization_queryset(),
            "document_types": DocumentType.objects.filter(
                archived=False
            ),
            "counterparties": Counterparty.objects.filter(
                archived=False
            ),
            "contracts": Contract.objects.filter(
                archived=False
            ).select_related(
                "organization",
                "counterparty",
            ),
            "years": years,
        },
    )


@login_required
def document_inbox(request):
    form = InboxUploadForm(
        request.POST or None,
        request.FILES or None,
    )

    if form.is_valid():
        organization = form.cleaned_data["organization"]
        uploaded_files = form.cleaned_data["files"]
        prepared = []
        selected_hashes = set()

        for uploaded in uploaded_files:
            file_hash = _file_sha256(uploaded)
            if file_hash in selected_hashes:
                form.add_error(
                    "files",
                    f"{uploaded.name}: этот же файл выбран дважды.",
                )
                break

            duplicate = _duplicate_document_for_hash(file_hash)
            if duplicate is not None:
                form.add_error(
                    "files",
                    (
                        f"{uploaded.name}: такой файл уже есть как "
                        f"«{duplicate.display_title}»."
                    ),
                )
                break

            selected_hashes.add(file_hash)
            prepared.append((uploaded, file_hash))

        if not form.errors:
            with transaction.atomic():
                for uploaded, file_hash in prepared:
                    document = DocumentRecord.objects.create(
                        organization=organization,
                        classification_source=(
                            DocumentRecord.ClassificationSource.UNKNOWN
                        ),
                        file=uploaded,
                        original_name=uploaded.name,
                        file_sha256=file_hash,
                        created_by=request.user,
                    )
                    _log_document_activity(
                        action="uploaded_attention",
                        message=(
                            f"Файл «{uploaded.name}» загружен "
                            "в очередь разбора."
                        ),
                        user=request.user,
                        document=document,
                    )

            messages.success(
                request,
                f"В очередь разбора добавлено файлов: {len(prepared)}.",
            )
            return redirect("document_inbox")

    attention_qs = (
        DocumentRecord.objects.filter(
            trashed_at__isnull=True,
        )
        .filter(
            Q(document_type__isnull=True)
            | Q(
                classification_source=(
                    DocumentRecord.ClassificationSource.FILENAME
                )
            )
        )
        .select_related(
            "organization",
            "counterparty",
            "document_type",
            "operation",
            "contract",
        )
        .order_by("-created_at", "-pk")
    )
    page_obj, query_string = _paginate(
        request,
        attention_qs,
        50,
    )

    return render(
        request,
        "inventory/documents/inbox.html",
        {
            "form": form,
            "objects": page_obj.object_list,
            "page_obj": page_obj,
            "query_string": query_string,
            "attention_total": attention_qs.count(),
        },
    )


@login_required
def document_upload(request):
    initial_contract = None
    initial_operation = None
    initial_organization = None
    initial_counterparty = None
    initial_location = None
    initial_equipment = None

    if request.GET.get("operation"):
        initial_operation = get_object_or_404(
            DocumentOperation.objects.select_related(
                "organization",
                "counterparty",
                "contract",
                "location",
            ),
            pk=request.GET["operation"],
        )
        initial_organization = initial_operation.organization
        initial_counterparty = initial_operation.counterparty
        initial_contract = initial_operation.contract
    elif request.GET.get("contract"):
        initial_contract = get_object_or_404(
            Contract.objects.select_related(
                "organization",
                "counterparty",
                "location",
            ),
            pk=request.GET["contract"],
        )
        initial_organization = initial_contract.organization
        initial_counterparty = initial_contract.counterparty
    elif request.GET.get("equipment"):
        initial_equipment = get_object_or_404(
            Equipment.objects.select_related(
                "owner",
                "location",
            ),
            pk=request.GET["equipment"],
            archived=False,
        )
        initial_organization = initial_equipment.owner
    elif request.GET.get("location"):
        initial_location = get_object_or_404(
            Location.objects.select_related("organization"),
            pk=request.GET["location"],
            archived=False,
        )
        initial_organization = initial_location.organization
    elif request.GET.get("organization"):
        initial_organization = get_object_or_404(
            _organization_queryset(),
            pk=request.GET["organization"],
        )

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

    if form.is_bound:
        if initial_operation is not None:
            form.initial.update(
                {
                    "organization": initial_operation.organization_id,
                    "counterparty": initial_operation.counterparty_id,
                    "contract": initial_operation.contract_id,
                    "operation": initial_operation.pk,
                    "location": initial_operation.location_id,
                }
            )
        elif initial_contract is not None:
            form.initial.update(
                {
                    "organization": initial_contract.organization_id,
                    "counterparty": initial_contract.counterparty_id,
                    "contract": initial_contract.pk,
                    "location": initial_contract.location_id,
                }
            )
        else:
            if initial_organization is not None:
                form.initial["organization"] = initial_organization.pk
            if initial_counterparty is not None:
                form.initial["counterparty"] = initial_counterparty.pk

    if initial_organization is not None:
        form.fields["organization"].disabled = True
    if initial_counterparty is not None:
        form.fields["counterparty"].disabled = True
    if initial_contract is not None:
        form.fields["contract"].disabled = True
    if initial_operation is not None:
        form.fields["operation"].disabled = True

    if form.is_valid():
        data = form.cleaned_data
        uploaded_files = data["files"]
        equipment = list(data["equipment"])
        prepared = []
        selected_hashes = set()

        for uploaded in uploaded_files:
            file_hash = _file_sha256(uploaded)
            if file_hash in selected_hashes:
                form.add_error(
                    "files",
                    f"{uploaded.name}: этот же файл выбран дважды.",
                )
                break

            duplicate = _duplicate_document_for_hash(file_hash)
            if duplicate is not None:
                form.add_error(
                    "files",
                    (
                        f"{uploaded.name}: такой файл уже есть "
                        f"как «{duplicate.display_title}»."
                    ),
                )
                break

            selected_hashes.add(file_hash)
            document_type = data.get("document_type")
            source = DocumentRecord.ClassificationSource.MANUAL
            if document_type is None:
                document_type = _infer_document_type_from_filename(
                    uploaded.name
                )
                source = (
                    DocumentRecord.ClassificationSource.FILENAME
                    if document_type is not None
                    else DocumentRecord.ClassificationSource.UNKNOWN
                )
            prepared.append(
                (
                    uploaded,
                    file_hash,
                    document_type,
                    source,
                )
            )

        if not form.errors:
            created = []
            _ensure_party_link(
                data["organization"],
                data.get("counterparty"),
                request.user,
            )

            with transaction.atomic():
                for (
                    uploaded,
                    file_hash,
                    document_type,
                    source,
                ) in prepared:
                    document = DocumentRecord.objects.create(
                        organization=data["organization"],
                        document_type=document_type,
                        classification_source=source,
                        counterparty=data.get("counterparty"),
                        contract=data.get("contract"),
                        operation=data.get("operation"),
                        location=data.get("location"),
                        title=(
                            data.get("title", "")
                            if len(prepared) == 1
                            else ""
                        ),
                        number=(
                            data.get("number", "")
                            if len(prepared) == 1
                            else ""
                        ),
                        document_date=(
                            data.get("document_date")
                            if len(prepared) == 1
                            else None
                        ),
                        amount=(
                            data.get("amount")
                            if len(prepared) == 1
                            else None
                        ),
                        file=uploaded,
                        original_name=uploaded.name,
                        file_sha256=file_hash,
                        notes=data.get("notes", ""),
                        created_by=request.user,
                    )
                    if equipment:
                        document.equipment.set(equipment)
                    created.append(document)
                    _log_document_activity(
                        action="uploaded",
                        message=f"Загружен файл «{uploaded.name}».",
                        user=request.user,
                        document=document,
                    )

            messages.success(
                request,
                f"Загружено файлов: {len(created)}.",
            )

            viewpoint = request.GET.get("view", "").strip()
            view_query = (
                f"?view={viewpoint}"
                if viewpoint.isdigit()
                else ""
            )

            if initial_operation is not None:
                return redirect(
                    initial_operation.get_absolute_url()
                    + view_query
                )
            if initial_contract is not None and len(created) > 1:
                return redirect(
                    initial_contract.get_absolute_url()
                    + view_query
                )
            if len(created) == 1:
                target = created[0].get_absolute_url()
                if viewpoint.isdigit():
                    target += view_query
                return redirect(target)

            return redirect(
                reverse("document_list") + "?mode=grouped"
            )

    if initial_operation is not None:
        cancel_url = initial_operation.get_absolute_url()
        if request.GET.get("view", "").isdigit():
            cancel_url += f"?view={request.GET['view']}"
    elif initial_contract is not None:
        cancel_url = initial_contract.get_absolute_url()
        if request.GET.get("view", "").isdigit():
            cancel_url += f"?view={request.GET['view']}"
    elif initial_organization and initial_counterparty:
        descriptor = _relative_party_descriptor(
            initial_organization,
            initial_organization,
            initial_counterparty,
        )
        cancel_url = reverse(
            "organization_document_workspace",
            args=[initial_organization.pk],
        )
        if descriptor:
            cancel_url += f"?party={descriptor['key']}"
    elif initial_organization:
        cancel_url = reverse(
            "organization_document_workspace",
            args=[initial_organization.pk],
        )
    elif request.GET.get("view", "").isdigit():
        cancel_url = reverse(
            "organization_document_workspace",
            args=[request.GET["view"]],
        )
    else:
        cancel_url = reverse("document_list")

    context_label = _relationship_context_label(
        initial_organization,
        initial_counterparty,
        initial_contract,
        initial_operation,
    )

    return render(
        request,
        "inventory/documents/document_form.html",
        {
            "form": form,
            "title": "Загрузить документы",
            "multiple": True,
            "cancel_url": cancel_url,
            "context_label": context_label,
        },
    )


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
        ).prefetch_related("equipment", "file_versions"),
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
        back_label = "К пакету"
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
        back_label = "К документам стороны"
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
    extension = (
        Path(obj.file.name).suffix.lower().lstrip(".")
        if obj.file
        else ""
    )

    return render(
        request,
        "inventory/documents/document_detail.html",
        {
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
            "sequence_position": (
                sequence_index + 1
                if sequence
                else 1
            ),
            "sequence_total": len(sequence) if sequence else 1,
            "sequence_label": sequence_label,
            "file_versions": obj.file_versions.select_related(
                "created_by"
            )[:10],
            "activity_rows": obj.activity.select_related(
                "actor"
            )[:20],
            "edit_url": (
                reverse("document_edit", args=[obj.pk])
                + view_query
            ),
        },
    )


@login_required
def document_edit(request, pk):
    obj = get_object_or_404(
        DocumentRecord.objects.select_related(
            "organization",
            "counterparty",
            "contract",
            "operation",
            "document_type",
        ),
        pk=pk,
        trashed_at__isnull=True,
    )
    viewpoint = _relationship_viewpoint(
        request,
        obj.organization,
        obj.counterparty,
    )
    fallback_url = (
        obj.get_absolute_url()
        + f"?view={viewpoint.pk}"
    )
    old_file_name = obj.file.name if obj.file else ""
    old_original_name = obj.original_name
    old_hash = obj.file_sha256
    old_type_id = obj.document_type_id
    old_operation_id = obj.operation_id

    form = DocumentEditForm(
        request.POST or None,
        request.FILES or None,
        instance=obj,
    )

    if form.is_valid():
        uploaded = request.FILES.get("file")
        new_hash = old_hash
        if uploaded is not None:
            new_hash = _file_sha256(uploaded)
            duplicate = _duplicate_document_for_hash(
                new_hash,
                exclude_pk=obj.pk,
            )
            if duplicate is not None:
                form.add_error(
                    "file",
                    (
                        "Такой файл уже есть в FOX Inventory "
                        f"как «{duplicate.display_title}»."
                    ),
                )

        if not form.errors:
            with transaction.atomic():
                saved = form.save(commit=False)
                if saved.document_type_id:
                    saved.classification_source = (
                        DocumentRecord.ClassificationSource.MANUAL
                    )
                else:
                    saved.classification_source = (
                        DocumentRecord.ClassificationSource.UNKNOWN
                    )
                if uploaded is not None:
                    saved.original_name = uploaded.name
                    saved.file_sha256 = new_hash
                saved.save()
                form.save_m2m()

                if (
                    uploaded is not None
                    and old_file_name
                    and old_file_name != saved.file.name
                ):
                    DocumentFileVersion.objects.create(
                        document=saved,
                        file=old_file_name,
                        original_name=old_original_name,
                        file_sha256=old_hash,
                        created_by=request.user,
                    )

                changes = []
                if old_type_id != saved.document_type_id:
                    changes.append("тип документа")
                if old_operation_id != saved.operation_id:
                    changes.append("пакет")
                if uploaded is not None:
                    changes.append("файл")
                message = (
                    "Изменены: " + ", ".join(changes) + "."
                    if changes
                    else "Изменены реквизиты документа."
                )
                _log_document_activity(
                    action="edited",
                    message=message,
                    user=request.user,
                    document=saved,
                )

            messages.success(request, "Документ сохранён.")
            return redirect(fallback_url)

    return render(
        request,
        "inventory/documents/document_form.html",
        {
            "form": form,
            "title": "Изменить документ",
            "object": obj,
            "cancel_url": fallback_url,
            "context_label": _relationship_context_label(
                obj.organization,
                obj.counterparty,
                obj.contract,
                obj.operation,
            ),
            "multiple": False,
        },
    )


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
@xframe_options_sameorigin
def document_version_preview(request, pk, version_pk):
    version = get_object_or_404(
        DocumentFileVersion.objects.select_related("document"),
        pk=version_pk,
        document_id=pk,
    )
    if _preview_kind(version.file) not in {"pdf", "image"}:
        raise Http404
    filename = (
        version.original_name
        or Path(version.file.name).name
    )
    return _storage_file_response(
        version.file,
        "inline",
        filename,
    )


@login_required
def document_version_download(request, pk, version_pk):
    version = get_object_or_404(
        DocumentFileVersion.objects.select_related("document"),
        pk=version_pk,
        document_id=pk,
    )
    filename = (
        version.original_name
        or Path(version.file.name).name
    )
    return _storage_file_response(
        version.file,
        "attachment",
        filename,
    )


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
    filename = obj.main_file_original_name or Path(obj.main_file.name).name

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
        obj.main_file_original_name or Path(obj.main_file.name).name,
    )


@login_required
def contract_file_download(request, pk):
    obj = get_object_or_404(Contract, pk=pk)
    if not obj.main_file:
        raise Http404
    return _storage_file_response(
        obj.main_file,
        "attachment",
        obj.main_file_original_name or Path(obj.main_file.name).name,
    )


@login_required
@require_POST
def document_trash(request, pk):
    obj = get_object_or_404(
        DocumentRecord,
        pk=pk,
        trashed_at__isnull=True,
    )
    fallback = reverse("document_list")
    if obj.operation_id:
        fallback = obj.operation.get_absolute_url()
    elif obj.contract_id:
        fallback = obj.contract.get_absolute_url()

    obj.trashed_at = timezone.now()
    obj.save(update_fields=["trashed_at", "updated_at"])
    _log_document_activity(
        action="trashed",
        message=f"«{obj.display_title}» перемещён в корзину.",
        user=request.user,
        document=obj,
    )
    messages.success(
        request,
        "Документ перемещён в корзину.",
    )
    return redirect(
        _safe_return_url(
            request,
            fallback,
        )
    )


@login_required
def document_trash_list(request):
    qs = (
        DocumentRecord.objects.filter(
            trashed_at__isnull=False
        )
        .select_related(
            "organization",
            "document_type",
            "counterparty",
            "contract",
            "operation",
        )
        .order_by("-trashed_at", "-pk")
    )
    page_obj, query_string = _paginate(
        request,
        qs,
        50,
    )
    return render(
        request,
        "inventory/documents/trash.html",
        {
            "objects": page_obj.object_list,
            "page_obj": page_obj,
            "query_string": query_string,
        },
    )


@login_required
@require_POST
def document_restore(request, pk):
    obj = get_object_or_404(
        DocumentRecord,
        pk=pk,
        trashed_at__isnull=False,
    )
    obj.trashed_at = None
    obj.save(update_fields=["trashed_at", "updated_at"])
    _log_document_activity(
        action="restored",
        message=f"«{obj.display_title}» восстановлен из корзины.",
        user=request.user,
        document=obj,
    )
    messages.success(request, "Документ восстановлен.")
    return redirect(
        _safe_return_url(
            request,
            obj.get_absolute_url(),
        )
    )


@login_required
@require_POST
def document_delete_permanently(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden("Недостаточно прав.")
    obj = get_object_or_404(
        DocumentRecord.objects.prefetch_related("file_versions"),
        pk=pk,
        trashed_at__isnull=False,
    )
    file_names = set()
    if obj.file and obj.file.name:
        file_names.add(obj.file.name)
    for version in obj.file_versions.all():
        if version.file and version.file.name:
            file_names.add(version.file.name)

    obj.delete()
    for file_name in file_names:
        if default_storage.exists(file_name):
            default_storage.delete(file_name)

    messages.success(
        request,
        "Документ и сохранённые версии удалены окончательно.",
    )
    return redirect("document_trash_list")


@login_required
def counterparty_list(request):
    q = request.GET.get("q", "").strip()
    show_archived = request.GET.get("archived") == "1"
    qs = (
        Counterparty.objects.filter(archived=show_archived)
        .select_related("linked_organization")
        .order_by("name")
    )
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(short_name__icontains=q)
            | Q(inn__icontains=q)
        )

    objects = list(qs)
    for item in objects:
        if item.linked_organization_id:
            item.contract_count = _contracts_for_organization(
                item.linked_organization,
            ).count()
            item.document_count = _documents_for_organization(
                item.linked_organization,
            ).count()
        else:
            item.contract_count = item.contracts.filter(
                archived=False
            ).count()
            item.document_count = item.documents.filter(
                trashed_at__isnull=True
            ).count()

    return render(
        request,
        "inventory/documents/counterparty_list.html",
        {
            "objects": objects,
            "q": q,
            "show_archived": show_archived,
        },
    )


@login_required
def counterparty_detail(request, pk):
    obj = get_object_or_404(
        Counterparty.objects.select_related(
            "linked_organization"
        ),
        pk=pk,
    )

    if obj.linked_organization_id:
        contracts = _contracts_for_organization(
            obj.linked_organization,
        ).select_related(
            "organization",
            "counterparty",
            "location",
        )
        documents = _documents_for_organization(
            obj.linked_organization,
        ).select_related(
            "organization",
            "counterparty",
            "document_type",
            "contract",
            "operation",
        )
    else:
        contracts = obj.contracts.select_related(
            "organization",
            "location",
        ).filter(archived=False)
        documents = obj.documents.select_related(
            "organization",
            "document_type",
            "contract",
            "operation",
        ).filter(trashed_at__isnull=True)

    page_obj, query_string = _paginate(
        request,
        documents.order_by(
            "-document_date",
            "-created_at",
            "-pk",
        ),
        50,
    )

    return render(
        request,
        "inventory/documents/counterparty_detail.html",
        {
            "object": obj,
            "contracts": contracts,
            "documents": page_obj.object_list,
            "page_obj": page_obj,
            "query_string": query_string,
        },
    )


@login_required
def counterparty_form(request, pk=None):
    obj = get_object_or_404(
        Counterparty,
        pk=pk,
    ) if pk else None
    form = CounterpartyForm(
        request.POST or None,
        instance=obj,
    )

    return_url = None
    organization = None
    organization_raw = request.GET.get(
        "organization",
        "",
    ).strip()
    if organization_raw.isdigit():
        organization = _organization_queryset().filter(
            pk=int(organization_raw)
        ).first()

    if organization is not None and obj is not None:
        descriptor = _relative_party_descriptor(
            organization,
            organization,
            obj,
        )
        if descriptor:
            return_url = reverse(
                "organization_document_workspace",
                args=[organization.pk],
            )
            return_url += f"?party={descriptor['key']}"

    if form.is_valid():
        saved = form.save()
        if organization is not None:
            _ensure_party_link(
                organization,
                saved,
                request.user,
            )
        messages.success(
            request,
            "Карточка стороны сохранена.",
        )
        return redirect(
            return_url or saved.get_absolute_url()
        )

    return render(
        request,
        "inventory/documents/simple_form.html",
        {
            "form": form,
            "title": "Карточка стороны",
            "form_eyebrow": "Контрагент",
            "form_intro": (
                "Реквизиты компании используются во всех договорах "
                "и документах. Изменения применяются к одной карточке, "
                "а не создают дубликат."
            ),
            "cancel_url": (
                return_url
                or (
                    obj.get_absolute_url()
                    if obj
                    else reverse("counterparty_list")
                )
            ),
        },
    )


@login_required
def contract_list(request):
    q = request.GET.get("q", "").strip()
    organization_raw = request.GET.get(
        "organization",
        "",
    ).strip()
    category = request.GET.get("category", "").strip()
    show_archived = request.GET.get("archived") == "1"

    selected_org = None
    if organization_raw.isdigit():
        selected_org = _organization_queryset().filter(
            pk=int(organization_raw)
        ).first()

    if selected_org is not None:
        qs = _contracts_for_organization(
            selected_org,
            archived=show_archived,
        )
    else:
        qs = Contract.objects.filter(
            archived=show_archived
        )

    qs = (
        qs.select_related(
            "organization",
            "counterparty",
            "counterparty__linked_organization",
            "location",
            "responsible_employee",
        )
        .annotate(
            document_count=Count(
                "documents",
                filter=Q(
                    documents__trashed_at__isnull=True
                ),
                distinct=True,
            ),
            reminder_count=Count(
                "reminders",
                filter=Q(reminders__active=True),
                distinct=True,
            ),
        )
    )

    if q:
        qs = qs.filter(
            Q(title__icontains=q)
            | Q(number__icontains=q)
            | Q(counterparty__name__icontains=q)
            | Q(organization__name__icontains=q)
        )
    if category in dict(Contract.Category.choices):
        qs = qs.filter(category=category)

    return render(
        request,
        "inventory/documents/contract_list.html",
        {
            "objects": qs,
            "q": q,
            "organizations": _organization_queryset(),
            "selected_organization": organization_raw,
            "categories": Contract.Category.choices,
            "selected_category": category,
            "show_archived": show_archived,
        },
    )


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

    operations = list(
        obj.operations.select_related(
            "organization", "counterparty", "location"
        )
        .annotate(
            document_count=Count(
                "documents",
                filter=Q(documents__trashed_at__isnull=True),
                distinct=True,
            )
        )
        .prefetch_related(
            Prefetch(
                "documents",
                queryset=DocumentRecord.objects.filter(
                    trashed_at__isnull=True
                ).select_related("document_type").order_by("document_date", "pk"),
                to_attr="active_documents",
            )
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
    obj = get_object_or_404(
        Contract.objects.select_related(
            "organization",
            "counterparty",
        ),
        pk=pk,
    ) if pk else None

    initial = {}
    initial_organization = None
    initial_counterparty = None

    if obj is None and request.GET.get("organization"):
        initial_organization = get_object_or_404(
            _organization_queryset(),
            pk=request.GET["organization"],
        )
        initial["organization"] = initial_organization.pk

    if obj is None and request.GET.get("counterparty"):
        initial_counterparty = get_object_or_404(
            Counterparty.objects.filter(archived=False),
            pk=request.GET["counterparty"],
        )
        initial["counterparty"] = initial_counterparty.pk

    form = ContractForm(
        request.POST or None,
        request.FILES or None,
        instance=obj,
        initial=initial,
    )

    if obj is None:
        if initial_organization is not None:
            form.fields["organization"].disabled = True
        if initial_counterparty is not None:
            form.fields["counterparty"].disabled = True

    if obj is not None:
        context_organization = obj.organization
        context_counterparty = obj.counterparty
    else:
        context_organization = initial_organization
        context_counterparty = initial_counterparty

    relationship_url = None
    if context_organization and context_counterparty:
        descriptor = _relative_party_descriptor(
            context_organization,
            context_organization,
            context_counterparty,
        )
        if descriptor:
            relationship_url = reverse(
                "organization_document_workspace",
                args=[context_organization.pk],
            )
            relationship_url += f"?party={descriptor['key']}"

    if form.is_valid():
        saved = form.save(commit=False)
        if saved.pk is None:
            saved.created_by = request.user
        uploaded_main_file = request.FILES.get("main_file")
        if uploaded_main_file is not None:
            saved.main_file_original_name = uploaded_main_file.name
        saved.save()
        form.save_m2m()

        _ensure_party_link(
            saved.organization,
            saved.counterparty,
            request.user,
        )

        messages.success(
            request,
            "Договор сохранён.",
        )

        target = saved.get_absolute_url()
        viewpoint = request.GET.get("view", "").strip()
        if viewpoint.isdigit():
            target += f"?view={viewpoint}"
        return redirect(target)

    cancel_url = (
        obj.get_absolute_url()
        if obj
        else relationship_url
        or reverse("contract_list")
    )

    return render(
        request,
        "inventory/documents/simple_form.html",
        {
            "form": form,
            "title": (
                "Изменить договор"
                if obj
                else "Новый договор"
            ),
            "form_eyebrow": "Договор",
            "form_intro": (
                "Стороны уже подставлены из текущего контекста. "
                "Заполните только реквизиты самого договора."
                if relationship_url and obj is None
                else "Карточка договора, сроки, ответственный и основной файл."
            ),
            "context_label": _relationship_context_label(
                context_organization,
                context_counterparty,
            ),
            "cancel_url": cancel_url,
            "multipart": True,
        },
    )


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
