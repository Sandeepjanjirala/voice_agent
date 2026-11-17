from django.contrib import admin

from .models import AuditEvent, Campus, Membership, Organisation, RetentionPolicy


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(Campus)
class CampusAdmin(admin.ModelAdmin):
    list_display = ("name", "organisation", "city", "is_active")
    list_filter = ("organisation", "is_active")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organisation", "role")
    list_filter = ("organisation", "role")


admin.site.register(RetentionPolicy)


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """Read-only: the log is evidence, not something to edit."""

    list_display = ("created_at", "organisation", "actor_name", "action", "target_type")
    list_filter = ("organisation", "action")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
