from django.contrib import admin


class ReadOnlyAdmin(admin.ModelAdmin):
    """
    For records that must not be changed by hand: audit trail, duty history, stock and dispensing.
    Staff can view and search them. Nobody can add, edit or delete them here.
    Changes go through the app's own services, which keep the record consistent.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
