import datetime
from decimal import Decimal

from django.db import migrations


def forwards(apps, schema_editor):
    V = apps.get_model("erp_trade", "VatRate")
    V.objects.get_or_create(effective_from=datetime.date(2023, 1, 1), defaults={
        "standard_pct": Decimal("15"), "source": "VAT Act standard rate before 2026"})
    V.objects.get_or_create(effective_from=datetime.date(2026, 1, 1), defaults={
        "standard_pct": Decimal("15.5"), "source": "Finance Act No. 7 of 2025: 15.5% from 1 January 2026"})


class Migration(migrations.Migration):
    dependencies = [("erp_trade", "0001_initial")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
