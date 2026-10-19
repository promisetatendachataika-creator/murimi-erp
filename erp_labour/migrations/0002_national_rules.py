"""Starting PAYE/NSSA rules and NEC minimum wages (Murimi-wide).

These are the figures published for 2026 as of October 2026. They are only a
starting point: check them with ZIMRA, NSSA and NEC Agriculture, and add a new
row with a new start date whenever they change (old pay runs keep the old rule).
"""
import datetime
from decimal import Decimal

from django.db import migrations

SRC_TAX = "ZIMRA 2026 PAYE tables as published by Zimbabwe payroll firms; NSSA 4.5% + 4.5%, ceiling US$700. Check with ZIMRA."
SRC_NEC = "NEC Agriculture, General agriculture sub-sector, from 1 June 2025 (A1 US$80, C2 US$159). Fill the other grades from your NEC schedule."
SRC_HORT = "SI 57 of 2026, Horticulture sub-sector, from 1 May 2025."


def forwards(apps, schema_editor):
    Rule = apps.get_model("erp_labour", "PayrollRule")
    Min = apps.get_model("erp_labour", "MinimumWage")
    d = datetime.date(2026, 1, 1)
    Rule.objects.get_or_create(organisation=None, currency="USD", effective_from=d, defaults=dict(
        bands=[[100, 0, 0], [300, 20, 20], [1000, 25, 35], [2000, 30, 85], [3000, 35, 185], [None, 40, 335]],
        nssa_ceiling=Decimal("700"), source=SRC_TAX))
    Rule.objects.get_or_create(organisation=None, currency="ZWG", effective_from=d, defaults=dict(
        bands=[[2800, 0, 0], [8400, 20, 560], [28000, 25, 980], [56000, 30, 2380], [84000, 35, 5180],
               [None, 40, 9380]],
        nssa_ceiling=None, source=SRC_TAX + " ZiG ceiling: US$700 at the pay run's rate."))
    for grade, amount in [("A1", 80), ("C2", 159)]:
        Min.objects.get_or_create(organisation=None, sector="General agriculture", grade=grade,
                                  effective_from=datetime.date(2025, 6, 1),
                                  defaults=dict(monthly_usd=Decimal(amount), source=SRC_NEC))
    for grade, amount in [("A1", 99), ("A2", 107), ("A3", 116), ("B1", 125), ("B2", 133), ("B3", 143), ("B4", 154),
                          ("B5", 166), ("C1", 180), ("C2", 196)]:
        Min.objects.get_or_create(organisation=None, sector="Horticulture", grade=grade,
                                  effective_from=datetime.date(2025, 5, 1),
                                  defaults=dict(monthly_usd=Decimal(amount), source=SRC_HORT))


class Migration(migrations.Migration):
    dependencies = [("erp_labour", "0001_initial")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
