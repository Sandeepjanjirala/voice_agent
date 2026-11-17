from django.contrib import admin

from .models import Contact, ContactImport, Lead, OptOut


class _NoNumbers(admin.ModelAdmin):
    """The admin shows masked numbers only and cannot edit how one is stored."""

    exclude = ("phone_encrypted", "phone_lookup")
    readonly_fields = ("phone_masked",)

    def has_add_permission(self, request):
        return False


@admin.register(Contact)
class ContactAdmin(_NoNumbers):
    list_display = ("name", "phone_masked", "organisation", "campus", "consent")
    list_filter = ("organisation", "consent")


@admin.register(OptOut)
class OptOutAdmin(_NoNumbers):
    list_display = ("phone_masked", "organisation", "source", "created_at")
    list_filter = ("organisation",)


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("subject", "organisation", "campus", "stage", "created_at")
    list_filter = ("organisation", "stage")
    raw_id_fields = ("contact",)


@admin.register(ContactImport)
class ContactImportAdmin(admin.ModelAdmin):
    list_display = ("created_at", "organisation", "campus", "rows", "created")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
