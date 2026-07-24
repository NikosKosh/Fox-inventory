from django.contrib import admin
from .models import Act, Cabinet, Category, Employee, Equipment, EquipmentLoan, EquipmentMovement, Location, Organization, RepairRecord, Room

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
    list_display = ("internal_code", "name", "category", "mac_address", "owner", "responsible_employee", "usage_status", "condition")
    list_filter = ("category", "owner", "usage_status", "condition", "archived")
    search_fields = ("internal_code", "name", "manufacturer", "model", "serial_number", "mac_address", "hostname", "network_address")

admin.site.register([Location, Room, Cabinet, Category, EquipmentLoan, EquipmentMovement, Act, RepairRecord])
admin.site.site_header = "FOX Inventory — администрирование"
admin.site.site_title = "FOX Inventory"
