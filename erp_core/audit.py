"""Write one AuditEvent per change. Keep it simple: who, what, which record, what changed."""
from decimal import Decimal
import datetime
import uuid

from .models import AuditEvent


def _plain(v):
    if isinstance(v, (Decimal, uuid.UUID)):
        return str(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    if hasattr(v, "pk"):
        return str(v.pk)
    return v


def diff(before, after):
    """{'size_ha': ['2.00', '2.50']} for every value that changed."""
    return {k: [_plain(before.get(k)), _plain(after.get(k))]
            for k in after if _plain(before.get(k)) != _plain(after.get(k))}


def record(request, organisation, action, obj, summary, changes=None):
    AuditEvent.objects.create(
        organisation=organisation,
        user=request.user if request.user.is_authenticated else None,
        action=action,
        object_type=type(obj).__name__,
        object_id=str(getattr(obj, "pk", "") or ""),
        summary=summary[:200],
        changes={k: _plain(v) if not isinstance(v, list) else [_plain(x) for x in v] for k, v in (changes or {}).items()},
    )
