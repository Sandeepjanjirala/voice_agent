from django.contrib import admin

from .models import Employee


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("name", "role", "channel", "language", "draft_source", "updated_at")
    list_filter = ("channel", "language")
    search_fields = ("name", "role")
    readonly_fields = ("draft", "draft_source", "created_by")
