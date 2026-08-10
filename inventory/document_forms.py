from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import (
    Contract,
    Counterparty,
    DocumentOperation,
    DocumentRecord,
    DocumentType,
    Employee,
    Equipment,
    Location,
    Organization,
    Reminder,
)
from .validators import validate_business_document


class StyledFormMixin:
    def apply_styles(self):
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "checkbox")
            elif isinstance(field.widget, forms.CheckboxSelectMultiple):
                continue
            else:
                field.widget.attrs.setdefault("class", "input")


class CounterpartyForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Counterparty
        fields = ["name", "short_name", "linked_organization", "inn", "kpp", "contact_name", "phone", "email", "notes", "archived"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()
        self.fields["linked_organization"].queryset = Organization.objects.filter(archived=False, kind=Organization.Kind.COMPANY)
        self.fields["linked_organization"].empty_label = "Внешний контрагент"
        self.fields["linked_organization"].help_text = "Заполняйте только если эта сторона является нашей внутренней организацией."

    def clean(self):
        data = super().clean()
        name = (data.get("name") or "").strip()
        short_name = (data.get("short_name") or "").strip()
        inn = (data.get("inn") or "").strip()

        def key(value):
            return "".join(
                char
                for char in value.casefold()
                if char.isalnum()
            )

        qs = Counterparty.objects.all()
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        if inn and qs.filter(inn=inn).exists():
            self.add_error(
                "inn",
                "Контрагент с таким ИНН уже существует.",
            )

        name_keys = {
            value
            for value in (key(name), key(short_name))
            if value
        }
        if name_keys:
            for existing in qs.only("name", "short_name"):
                existing_keys = {
                    value
                    for value in (
                        key(existing.name or ""),
                        key(existing.short_name or ""),
                    )
                    if value
                }
                if name_keys & existing_keys:
                    self.add_error(
                        "name",
                        f"Похожая сторона уже существует: {existing}. "
                        "Откройте существующую карточку вместо создания дубля.",
                    )
                    break

        return data


class ContractForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Contract
        fields = [
            "organization", "counterparty", "title", "number", "contract_date", "category",
            "starts_at", "ends_at", "indefinite", "location", "responsible_employee",
            "main_file", "notes", "archived",
        ]
        widgets = {
            "contract_date": forms.DateInput(attrs={"type": "date"}),
            "starts_at": forms.DateInput(attrs={"type": "date"}),
            "ends_at": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()
        self.fields["organization"].queryset = Organization.objects.filter(archived=False, kind=Organization.Kind.COMPANY)
        self.fields["counterparty"].queryset = Counterparty.objects.filter(archived=False)
        self.fields["location"].queryset = Location.objects.filter(archived=False).select_related("organization")
        self.fields["responsible_employee"].queryset = Employee.objects.filter(archived=False).select_related("organization")
        self.fields["location"].empty_label = "Не связан с объектом"
        self.fields["responsible_employee"].empty_label = "Не указан"

    def clean(self):
        data = super().clean()
        organization = data.get("organization")
        location = data.get("location")
        employee = data.get("responsible_employee")
        counterparty = data.get("counterparty")
        if counterparty and organization and counterparty.linked_organization_id == organization.id:
            self.add_error("counterparty", "Организация не может заключить договор сама с собой.")
        if location and organization and location.organization_id != organization.id:
            self.add_error("location", "Объект относится к другой организации.")
        if employee and organization and employee.organization_id != organization.id:
            self.add_error("responsible_employee", "Сотрудник относится к другой организации.")
        if data.get("indefinite"):
            data["ends_at"] = None
        starts_at = data.get("starts_at")
        ends_at = data.get("ends_at")
        if starts_at and ends_at and ends_at < starts_at:
            self.add_error("ends_at", "Дата окончания не может быть раньше даты начала.")
        return data



class DocumentOperationForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = DocumentOperation
        fields = ["organization", "counterparty", "contract", "title", "operation_date", "amount", "location", "notes"]
        widgets = {
            "operation_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()
        self.fields["amount"].localize = True
        self.fields["organization"].queryset = Organization.objects.filter(archived=False, kind=Organization.Kind.COMPANY)
        self.fields["counterparty"].queryset = Counterparty.objects.filter(archived=False)
        self.fields["contract"].queryset = Contract.objects.filter(archived=False).select_related("organization", "counterparty")
        self.fields["location"].queryset = Location.objects.filter(archived=False).select_related("organization")
        self.fields["counterparty"].label = "Вторая сторона"
        self.fields["contract"].label = "Договор (необязательно)"
        self.fields["counterparty"].empty_label = "Не указан"
        self.fields["contract"].empty_label = "Без договора"
        self.fields["location"].empty_label = "Не связан"

    def clean(self):
        data = super().clean()
        organization = data.get("organization")
        counterparty = data.get("counterparty")
        contract = data.get("contract")
        location = data.get("location")
        if contract:
            data["organization"] = contract.organization
            if not counterparty:
                data["counterparty"] = contract.counterparty
            elif counterparty.pk != contract.counterparty_id:
                self.add_error("counterparty", "Контрагент не совпадает с контрагентом договора.")
            if not location and contract.location_id:
                data["location"] = contract.location
        if location and data.get("organization") and location.organization_id != data["organization"].id:
            self.add_error("location", "Объект относится к другой организации.")
        return data


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(item, initial) for item in data]
        return [single_clean(data, initial)] if data else []


class DocumentUploadForm(StyledFormMixin, forms.Form):
    organization = forms.ModelChoiceField(label="Организация", queryset=Organization.objects.none())
    files = MultipleFileField(label="Файлы", validators=[validate_business_document])
    document_type = forms.ModelChoiceField(label="Тип документа", queryset=DocumentType.objects.none(), required=False)
    counterparty = forms.ModelChoiceField(label="Вторая сторона", queryset=Counterparty.objects.none(), required=False)
    contract = forms.ModelChoiceField(label="Договор", queryset=Contract.objects.none(), required=False)
    operation = forms.ModelChoiceField(label="Пакет / операция", queryset=DocumentOperation.objects.none(), required=False)
    location = forms.ModelChoiceField(label="Объект", queryset=Location.objects.none(), required=False)
    equipment = forms.ModelMultipleChoiceField(
        label="Оборудование", queryset=Equipment.objects.none(), required=False, widget=forms.SelectMultiple(attrs={"size": 7})
    )
    document_date = forms.DateField(label="Дата документа", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    number = forms.CharField(label="Номер", max_length=120, required=False)
    amount = forms.DecimalField(label="Сумма", max_digits=15, decimal_places=2, required=False, localize=True)
    title = forms.CharField(label="Название", max_length=255, required=False, help_text="Можно оставить пустым — будет использован тип документа.")
    notes = forms.CharField(label="Комментарий", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, initial_contract=None, initial_operation=None, initial_organization=None, initial_counterparty=None, initial_location=None, initial_equipment=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()
        self.fields["amount"].localize = True
        self.fields["organization"].queryset = Organization.objects.filter(archived=False, kind=Organization.Kind.COMPANY)
        self.fields["document_type"].queryset = DocumentType.objects.filter(archived=False)
        self.fields["counterparty"].queryset = Counterparty.objects.filter(archived=False)
        self.fields["contract"].queryset = Contract.objects.filter(archived=False).select_related("organization", "counterparty")
        self.fields["operation"].queryset = DocumentOperation.objects.select_related("organization", "counterparty", "contract")
        self.fields["location"].queryset = Location.objects.filter(archived=False).select_related("organization")
        self.fields["equipment"].queryset = Equipment.objects.filter(archived=False).select_related("owner", "category")
        self.fields["document_type"].empty_label = "Неразобранное"
        self.fields["counterparty"].empty_label = "Не указан"
        self.fields["contract"].empty_label = "Не привязан"
        self.fields["operation"].empty_label = "Вне операции"
        self.fields["location"].empty_label = "Не связан"
        self.fields["document_date"].initial = None
        if not self.is_bound:
            if initial_operation is not None:
                self.initial.update({"operation": initial_operation.pk, "contract": initial_operation.contract_id, "organization": initial_operation.organization_id, "counterparty": initial_operation.counterparty_id, "location": initial_operation.location_id})
            elif initial_contract is not None:
                self.initial.update({
                    "contract": initial_contract.pk,
                    "organization": initial_contract.organization_id,
                    "counterparty": initial_contract.counterparty_id,
                    "location": initial_contract.location_id,
                })
            elif initial_organization is not None:
                self.initial["organization"] = initial_organization.pk
            if initial_counterparty is not None:
                self.initial["counterparty"] = initial_counterparty.pk
            if initial_location is not None:
                self.initial["location"] = initial_location.pk
                self.initial["organization"] = initial_location.organization_id
            if initial_equipment is not None:
                self.initial["equipment"] = [initial_equipment.pk]
                self.initial["organization"] = initial_equipment.owner_id
                if initial_equipment.location_id:
                    self.initial["location"] = initial_equipment.location_id

    def clean(self):
        data = super().clean()
        organization = data.get("organization")
        contract = data.get("contract")
        operation = data.get("operation")
        counterparty = data.get("counterparty")
        location = data.get("location")
        equipment = data.get("equipment")
        if operation:
            if organization and operation.organization_id != organization.id:
                self.add_error("operation", "Операция относится к другой организации.")
            if contract and operation.contract_id != contract.id:
                self.add_error("operation", "Операция относится к другому договору.")
            if not contract and operation.contract_id:
                data["contract"] = operation.contract
            if not counterparty and operation.counterparty_id:
                data["counterparty"] = operation.counterparty
        if contract:
            if organization and contract.organization_id != organization.id:
                self.add_error("contract", "Договор относится к другой организации.")
            if counterparty and contract.counterparty_id != counterparty.id:
                self.add_error("counterparty", "Контрагент отличается от контрагента договора.")
        if location and organization and location.organization_id != organization.id:
            self.add_error("location", "Объект относится к другой организации.")
        if equipment and organization:
            wrong = equipment.exclude(owner=organization)
            if wrong.exists():
                self.add_error("equipment", "В списке есть оборудование другого владельца.")
        return data


class InboxUploadForm(StyledFormMixin, forms.Form):
    organization = forms.ModelChoiceField(label="Организация", queryset=Organization.objects.none())
    files = MultipleFileField(label="Файлы", validators=[validate_business_document])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()
        self.fields["organization"].queryset = Organization.objects.filter(archived=False, kind=Organization.Kind.COMPANY)


class DocumentEditForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = DocumentRecord
        fields = [
            "organization", "document_type", "counterparty", "contract", "operation", "location", "equipment",
            "title", "number", "document_date", "amount", "file", "notes",
        ]
        widgets = {
            "document_date": forms.DateInput(attrs={"type": "date"}),
            "equipment": forms.SelectMultiple(attrs={"size": 7}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()
        self.fields["amount"].localize = True
        self.fields["organization"].queryset = Organization.objects.filter(archived=False, kind=Organization.Kind.COMPANY)
        self.fields["document_type"].queryset = DocumentType.objects.filter(archived=False)
        self.fields["counterparty"].queryset = Counterparty.objects.filter(archived=False)
        self.fields["contract"].queryset = Contract.objects.filter(archived=False).select_related("organization", "counterparty")
        self.fields["operation"].queryset = DocumentOperation.objects.select_related("organization", "counterparty", "contract")
        self.fields["location"].queryset = Location.objects.filter(archived=False).select_related("organization")
        self.fields["equipment"].queryset = Equipment.objects.filter(archived=False).select_related("owner", "category")
        self.fields["document_type"].empty_label = "Неразобранное"
        self.fields["counterparty"].empty_label = "Не указан"
        self.fields["contract"].empty_label = "Не привязан"
        self.fields["operation"].empty_label = "Вне операции"
        self.fields["location"].empty_label = "Не связан"

    def clean(self):
        data = super().clean()
        organization = data.get("organization")
        counterparty = data.get("counterparty")
        contract = data.get("contract")
        location = data.get("location")
        equipment = data.get("equipment")
        if contract and organization and contract.organization_id != organization.id:
            self.add_error("contract", "Договор относится к другой организации.")
        if contract and counterparty and contract.counterparty_id != counterparty.id:
            self.add_error("counterparty", "Контрагент не совпадает с контрагентом договора.")
        if location and organization and location.organization_id != organization.id:
            self.add_error("location", "Объект относится к другой организации.")
        if equipment and organization and equipment.exclude(owner=organization).exists():
            self.add_error("equipment", "В списке есть оборудование другого владельца.")
        return data


class DocumentTypeForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = DocumentType
        fields = ["name", "code", "sort_order", "archived"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()


class ReminderForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Reminder
        fields = [
            "title", "organization", "counterparty", "contract", "location", "next_due_date",
            "remind_days_before", "recurrence", "interval_days", "amount", "notes", "active",
        ]
        widgets = {
            "next_due_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()
        self.fields["amount"].localize = True
        self.fields["organization"].queryset = Organization.objects.filter(archived=False, kind=Organization.Kind.COMPANY)
        self.fields["counterparty"].queryset = Counterparty.objects.filter(archived=False)
        self.fields["contract"].queryset = Contract.objects.filter(archived=False).select_related("organization", "counterparty")
        self.fields["location"].queryset = Location.objects.filter(archived=False).select_related("organization")
        self.fields["organization"].empty_label = "Без привязки"
        self.fields["counterparty"].empty_label = "Не указан"
        self.fields["contract"].empty_label = "Не привязан"
        self.fields["location"].empty_label = "Не связан"
        self.fields["remind_days_before"].help_text = "Например, 3 — показать напоминание за три дня до даты."
        self.fields["interval_days"].help_text = "Используется только для повтора «через заданное число дней»."
        if not self.instance.pk and not self.is_bound:
            self.fields["next_due_date"].initial = timezone.localdate()

    def clean(self):
        data = super().clean()
        organization = data.get("organization")
        counterparty = data.get("counterparty")
        contract = data.get("contract")
        location = data.get("location")
        recurrence = data.get("recurrence")
        interval_days = data.get("interval_days")
        if contract:
            if organization and contract.organization_id != organization.id:
                self.add_error("contract", "Договор относится к другой организации.")
            if counterparty and contract.counterparty_id != counterparty.id:
                self.add_error("counterparty", "Контрагент не совпадает с контрагентом договора.")
            if not organization:
                data["organization"] = contract.organization
            if not data.get("counterparty"):
                data["counterparty"] = contract.counterparty
            if not location and contract.location_id:
                data["location"] = contract.location
        if location and data.get("organization") and location.organization_id != data["organization"].id:
            self.add_error("location", "Объект относится к другой организации.")
        if recurrence == Reminder.Recurrence.INTERVAL and not interval_days:
            self.add_error("interval_days", "Укажите интервал в днях.")
        if recurrence != Reminder.Recurrence.INTERVAL:
            data["interval_days"] = None
        return data
