"""Repair numbers saved before the phone rule handled '+263 07...' and '77...' (no 0)."""
from django.db import migrations


def forwards(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for u in User.objects.all():
        digits = u.phone.lstrip("+")
        fixed = None
        if digits.startswith("2630") and len(digits) == 13:
            fixed = "+263" + digits[4:]
        elif len(digits) == 9 and digits[0] == "7":
            fixed = "+263" + digits
        if fixed and not User.objects.filter(phone=fixed).exclude(pk=u.pk).exists():
            u.phone = fixed
            u.save(update_fields=["phone"])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_password_reset")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
