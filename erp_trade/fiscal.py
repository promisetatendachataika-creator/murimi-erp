"""
The fiscal-device slot.

ZIMRA wants every sale recorded on a fiscal device and sent to its Fiscalisation Data
Management System (FDMS), which returns a receipt number and a verification (QR) code
that must be printed on the invoice.

Today Murimi uses the ManualDevice: you issue the invoice here, put the sale through your
own fiscal device, and type its fiscal receipt number and verification code into Murimi.

Later, an FdmsDevice that talks to ZIMRA's FDMS (a "virtual fiscal device") can be added:
it only needs a `submit(document)` that returns the same three fields. Nothing else in the
ERP has to change. Choose it with MURIMI_FISCAL_DEVICE in .env.
"""
from django.conf import settings
from django.utils import timezone


class FiscalError(Exception):
    pass


class ManualDevice:
    name = "manual"
    automatic = False

    def submit(self, document, receipt_no="", code="", device=""):
        receipt_no, code = (receipt_no or "").strip(), (code or "").strip()
        if not receipt_no:
            raise FiscalError("Enter the fiscal receipt number printed by your fiscal device.")
        return {"fiscal_status": "manual", "fiscal_receipt_no": receipt_no[:60], "fiscal_code": code[:200],
                "fiscal_device": (device or "").strip()[:40], "fiscalised_at": timezone.now()}


DEVICES = {"manual": ManualDevice}


def get_device(org):
    return DEVICES.get(getattr(settings, "MURIMI_FISCAL_DEVICE", "manual"), ManualDevice)()
