"""
Tenancy: making sure each request only touches ONE organisation's records.

Every ERP address looks like /api/erp/orgs/<organisation id>/... . Before a
view does anything, `org_view` checks that the logged-in person belongs to
that organisation and has a role that allows the action. If they don't
belong, the answer is 404 "not found" (not 403), so nobody can find out
which organisations exist.

A small "Resource" class then gives each kind of record (farm, field, season,
...) the same four actions without repeating code:

    GET    .../farms/        list
    POST   .../farms/        create
    GET    .../farms/<id>/   one record
    PATCH  .../farms/<id>/   change it
    DELETE .../farms/<id>/   archive it (or delete it if nothing depends on it)
"""
from functools import wraps

from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse

from murimi_common.clean import uuid_value
from murimi_common.http import body, error, login_required_json

from . import audit
from .models import Membership, Organisation
from .permissions import allowed


def get_membership(user, org_id):
    org_uuid = uuid_value(org_id)
    if org_uuid is None:
        return None
    return (Membership.objects.select_related("organisation")
            .filter(user=user, organisation_id=org_uuid, is_active=True, organisation__is_active=True)
            .first())


def org_view(view):
    """Look up the membership and pass it on as `m`. 404 if the person doesn't belong."""
    @login_required_json
    @wraps(view)
    def wrapped(request, org_id, *args, **kwargs):
        m = get_membership(request.user, org_id)
        if m is None:
            return error("We couldn't find that organisation.", status=404)
        return view(request, m, *args, **kwargs)
    return wrapped


class Resource:
    """Override `model`, `edit_permission`, `to_dict`, `clean` and optionally `queryset` / `describe`."""
    model = None
    edit_permission = "edit_farms"
    label = "record"
    archivable = True  # True: DELETE sets is_active=False; False: DELETE removes the row

    def queryset(self, m, request):
        qs = self.model.objects.filter(organisation=m.organisation)
        if self.archivable and request.GET.get("include_archived") != "1":
            qs = qs.filter(is_active=True)
        return qs

    def to_dict(self, obj):
        raise NotImplementedError

    def clean(self, data, m, instance, errors):
        """Return a dict of field values to save. Add messages to `errors`."""
        raise NotImplementedError

    def describe(self, obj):
        return str(obj)

    def after_validate(self, values, m, instance, errors):
        """Checks that need several fields together (e.g. area left in a field)."""

    # ---- views ------------------------------------------------------------
    def collection_view(self):
        @org_view
        def view(request, m):
            if request.method == "GET":
                return JsonResponse({"results": [self.to_dict(o) for o in self.queryset(m, request)]})
            if request.method == "POST":
                return self.save(request, m, None)
            return error("Method not allowed.", status=405)
        view.__name__ = f"{self.label}_collection"
        return view

    def item_view(self):
        @org_view
        def view(request, m, pk):
            obj = self.queryset(m, request).filter(pk=uuid_value(pk)).first() if uuid_value(pk) else None
            if obj is None and self.archivable and uuid_value(pk):
                obj = self.model.objects.filter(organisation=m.organisation, pk=uuid_value(pk)).first()
            if obj is None:
                return error(f"We couldn't find that {self.label}.", status=404)
            if request.method == "GET":
                return JsonResponse(self.to_dict(obj))
            if request.method == "PATCH":
                return self.save(request, m, obj)
            if request.method == "DELETE":
                return self.remove(request, m, obj)
            return error("Method not allowed.", status=405)
        view.__name__ = f"{self.label}_item"
        return view

    def save(self, request, m, instance):
        if not allowed(m, self.edit_permission):
            return error("Your role can't change this. Ask the owner or farm manager.", status=403)
        data = body(request)
        if instance is not None:
            # PATCH may send only the fields that changed; the rest keep their current values.
            data = {**self.to_dict(instance), **data}
        errors = {}
        values = self.clean(data, m, instance, errors)
        if not errors:
            self.after_validate(values, m, instance, errors)
        if errors:
            return error("Please fix the highlighted fields.", errors=errors)
        before = {k: getattr(instance, k) for k in values} if instance else {}
        obj = instance or self.model(organisation=m.organisation)
        new_id = uuid_value(data.get("id")) if instance is None else None
        if new_id and not self.model.objects.filter(pk=new_id).exists():
            obj.pk = new_id  # ids made on a phone while offline are kept
        for k, v in values.items():
            setattr(obj, k, v)
        try:
            with transaction.atomic():
                obj.save()
        except IntegrityError:
            return error(f"That {self.label} already exists. Use a different name.",
                         errors={"name": "This name is already used."})
        changes = audit.diff(before, values) if instance else {k: v for k, v in values.items()}
        if instance and not changes:
            return JsonResponse(self.to_dict(obj))
        audit.record(request, m.organisation, "update" if instance else "create", obj,
                     f"{'Changed' if instance else 'Added'} {self.label} {self.describe(obj)}", changes)
        return JsonResponse(self.to_dict(obj), status=200 if instance else 201)

    def remove(self, request, m, obj):
        if not allowed(m, self.edit_permission):
            return error("Your role can't change this. Ask the owner or farm manager.", status=403)
        if self.archivable:
            obj.is_active = False
            obj.save(update_fields=["is_active"])
            audit.record(request, m.organisation, "archive", obj, f"Archived {self.label} {self.describe(obj)}")
            return JsonResponse({"ok": True, "archived": True})
        try:
            with transaction.atomic():  # if the delete is refused, the audit line is undone too
                audit.record(request, m.organisation, "delete", obj, f"Deleted {self.label} {self.describe(obj)}")
                obj.delete()
        except ProtectedError:
            return error(f"This {self.label} is used by other records, so it can't be deleted.", status=409)
        return JsonResponse({"ok": True, "deleted": True})
