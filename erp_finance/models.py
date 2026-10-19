"""
Exchange rates between US dollars and the other currencies farms use.

Every rate says: on this DATE, 1 US dollar = RATE units of CURRENCY
(for example 1 USD = 26.85 ZWG). Rates entered by Murimi staff have no
organisation and apply to everyone; a farm business can add its own (for
example the rate its bank actually gave it), and its own rate wins.

Transactions in later stages store the rate they used, so old records never
change when new rates are added.
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from erp_core.models import Organisation


class ExchangeRate(models.Model):
    OFFICIAL, BANK, CASH, OTHER, ONLINE = "official", "bank", "cash", "other", "online"
    SOURCE_CHOICES = [
        (OFFICIAL, "Official interbank rate (RBZ)"),
        (ONLINE, "Fetched online automatically"),
        (BANK, "Our bank's rate"),
        (CASH, "Cash / market rate"),
        (OTHER, "Other"),
    ]
    QUOTE_CURRENCIES = [("ZWG", "ZiG"), ("ZAR", "South African rand")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(Organisation, null=True, blank=True, on_delete=models.CASCADE,
                                     related_name="exchange_rates",
                                     help_text="Empty = a Murimi-wide rate that every organisation can use.")
    date = models.DateField()
    currency = models.CharField(max_length=3, choices=QUOTE_CURRENCIES)
    units_per_usd = models.DecimalField("1 USD =", max_digits=18, decimal_places=6)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=OFFICIAL)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "currency"]
        constraints = [
            models.UniqueConstraint(fields=["organisation", "date", "currency"], name="erp_one_org_rate_per_day"),
            models.UniqueConstraint(fields=["date", "currency"], condition=Q(organisation__isnull=True),
                                    name="erp_one_global_rate_per_day"),
            models.CheckConstraint(condition=Q(units_per_usd__gt=0), name="erp_rate_positive"),
        ]

    def __str__(self):
        who = self.organisation.name if self.organisation_id else "Murimi"
        return f"{self.date}: 1 USD = {self.units_per_usd:f} {self.currency} ({who})"
