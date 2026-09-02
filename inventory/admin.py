from django.contrib import admin
from .models import (
    Act, ActItem, Cabinet, CatalogItem, CatalogPriceHistory, Category, Contract, Counterparty,
    DocumentRecord, DocumentType, Employee, Equipment, EquipmentLoan, EquipmentMovement, Location,
    LoginAttempt, MaterialStock, MaterialTransaction, Organization, Project, ProjectOperation,
    ProjectOperationLine, ProjectStage, Reminder, RepairRecord, Room, Warehouse,
)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "prefix", "kind", "archived")
    search_fields = ("name", "short_name", "prefix")


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("full_name", "organization", "department", "position", "workplace", "archived")
    list_filter = ("organization", "archived")
    search_fields = ("full_name", "phone", "position", "department", "workplace")


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("internal_code", "name", "catalog_item", "category", "mac_address", "owner", "responsible_employee", "usage_status", "condition")
    list_filter = ("category", "owner", "usage_status", "condition", "archived")
    search_fields = ("internal_code", "name", "manufacturer", "model", "serial_number", "mac_address", "hostname", "network_address")


@admin.register(MaterialStock)
class MaterialStockAdmin(admin.ModelAdmin):
    list_display = ("catalog_item", "warehouse", "quantity", "updated_at")
    list_filter = ("warehouse__organization", "warehouse")
    search_fields = ("catalog_item__name", "catalog_item__manufacturer", "catalog_item__model", "catalog_item__sku")
    readonly_fields = ("warehouse", "catalog_item", "quantity", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ImmutableLedgerAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MaterialTransaction)
class MaterialTransactionAdmin(ImmutableLedgerAdmin):
    list_display = ("created_at", "catalog_item", "warehouse", "transaction_type", "quantity", "balance_after", "created_by")
    list_filter = ("transaction_type", "warehouse__organization", "warehouse")
    search_fields = ("catalog_item__name", "source", "note")


@admin.register(ProjectOperation)
class ProjectOperationAdmin(ImmutableLedgerAdmin):
    list_display = ("operation_date", "stage", "created_by", "voided_at")
    list_filter = ("stage__project__organization", "stage__project")


@admin.register(ProjectOperationLine)
class ProjectOperationLineAdmin(ImmutableLedgerAdmin):
    list_display = ("operation", "line_type", "item_name_snapshot", "quantity", "line_total_snapshot")
    list_filter = ("line_type",)


admin.site.register([
    Location, Room, Cabinet, Category, CatalogItem, CatalogPriceHistory, EquipmentLoan, EquipmentMovement,
    Act, ActItem, RepairRecord, Counterparty, Contract, DocumentType, DocumentRecord, Reminder,
    Warehouse, Project, ProjectStage,
])
admin.site.site_header = "FOX Inventory — администрирование"
admin.site.site_title = "FOX Inventory"


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    list_display = ("created_at", "username", "user", "ip_address", "result", "short_user_agent")
    list_filter = ("result", "created_at")
    search_fields = ("username", "username_normalized", "ip_address", "user_agent")
    readonly_fields = ("created_at", "username", "username_normalized", "user", "ip_address", "user_agent", "result", "reason")
    ordering = ("-created_at",)

    @admin.display(description="Браузер / клиент")
    def short_user_agent(self, obj):
        return obj.user_agent[:100]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
