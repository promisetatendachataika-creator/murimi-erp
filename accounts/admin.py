from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm as BaseUserChangeForm

from .models import User


class UserCreationForm(BaseUserCreationForm):
    class Meta:
        model = User
        fields = ("phone", "username", "full_name")


class UserChangeForm(BaseUserChangeForm):
    class Meta:
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    ordering = ("full_name",)
    list_display = ("full_name", "phone", "username", "is_staff", "date_joined")
    search_fields = ("full_name", "phone", "username", "email")
    list_filter = ("is_staff", "is_active")
    readonly_fields = ("date_joined", "last_login")
    fieldsets = (
        (None, {"fields": ("phone", "username", "password")}),
        ("Person", {"fields": ("full_name", "email", "whatsapp_consent", "phone_verified_at")}),
        ("Access", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions"),
                    "classes": ("collapse",)}),
        ("Dates", {"fields": ("date_joined", "last_login")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("phone", "username", "full_name", "password1", "password2")}),)
