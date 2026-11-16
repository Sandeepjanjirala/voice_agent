from django.contrib import admin

from .models import CallRecord


@admin.register(CallRecord)
class CallRecordAdmin(admin.ModelAdmin):
    """Read-only: records are written by the call service and the voice worker."""

    list_display = (
        "created_at",
        "direction",
        "masked_number",
        "status",
        "duration_seconds",
        "sip_status_code",
        "e2e_latency_p50",
        "placed_by",
    )
    list_filter = ("status", "direction")
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
