"""python manage.py fetch_rates  ->  save today's ZiG and rand rates from the internet."""
from django.core.management.base import BaseCommand

from erp_finance.fetch import FetchFailed, fetch_latest


class Command(BaseCommand):
    help = "Fetch today's USD to ZiG and rand rates online (start-windows.bat runs this)."

    def handle(self, *args, **options):
        try:
            saved = fetch_latest()
        except FetchFailed as e:
            self.stdout.write(f"Exchange rates not updated: {e}")
            return
        for r in saved:
            self.stdout.write(f"Exchange rate {r.date}: 1 USD = {r.units_per_usd.normalize():f} {r.currency}")
