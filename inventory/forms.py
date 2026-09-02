from pathlib import Path
from decimal import Decimal
from django import forms
from django.conf import settings
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from .models import (Act, Cabinet, CatalogItem, Category, Employee, Equipment, EquipmentLoan, Location, Organization, RepairRecord, Room, Warehouse, MaterialStock, Project, ProjectStage)
from .catalog import extract_catalog_sku, make_catalog_identity_key
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
        fields = [
            "name", "short_name", "prefix", "kind",
            "act_organization_name", "act_city",
            "act_issue_representative_position", "act_issue_representative_name",
            "act_return_representative_position", "act_return_representative_name",
            "archived", "notes",
        ]
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}

    def clean_prefix(self):
        return self.cleaned_data["prefix"].strip().upper()


class EmployeeForm(StyledModelForm):
    class Meta:
        model = Employee
        fields = [
            "full_name", "position", "department", "workplace_location", "room",
            "phone", "organization", "notes",
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
        fields = ["organization", "address", "label", "responsible_employee", "archived"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        org_id = self.data.get("organization") if self.is_bound else self.initial.get("organization")
        if not org_id and self.instance and self.instance.pk:
            org_id = self.instance.organization_id
        if hasattr(org_id, "pk"):
            org_id = org_id.pk
        qs = Employee.objects.filter(archived=False).select_related("organization")
        if org_id:
            qs = qs.filter(organization_id=org_id)
        self.fields["responsible_employee"].queryset = qs

    def clean(self):
        data = super().clean()
        org = data.get("organization")
        responsible = data.get("responsible_employee")
        if org and responsible and responsible.organization_id != org.pk:
            self.add_error("responsible_employee", "Ответственный должен быть сотрудником этой организации.")
        return data


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


class CatalogItemForm(StyledModelForm):
    class Meta:
        model = CatalogItem
        fields = [
            "category", "accounting_group", "name", "manufacturer", "model", "sku",
            "inventory_kind", "unit_of_measure", "unit_price", "price_needs_review", "notes",
        ]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["price_needs_review"].help_text = (
            "Отметка нужна только для автоматически выбранной цены. "
            "При ручном сохранении цены подтверждение снимается автоматически."
        )

    def clean(self):
        data = super().clean()
        category = data.get("category")
        if not category:
            return data
        if (
            self.instance
            and self.instance.pk
            and self.instance.category_id != category.pk
            and self.instance.equipment.exists()
        ):
            self.add_error(
                "category",
                "Нельзя менять категорию номенклатуры, к которой уже привязаны экземпляры. "
                "Создайте отдельную номенклатуру и перенесите нужные карточки.",
            )
            return data
        if self.instance and self.instance.pk:
            old_kind = self.instance.inventory_kind
            new_kind = data.get("inventory_kind")
            active_units = self.instance.equipment.filter(archived=False).exclude(usage_status=Equipment.UsageStatus.DISPOSED)
            if old_kind == CatalogItem.InventoryKind.EQUIPMENT and new_kind != old_kind and active_units.exists():
                self.add_error(
                    "inventory_kind",
                    "К этой номенклатуре ещё привязаны действующие индивидуальные экземпляры. "
                    "Сначала завершите их жизненный цикл либо создайте отдельную материальную номенклатуру.",
                )
        sku = (data.get("sku") or "").strip() or extract_catalog_sku(data.get("model", ""))
        data["sku"] = sku
        identity = make_catalog_identity_key(
            category.code,
            data.get("manufacturer", ""),
            data.get("model", ""),
            sku,
        )
        duplicate = CatalogItem.objects.filter(identity_key=identity)
        if self.instance and self.instance.pk:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise ValidationError("Такая номенклатура уже существует. Откройте существующую карточку.")
        return data


class ProjectForm(StyledModelForm):
    class Meta:
        model = Project
        fields = [
            "organization", "location", "name", "code", "project_type",
            "responsible_employee", "start_date", "description",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        org_id = self.data.get("organization") if self.is_bound else self.initial.get("organization")
        if not org_id and self.instance and self.instance.pk:
            org_id = self.instance.organization_id
        if hasattr(org_id, "pk"):
            org_id = org_id.pk
        locations = Location.objects.filter(archived=False).select_related("organization")
        employees = Employee.objects.filter(archived=False).select_related("organization")
        if org_id:
            locations = locations.filter(organization_id=org_id)
            employees = employees.filter(organization_id=org_id)
        self.fields["location"].queryset = locations
        self.fields["responsible_employee"].queryset = employees

    def clean(self):
        data = super().clean()
        org = data.get("organization")
        location = data.get("location")
        employee = data.get("responsible_employee")
        if org and location and location.organization_id != org.pk:
            self.add_error("location", "Объект относится к другой организации.")
        if org and employee and employee.organization_id != org.pk:
            self.add_error("responsible_employee", "Ответственный сотрудник относится к другой организации.")
        if self.instance and self.instance.pk and self.instance.stages.filter(operations__isnull=False).exists():
            if org and org.pk != self.instance.organization_id:
                self.add_error("organization", "Нельзя менять организацию проекта после проведения операций.")
            if location and location.pk != self.instance.location_id:
                self.add_error("location", "Нельзя менять объект проекта после проведения операций.")
        return data


class ProjectStageForm(StyledModelForm):
    class Meta:
        model = ProjectStage
        fields = ["number", "name", "start_date", "notes"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_number(self):
        number = self.cleaned_data["number"]
        if self.instance and self.instance.pk and self.instance.operations.exists() and number != self.instance.number:
            raise ValidationError("Нельзя менять номер этапа после проведения операций.")
        return number


class CatalogConvertMaterialForm(forms.Form):
    inventory_kind = forms.ChoiceField(
        label="Новый тип учёта",
        choices=[
            (CatalogItem.InventoryKind.MATERIAL, "Материал"),
            (CatalogItem.InventoryKind.CONSUMABLE, "Расходник"),
            (CatalogItem.InventoryKind.COMPONENT, "Запчасть / компонент"),
        ],
        initial=CatalogItem.InventoryKind.MATERIAL,
    )
    unit_of_measure = forms.ChoiceField(
        label="Единица измерения",
        choices=CatalogItem.UnitOfMeasure.choices,
        initial=CatalogItem.UnitOfMeasure.PCS,
    )
    confirm = forms.BooleanField(
        label="Подтверждаю преобразование",
        help_text="Подходящие складские карточки будут перенесены в количественный остаток и архивированы как исторические.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "checkbox")
            else:
                field.widget.attrs.setdefault("class", "input")


class MaterialReceiptForm(forms.Form):
    organization = forms.ModelChoiceField(label="Организация", queryset=Organization.objects.none())
    warehouse = forms.ModelChoiceField(label="Склад", queryset=Warehouse.objects.none())
    catalog_item = forms.ModelChoiceField(label="Материал / расходник", queryset=CatalogItem.objects.none())
    quantity = forms.DecimalField(label="Количество", min_value=Decimal("0.001"), max_digits=16, decimal_places=3)
    unit_price = forms.DecimalField(label="Цена за единицу, ₽", required=False, min_value=0, max_digits=14, decimal_places=2)
    source = forms.CharField(label="Источник / документ", required=False, max_length=255, help_text="Например: Счёт №15 от 01.09.2026")
    update_catalog_price = forms.BooleanField(label="Сделать эту цену текущей учётной", required=False, initial=True)
    note = forms.CharField(label="Комментарий", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["organization"].queryset = Organization.objects.filter(archived=False)
        org_id = self.data.get("organization") if self.is_bound else self.initial.get("organization")
        if hasattr(org_id, "pk"):
            org_id = org_id.pk
        warehouses = Warehouse.objects.filter(archived=False).select_related("organization")
        if org_id:
            warehouses = warehouses.filter(organization_id=org_id)
        self.fields["warehouse"].queryset = warehouses
        self.fields["catalog_item"].queryset = CatalogItem.objects.filter(
            archived=False,
            inventory_kind__in=[CatalogItem.InventoryKind.MATERIAL, CatalogItem.InventoryKind.CONSUMABLE, CatalogItem.InventoryKind.COMPONENT],
        ).select_related("category")
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "checkbox")
            else:
                field.widget.attrs.setdefault("class", "input")

    def clean(self):
        data = super().clean()
        org = data.get("organization")
        warehouse = data.get("warehouse")
        if org and warehouse and warehouse.organization_id != org.pk:
            self.add_error("warehouse", "Выбранный склад относится к другой организации.")
        catalog = data.get("catalog_item")
        if catalog and data.get("unit_price") is None and catalog.unit_price is None:
            self.add_error("unit_price", "Укажите цену: у этой номенклатуры ещё нет текущей учётной цены.")
        return data


class MaterialAdjustmentForm(forms.Form):
    direction = forms.ChoiceField(label="Операция", choices=[("plus", "Увеличить остаток"), ("minus", "Уменьшить остаток")])
    stock = forms.ModelChoiceField(label="Складская позиция", queryset=MaterialStock.objects.none())
    quantity = forms.DecimalField(label="Количество", min_value=Decimal("0.001"), max_digits=16, decimal_places=3)
    reason = forms.CharField(label="Причина корректировки", max_length=500, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stock"].queryset = MaterialStock.objects.filter(warehouse__archived=False).select_related(
            "warehouse", "warehouse__organization", "catalog_item"
        ).order_by("warehouse__organization__name", "catalog_item__name")
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input")

    def clean(self):
        data = super().clean()
        stock = data.get("stock")
        qty = data.get("quantity")
        if stock and qty and data.get("direction") == "minus" and qty > stock.quantity:
            self.add_error("quantity", f"Нельзя уменьшить на {qty:g}: текущий остаток {stock.quantity:g}.")
        return data


class ProjectStageOperationForm(forms.Form):
    operation_date = forms.DateField(label="Дата операции", initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date", "class": "input"}))
    equipment = forms.ModelMultipleChoiceField(
        label="Оборудование",
        queryset=Equipment.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    room = forms.ModelChoiceField(label="Помещение", queryset=Room.objects.none(), required=False)
    cabinet = forms.ModelChoiceField(label="Шкаф", queryset=Cabinet.objects.none(), required=False)
    freeform_location = forms.CharField(label="Зона / место установки", required=False, max_length=500)
    note = forms.CharField(label="Комментарий к операции", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, stage=None, **kwargs):
        self.stage = stage
        super().__init__(*args, **kwargs)
        project = stage.project
        self.material_stocks = list(
            MaterialStock.objects.filter(
                warehouse__organization=project.organization,
                warehouse__archived=False,
                quantity__gt=0,
                catalog_item__archived=False,
                catalog_item__inventory_kind__in=[CatalogItem.InventoryKind.MATERIAL, CatalogItem.InventoryKind.CONSUMABLE, CatalogItem.InventoryKind.COMPONENT],
            ).select_related("warehouse", "catalog_item", "catalog_item__category").order_by("catalog_item__name", "warehouse__name")
        )
        for stock in self.material_stocks:
            name = f"material_{stock.pk}"
            self.fields[name] = forms.DecimalField(
                label=stock.catalog_item.name,
                required=False,
                min_value=Decimal("0.001"),
                max_digits=16,
                decimal_places=3,
                widget=forms.NumberInput(attrs={"class": "input material-qty", "step": "0.001", "min": "0", "max": str(stock.quantity), "placeholder": "0", "data-price": str(stock.catalog_item.unit_price or ""), "data-stock": str(stock.quantity)}),
            )
        self.fields["equipment"].queryset = Equipment.objects.filter(
            owner=project.organization,
            archived=False,
            responsible_employee__isnull=True,
            usage_status__in=[Equipment.UsageStatus.STOCK, Equipment.UsageStatus.RESERVE],
            catalog_item__inventory_kind=CatalogItem.InventoryKind.EQUIPMENT,
        ).select_related("catalog_item", "category", "owner").order_by("internal_code", "name")
        self.fields["room"].queryset = Room.objects.filter(location=project.location, archived=False)
        self.fields["cabinet"].queryset = Cabinet.objects.filter(location=project.location, archived=False).select_related("room")
        for name in ["room", "cabinet", "freeform_location", "note"]:
            self.fields[name].widget.attrs.setdefault("class", "input")

    def clean(self):
        data = super().clean()
        selected_materials = []
        for stock in self.material_stocks:
            qty = data.get(f"material_{stock.pk}")
            if not qty:
                continue
            if qty > stock.quantity:
                self.add_error(f"material_{stock.pk}", f"Доступно только {stock.quantity:g} {stock.catalog_item.get_unit_of_measure_display()}.")
            if stock.catalog_item.unit_price is None:
                self.add_error(f"material_{stock.pk}", "Сначала задайте учётную цену номенклатуры.")
            selected_materials.append((stock, qty))
        equipment = data.get("equipment")
        if equipment:
            for item in equipment:
                if item.unit_price is None:
                    self.add_error("equipment", f"У {item} не задана учётная цена.")
        room = data.get("room")
        cabinet = data.get("cabinet")
        if cabinet and room and cabinet.room_id and cabinet.room_id != room.pk:
            self.add_error("cabinet", "Шкаф находится в другом помещении.")
        if not selected_materials and not equipment:
            raise ValidationError("Добавьте хотя бы один материал или экземпляр оборудования.")
        self.selected_materials = selected_materials
        return data


class EquipmentForm(StyledModelForm):
    network_password = forms.CharField(label="Пароль", required=False, widget=forms.PasswordInput(render_value=False), help_text="Оставьте пустым, чтобы не менять пароль.")

    class Meta:
        model = Equipment
        fields = [
            "catalog_item", "category", "accounting_group", "internal_code", "name", "manufacturer", "model", "serial_number", "mac_address", "hostname",
            "owner", "responsible_employee", "location", "room", "cabinet", "freeform_location", "quantity",
            "usage_status", "condition", "notes", "network_address", "network_username",
        ]
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        catalog_qs = CatalogItem.objects.filter(archived=False).filter(
            Q(inventory_kind=CatalogItem.InventoryKind.EQUIPMENT)
            | Q(pk=self.instance.catalog_item_id if self.instance and self.instance.pk else None)
        ).select_related("category")
        self.fields["catalog_item"].queryset = catalog_qs
        self.fields["catalog_item"].empty_label = "Выберите номенклатуру или заполните данные вручную"
        self.fields["catalog_item"].help_text = (
            "Для одинаковых устройств выбирайте одну номенклатуру: название, модель и цена будут едиными."
        )

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
        catalog_item = data.get("catalog_item")
        if catalog_item:
            data["category"] = catalog_item.category
            data["accounting_group"] = catalog_item.accounting_group
            data["name"] = catalog_item.name
            data["manufacturer"] = catalog_item.manufacturer
            data["model"] = catalog_item.model
        category = data.get("category")
        if category and category.tracking_mode == Category.TrackingMode.UNIT:
            data["quantity"] = 1
        if (
            data.get("condition") == Equipment.Condition.BROKEN
            and data.get("usage_status") not in {
                Equipment.UsageStatus.REPAIR,
                Equipment.UsageStatus.WAITING_DISPOSAL,
                Equipment.UsageStatus.DISPOSED,
            }
        ):
            self.add_error(
                "usage_status",
                "Для сломанного оборудования выберите ремонт, ожидание списания или списание.",
            )
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
            ).select_related("category", "catalog_item", "owner", "location").order_by("category__name", "internal_code", "name")


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
        self.fields["equipment"].queryset = Equipment.objects.filter(archived=False).select_related("category", "catalog_item", "owner")
        if self.instance and self.instance.pk:
            if not self.is_bound:
                self.fields["apply_to_current_state"].initial = False
            if self.instance.items.exists():
                self.fields["equipment"].disabled = True
                self.fields["equipment"].help_text = (
                    "Состав нового акта зафиксирован историческим снимком и не меняется при редактировании. "
                    "Для другой комплектации создайте новый акт."
                )


class RepairForm(StyledModelForm):
    class Meta:
        model = RepairRecord
        fields = ["opened_at", "closed_at", "problem", "result", "status"]
        widgets = {"opened_at": forms.DateInput(attrs={"type": "date"}), "closed_at": forms.DateInput(attrs={"type": "date"})}


def _act_defaults_for_employee(employee, operation):
    organization = employee.organization
    if operation == "return":
        position = organization.act_return_representative_position or settings.ACT_RETURN_REPRESENTATIVE_POSITION
        name = organization.act_return_representative_name or settings.ACT_RETURN_REPRESENTATIVE_NAME
    else:
        position = organization.act_issue_representative_position or settings.ACT_ISSUE_REPRESENTATIVE_POSITION
        name = organization.act_issue_representative_name or settings.ACT_ISSUE_REPRESENTATIVE_NAME
    return {
        "city": organization.act_city or settings.ACT_DEFAULT_CITY,
        "organization_name": organization.act_organization_name or organization.name,
        "representative_position": position,
        "representative_name": name,
    }


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
            ).select_related("category", "catalog_item", "owner").order_by("category__name", "internal_code", "name")
        else:
            queryset = Equipment.objects.filter(
                archived=False,
                responsible_employee__isnull=True,
                usage_status__in=[Equipment.UsageStatus.STOCK, Equipment.UsageStatus.RESERVE],
            ).select_related("category", "catalog_item", "owner").order_by("category__name", "internal_code", "name")
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
        ).select_related("category", "catalog_item", "owner").order_by("category__name", "internal_code", "name")
        self.fields["equipment"].queryset = queryset
        if not self.is_bound:
            self.initial["equipment"] = list(queryset.values_list("pk", flat=True))
        if not self.is_bound and employee is not None:
            defaults = _act_defaults_for_employee(employee, act_type)
            self.fields["act_date"].initial = timezone.localdate()
            for field_name, value in defaults.items():
                self.fields[field_name].initial = value


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
            ).select_related("category", "catalog_item", "owner").order_by("category__name", "internal_code", "name")
        else:
            queryset = Equipment.objects.filter(
                archived=False,
                responsible_employee__isnull=True,
                usage_status__in=[Equipment.UsageStatus.STOCK, Equipment.UsageStatus.RESERVE],
            ).select_related("category", "catalog_item", "owner").order_by("category__name", "internal_code", "name")
        self.fields["equipment"].queryset = queryset

        if not self.is_bound and employee is not None:
            defaults = _act_defaults_for_employee(employee, operation)
            self.fields["act_date"].initial = timezone.localdate()
            for field_name, value in defaults.items():
                self.fields[field_name].initial = value
            if operation == "return":
                self.initial["equipment"] = list(queryset.values_list("pk", flat=True))

    def clean_equipment(self):
        equipment = self.cleaned_data["equipment"]
        if not equipment:
            raise ValidationError("Выберите хотя бы одну единицу оборудования.")
        return equipment
