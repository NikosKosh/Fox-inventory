from pathlib import Path
from django import forms
from django.conf import settings
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from .models import Act, Cabinet, Category, Employee, Equipment, EquipmentLoan, Location, Organization, RepairRecord, Room
from .validators import normalize_mac_address


class AccountPasswordChangeForm(PasswordChangeForm):
    error_css_class = "field-error"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        labels = {
            "old_password": "Текущий пароль",
            "new_password1": "Новый пароль",
            "new_password2": "Повторите новый пароль",
        }
        for name, field in self.fields.items():
            field.label = labels.get(name, field.label)
            field.widget.attrs.update({
                "class": "input",
                "autocomplete": "current-password" if name == "old_password" else "new-password",
            })



class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "checkbox")
            else:
                field.widget.attrs.setdefault("class", "input")


class OrganizationForm(StyledModelForm):
    class Meta:
        model = Organization
        fields = ["name", "short_name", "prefix", "kind", "archived", "notes"]

    def clean_prefix(self):
        return self.cleaned_data["prefix"].strip().upper()


class EmployeeForm(StyledModelForm):
    class Meta:
        model = Employee
        fields = [
            "full_name", "position", "department", "workplace_location", "room",
            "phone", "organization", "archived", "notes",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["workplace_location"].queryset = (
            Location.objects.filter(archived=False).select_related("organization")
        )
        self.fields["workplace_location"].empty_label = "Не выбрано"
        self.fields["workplace_location"].help_text = "Объекты создаются в разделе «Объекты»."
        self.fields["room"].queryset = Room.objects.filter(archived=False).select_related("location", "location__organization")
        self.fields["room"].empty_label = "Без конкретного помещения"
        self.fields["room"].help_text = "Например: переговорная, кабинет, серверная."

    def clean(self):
        data = super().clean()
        location = data.get("workplace_location")
        room = data.get("room")
        if room and location and room.location_id != location.id:
            self.add_error("room", "Помещение относится к другому объекту.")
        if room and not location:
            data["workplace_location"] = room.location
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        selected = self.cleaned_data.get("workplace_location")
        room = self.cleaned_data.get("room")
        if room and not selected:
            selected = room.location
            obj.workplace_location = selected
        obj.workplace = selected.address if selected else ""
        if commit:
            obj.save()
            self.save_m2m()
        return obj


class LocationForm(StyledModelForm):
    class Meta:
        model = Location
        fields = ["organization", "address", "label", "archived"]


class RoomForm(StyledModelForm):
    class Meta:
        model = Room
        fields = ["location", "name", "room_type", "floor", "notes", "archived"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class CabinetForm(StyledModelForm):
    class Meta:
        model = Cabinet
        fields = ["location", "room", "name", "notes", "archived"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["room"].queryset = Room.objects.filter(archived=False).select_related("location")
        self.fields["room"].empty_label = "Без помещения"

    def clean(self):
        data = super().clean()
        location = data.get("location")
        room = data.get("room")
        if room and location and room.location_id != location.id:
            self.add_error("room", "Помещение относится к другому объекту.")
        if room and not location:
            data["location"] = room.location
        return data


class CategoryForm(StyledModelForm):
    class Meta:
        model = Category
        fields = ["name", "code", "tracking_mode", "archived"]

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()


class EquipmentForm(StyledModelForm):
    network_password = forms.CharField(label="Пароль", required=False, widget=forms.PasswordInput(render_value=False), help_text="Оставьте пустым, чтобы не менять пароль.")

    class Meta:
        model = Equipment
        fields = [
            "category", "accounting_group", "internal_code", "name", "manufacturer", "model", "serial_number", "mac_address", "hostname",
            "owner", "responsible_employee", "location", "room", "cabinet", "freeform_location", "quantity",
            "usage_status", "condition", "notes", "network_address", "network_username", "archived",
        ]
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}


    def clean_mac_address(self):
        return normalize_mac_address(self.cleaned_data.get("mac_address", ""))

    def clean(self):
        data = super().clean()
        cabinet = data.get("cabinet")
        room = data.get("room")
        location = data.get("location")
        if room and location and room.location_id != location.id:
            self.add_error("room", "Помещение относится к другому объекту.")
        if room and not location:
            data["location"] = room.location
            location = room.location
        if cabinet and location and cabinet.location_id != location.id:
            self.add_error("cabinet", "Шкаф относится к другому адресу.")
        if cabinet and room and cabinet.room_id and cabinet.room_id != room.id:
            self.add_error("cabinet", "Шкаф находится в другом помещении.")
        if cabinet and not location:
            data["location"] = cabinet.location
        if cabinet and cabinet.room_id and not room:
            data["room"] = cabinet.room
        category = data.get("category")
        if category and category.tracking_mode == Category.TrackingMode.UNIT:
            data["quantity"] = 1
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        password = self.cleaned_data.get("network_password")
        if password:
            obj.set_network_password(password)
        if commit:
            obj.save()
            self.save_m2m()
        return obj


class AssignmentForm(forms.Form):
    employee = forms.ModelChoiceField(label="Сотрудник", queryset=Employee.objects.none(), required=False, widget=forms.Select(attrs={"class": "input"}))
    status = forms.ChoiceField(label="Новый статус", choices=Equipment.UsageStatus.choices, widget=forms.Select(attrs={"class": "input"}))
    location = forms.ModelChoiceField(label="Адрес", queryset=Location.objects.none(), required=False, widget=forms.Select(attrs={"class": "input"}))
    room = forms.ModelChoiceField(label="Помещение", queryset=Room.objects.none(), required=False, widget=forms.Select(attrs={"class": "input"}))
    cabinet = forms.ModelChoiceField(label="Шкаф", queryset=Cabinet.objects.none(), required=False, widget=forms.Select(attrs={"class": "input"}))
    freeform_location = forms.CharField(label="Место текстом", required=False, widget=forms.TextInput(attrs={"class": "input"}))
    notes = forms.CharField(label="Комментарий", required=False, widget=forms.Textarea(attrs={"class": "input", "rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = Employee.objects.filter(archived=False).select_related("organization")
        self.fields["location"].queryset = Location.objects.filter(archived=False).select_related("organization")
        self.fields["room"].queryset = Room.objects.filter(archived=False).select_related("location", "location__organization")
        self.fields["cabinet"].queryset = Cabinet.objects.filter(archived=False).select_related("location")


class RoomEquipmentAssignForm(forms.Form):
    equipment = forms.ModelMultipleChoiceField(
        label="Оборудование",
        queryset=Equipment.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )
    notes = forms.CharField(
        label="Комментарий", required=False,
        initial="Размещено в помещении объекта.",
        widget=forms.Textarea(attrs={"class": "input", "rows": 2}),
    )

    def __init__(self, *args, room=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.room = room
        if room is not None:
            self.fields["equipment"].queryset = Equipment.objects.filter(
                archived=False, responsible_employee__isnull=True,
                usage_status__in=[Equipment.UsageStatus.STOCK, Equipment.UsageStatus.RESERVE, Equipment.UsageStatus.OBJECT],
            ).filter(
                Q(location__isnull=True) | Q(location=room.location, room__isnull=True)
            ).select_related("category", "owner", "location").order_by("category__name", "internal_code", "name")


class LoanForm(StyledModelForm):
    class Meta:
        model = EquipmentLoan
        fields = ["borrower", "responsible_employee", "started_at", "expected_return_at", "undocumented", "document", "notes"]
        widgets = {"started_at": forms.DateInput(attrs={"type": "date"}), "expected_return_at": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, equipment=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.equipment = equipment
        if equipment:
            self.fields["borrower"].queryset = Organization.objects.exclude(pk=equipment.owner_id).filter(archived=False)


class ActForm(StyledModelForm):
    apply_to_current_state = forms.BooleanField(
        label="Применить выдачу/возврат к текущему состоянию оборудования",
        required=False,
        initial=True,
        help_text="Для архивного акта параметр можно отключить: документ сохранится без изменения текущего ответственного.",
    )

    class Meta:
        model = Act
        fields = ["number", "act_type", "act_date", "employee", "from_organization", "to_organization", "equipment", "document", "notes", "public_enabled"]
        widgets = {
            "act_date": forms.DateInput(attrs={"type": "date"}),
            "equipment": forms.SelectMultiple(attrs={"size": 12}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = Employee.objects.select_related("organization").all()
        self.fields["equipment"].queryset = Equipment.objects.filter(archived=False).select_related("category", "owner")
        if self.instance and self.instance.pk and not self.is_bound:
            self.fields["apply_to_current_state"].initial = False


class RepairForm(StyledModelForm):
    class Meta:
        model = RepairRecord
        fields = ["opened_at", "closed_at", "problem", "result", "status"]
        widgets = {"opened_at": forms.DateInput(attrs={"type": "date"}), "closed_at": forms.DateInput(attrs={"type": "date"})}


class ImportForm(forms.Form):
    file = forms.FileField(label="Файл XLSX или CSV", widget=forms.FileInput(attrs={"class": "input", "accept": ".xlsx,.csv"}))

    def clean_file(self):
        f = self.cleaned_data["file"]
        if Path(f.name).suffix.lower() not in {".xlsx", ".csv"}:
            raise ValidationError("Разрешены только XLSX и CSV.")
        return f


class EmployeeEquipmentOperationForm(forms.Form):
    equipment = forms.ModelMultipleChoiceField(
        label="Оборудование",
        queryset=Equipment.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )
    notes = forms.CharField(
        label="Комментарий",
        required=False,
        widget=forms.Textarea(attrs={"class": "input", "rows": 3}),
    )

    def __init__(self, *args, employee=None, operation="issue", **kwargs):
        super().__init__(*args, **kwargs)
        self.employee = employee
        self.operation = operation
        if operation == "return":
            queryset = Equipment.objects.filter(
                archived=False,
                responsible_employee=employee,
            ).select_related("category", "owner").order_by("category__name", "internal_code", "name")
        else:
            queryset = Equipment.objects.filter(
                archived=False,
                responsible_employee__isnull=True,
                usage_status__in=[Equipment.UsageStatus.STOCK, Equipment.UsageStatus.RESERVE],
            ).select_related("category", "owner").order_by("category__name", "internal_code", "name")
        self.fields["equipment"].queryset = queryset


class EmployeeActDocumentForm(forms.Form):
    act_date = forms.DateField(
        label="Дата акта",
        widget=forms.DateInput(attrs={"class": "input", "type": "date"}),
    )
    city = forms.CharField(
        label="Город",
        initial="",
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    organization_name = forms.CharField(
        label="Организация в акте",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    representative_position = forms.CharField(
        label="Должность представителя организации",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    representative_name = forms.CharField(
        label="ФИО представителя организации",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    equipment = forms.ModelMultipleChoiceField(
        label="Оборудование для акта",
        queryset=Equipment.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, employee=None, act_type="issue", **kwargs):
        super().__init__(*args, **kwargs)
        self.employee = employee
        self.act_type = act_type
        queryset = Equipment.objects.filter(
            archived=False,
            responsible_employee=employee,
        ).select_related("category", "owner").order_by("category__name", "internal_code", "name")
        self.fields["equipment"].queryset = queryset
        if not self.is_bound:
            self.initial["equipment"] = list(queryset.values_list("pk", flat=True))
        if not self.is_bound and employee is not None:
            self.fields["act_date"].initial = timezone.localdate()
            self.fields["organization_name"].initial = employee.organization.name
            self.fields["city"].initial = settings.ACT_DEFAULT_CITY
            if act_type == "return":
                self.fields["representative_position"].initial = settings.ACT_RETURN_REPRESENTATIVE_POSITION
                self.fields["representative_name"].initial = settings.ACT_RETURN_REPRESENTATIVE_NAME
            else:
                self.fields["representative_position"].initial = settings.ACT_ISSUE_REPRESENTATIVE_POSITION
                self.fields["representative_name"].initial = settings.ACT_ISSUE_REPRESENTATIVE_NAME


class EmployeeEquipmentActWorkflowForm(forms.Form):
    """Единая форма: выбор оборудования, создание акта и изменение состояния."""

    act_date = forms.DateField(
        label="Дата акта",
        widget=forms.DateInput(attrs={"class": "input", "type": "date"}),
    )
    city = forms.CharField(
        label="Город",
        initial="",
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    organization_name = forms.CharField(
        label="Организация в акте",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    representative_position = forms.CharField(
        label="Должность представителя организации",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    representative_name = forms.CharField(
        label="ФИО представителя организации",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    equipment = forms.ModelMultipleChoiceField(
        label="Оборудование",
        queryset=Equipment.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )
    notes = forms.CharField(
        label="Комментарий к операции",
        required=False,
        widget=forms.Textarea(attrs={"class": "input", "rows": 2}),
    )

    def __init__(self, *args, employee=None, operation="issue", **kwargs):
        super().__init__(*args, **kwargs)
        self.employee = employee
        self.operation = operation
        if operation == "return":
            queryset = Equipment.objects.filter(
                archived=False,
                responsible_employee=employee,
            ).select_related("category", "owner").order_by("category__name", "internal_code", "name")
        else:
            queryset = Equipment.objects.filter(
                archived=False,
                responsible_employee__isnull=True,
                usage_status__in=[Equipment.UsageStatus.STOCK, Equipment.UsageStatus.RESERVE],
            ).select_related("category", "owner").order_by("category__name", "internal_code", "name")
        self.fields["equipment"].queryset = queryset

        if not self.is_bound and employee is not None:
            self.fields["act_date"].initial = timezone.localdate()
            self.fields["organization_name"].initial = employee.organization.name
            self.fields["city"].initial = settings.ACT_DEFAULT_CITY
            if operation == "return":
                self.fields["representative_position"].initial = settings.ACT_RETURN_REPRESENTATIVE_POSITION
                self.fields["representative_name"].initial = settings.ACT_RETURN_REPRESENTATIVE_NAME
                self.initial["equipment"] = list(queryset.values_list("pk", flat=True))
            else:
                self.fields["representative_position"].initial = settings.ACT_ISSUE_REPRESENTATIVE_POSITION
                self.fields["representative_name"].initial = settings.ACT_ISSUE_REPRESENTATIVE_NAME

    def clean_equipment(self):
        equipment = self.cleaned_data["equipment"]
        if not equipment:
            raise ValidationError("Выберите хотя бы одну единицу оборудования.")
        return equipment
