from django.contrib import admin
from .models import Act, ActItem, Cabinet, CatalogItem, CatalogPriceHistory, Category, Contract, Counterparty, DocumentRecord, DocumentType, Employee, Equipment, EquipmentLoan, EquipmentMovement, Location, LoginAttempt, Organization, Reminder, RepairRecord, Room

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

admin.site.register([Location, Room, Cabinet, Category, CatalogItem, CatalogPriceHistory, EquipmentLoan, EquipmentMovement, Act, ActItem, RepairRecord, Counterparty, Contract, DocumentType, DocumentRecord, Reminder])
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
