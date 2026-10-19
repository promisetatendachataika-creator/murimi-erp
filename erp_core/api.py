"""
JSON API for organisations, members, farms, fields, seasons and crop plans.
Addresses are in erp_core/urls.py; tenancy checks are in tenancy.py.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from accounts.backends import find_user
from murimi_common import clean
from murimi_common.choices import CROPS, CURRENCY_CODES, PROVINCES
from murimi_common.http import body, error, login_required_json

from . import audit
from .models import AuditEvent, CropPlan, Farm, Field, Membership, Organisation, Season
from .permissions import actions_for, allowed
from .tenancy import Resource, org_view


def dec(v, places=2):
    """Decimal -> text with a fixed number of decimals ('22' becomes '22.00')."""
    if v is None:
        return None
    return f"{Decimal(v).quantize(Decimal(1).scaleb(-places)):f}"


# ---------------------------------------------------------------- organisations
def org_dict(org, m=None):
    data = {
        "id": str(org.id), "name": org.name, "trading_name": org.trading_name,
        "province": org.province, "district": org.district, "phone": org.phone, "email": org.email,
        "tin": org.tin, "vat_number": org.vat_number, "vat_registered": bool(org.vat_number),
        "reporting_currency": org.reporting_currency,
    }
    if m is not None:
        data["my_role"] = m.role
        data["my_role_label"] = m.get_role_display()
        data["can"] = actions_for(m.role)
    return data


def clean_org(data, errors):
    return {
        "name": clean.text(data, "name", errors, required=True, max_len=120, label="the business name"),
        "trading_name": clean.text(data, "trading_name", errors, max_len=120),
        "province": clean.choice(data, "province", errors, PROVINCES, required=False),
        "district": clean.text(data, "district", errors, max_len=80),
        "phone": clean.text(data, "phone", errors, max_len=20),
        "email": clean.text(data, "email", errors, max_len=254),
        "tin": clean.text(data, "tin", errors, max_len=30),
        "vat_number": clean.text(data, "vat_number", errors, max_len=30),
        "reporting_currency": clean.choice(data, "reporting_currency", errors, CURRENCY_CODES, default="USD"),
    }


@require_http_methods(["GET", "POST"])
@login_required_json
def organisations(request):
    if request.method == "GET":
        ms = (Membership.objects.filter(user=request.user, is_active=True, organisation__is_active=True)
              .select_related("organisation"))
        return JsonResponse({"results": [org_dict(m.organisation, m) for m in ms]})
    errors = {}
    values = clean_org(body(request), errors)
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    with transaction.atomic():
        org = Organisation.objects.create(created_by=request.user, **values)
        m = Membership.objects.create(organisation=org, user=request.user, role=Membership.OWNER)
        audit.record(request, org, "create", org, f"Created organisation {org.name}", values)
    return JsonResponse(org_dict(org, m), status=201)


@require_http_methods(["GET", "PATCH"])
@org_view
def organisation(request, m):
    org = m.organisation
    if request.method == "GET":
        return JsonResponse(org_dict(org, m))
    if not allowed(m, "edit_organisation"):
        return error("Only the owner or farm manager can change the business details.", status=403)
    errors = {}
    values = clean_org({**org_dict(org), **body(request)}, errors)
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    before = {k: getattr(org, k) for k in values}
    for k, v in values.items():
        setattr(org, k, v)
    org.save()
    changes = audit.diff(before, values)
    if changes:
        audit.record(request, org, "update", org, "Changed business details", changes)
    return JsonResponse(org_dict(org, m))


@require_http_methods(["GET"])
@org_view
def summary(request, m):
    """Numbers for the home screen."""
    org = m.organisation
    farms = Farm.objects.filter(organisation=org, is_active=True)
    fields = Field.objects.filter(organisation=org, is_active=True)
    active = Season.objects.filter(organisation=org, status=Season.ACTIVE).order_by("-start_date").first()
    current = active or Season.objects.filter(organisation=org).order_by("-start_date").first()
    plans = CropPlan.objects.filter(organisation=org, season=current).exclude(status=CropPlan.ABANDONED) if current else CropPlan.objects.none()
    by_crop = list(plans.values("crop").annotate(ha=Sum("planted_ha"), n=Count("id")).order_by("-ha"))
    return JsonResponse({
        "farms": farms.count(),
        "fields": fields.count(),
        "field_ha": dec(fields.aggregate(t=Sum("size_ha"))["t"] or Decimal("0")),
        "members": Membership.objects.filter(organisation=org, is_active=True).count(),
        "season": {"id": str(current.id), "name": current.name, "status": current.status} if current else None,
        "planted_ha": dec(plans.aggregate(t=Sum("planted_ha"))["t"] or Decimal("0")),
        "by_crop": [{"crop": r["crop"], "ha": dec(r["ha"]), "plans": r["n"]} for r in by_crop],
    })


# ---------------------------------------------------------------- members
def member_dict(mm):
    return {"id": str(mm.id), "user_id": mm.user_id, "full_name": mm.user.full_name, "phone": mm.user.phone,
            "username": mm.user.username or "", "role": mm.role, "role_label": mm.get_role_display(),
            "is_active": mm.is_active}


def other_owners(org, exclude):
    return Membership.objects.filter(organisation=org, role=Membership.OWNER, is_active=True).exclude(pk=exclude.pk).exists()


@require_http_methods(["GET", "POST"])
@org_view
def members(request, m):
    org = m.organisation
    if request.method == "GET":
        qs = Membership.objects.filter(organisation=org, is_active=True).select_related("user").order_by("user__full_name")
        return JsonResponse({"results": [member_dict(x) for x in qs],
                             "roles": [{"value": v, "label": l} for v, l in Membership.ROLE_CHOICES]})
    if not allowed(m, "manage_members"):
        return error("Only the owner can add people.", status=403)
    data = body(request)
    errors = {}
    role = clean.choice(data, "role", errors, [r for r, _ in Membership.ROLE_CHOICES])
    who = str(data.get("login") or "").strip()
    person = find_user(who) if who else None
    if not who:
        errors["login"] = "Enter their WhatsApp number or username."
    elif person is None or not person.is_active:
        errors["login"] = "No Murimi account with that number or username. Ask them to sign up first."
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    mm, created = Membership.objects.get_or_create(organisation=org, user=person, defaults={"role": role})
    if not created:
        if mm.is_active:
            return error("They are already in this organisation.", errors={"login": "Already a member."})
        mm.is_active, mm.role = True, role
        mm.save()
    audit.record(request, org, "create", mm, f"Added {person.full_name} as {mm.get_role_display()}", {"role": role})
    return JsonResponse(member_dict(mm), status=201)


@require_http_methods(["PATCH", "DELETE"])
@org_view
def member(request, m, pk):
    org = m.organisation
    if not allowed(m, "manage_members"):
        return error("Only the owner can change people's roles.", status=403)
    mm = Membership.objects.filter(organisation=org, pk=clean.uuid_value(pk), is_active=True).select_related("user").first()
    if mm is None:
        return error("We couldn't find that person.", status=404)
    if request.method == "PATCH":
        errors = {}
        role = clean.choice(body(request), "role", errors, [r for r, _ in Membership.ROLE_CHOICES])
        if errors:
            return error("Please choose a role.", errors=errors)
        if mm.role == Membership.OWNER and role != Membership.OWNER and not other_owners(org, mm):
            return error("Every organisation needs at least one owner. Make someone else owner first.", status=409)
        old = mm.role
        mm.role = role
        mm.save(update_fields=["role"])
        audit.record(request, org, "update", mm, f"Changed {mm.user.full_name}'s role", {"role": [old, role]})
        return JsonResponse(member_dict(mm))
    if mm.role == Membership.OWNER and not other_owners(org, mm):
        return error("Every organisation needs at least one owner. Make someone else owner first.", status=409)
    mm.is_active = False
    mm.save(update_fields=["is_active"])
    audit.record(request, org, "archive", mm, f"Removed {mm.user.full_name} from the organisation")
    return JsonResponse({"ok": True})


# ---------------------------------------------------------------- farms
class FarmResource(Resource):
    model = Farm
    label = "farm"

    def queryset(self, m, request):
        return (super().queryset(m, request)
                .annotate(n_fields=Count("fields", filter=Q(fields__is_active=True)),
                          field_ha=Sum("fields__size_ha", filter=Q(fields__is_active=True))))

    def to_dict(self, f):
        return {"id": str(f.id), "name": f.name, "province": f.province, "district": f.district,
                "size_ha": dec(f.size_ha), "notes": f.notes, "is_active": f.is_active,
                "fields": getattr(f, "n_fields", None), "field_ha": dec(getattr(f, "field_ha", None) or Decimal("0"))}

    def clean(self, data, m, instance, errors):
        return {
            "name": clean.text(data, "name", errors, required=True, max_len=120, label="the farm name"),
            "province": clean.choice(data, "province", errors, PROVINCES),
            "district": clean.text(data, "district", errors, required=True, max_len=80, label="the district"),
            "size_ha": clean.decimal(data, "size_ha", errors, positive=True, max_value=Decimal("100000")),
            "notes": clean.text(data, "notes", errors, max_len=2000),
        }


class FieldResource(Resource):
    model = Field
    label = "field"

    def queryset(self, m, request):
        qs = super().queryset(m, request).select_related("farm")
        farm = clean.uuid_value(request.GET.get("farm"))
        return qs.filter(farm_id=farm) if farm else qs

    def to_dict(self, f):
        return {"id": str(f.id), "farm": str(f.farm_id), "farm_name": f.farm.name, "name": f.name,
                "size_ha": dec(f.size_ha), "irrigated": f.irrigated, "soil_type": f.soil_type,
                "latitude": dec(f.latitude, 6), "longitude": dec(f.longitude, 6), "notes": f.notes, "is_active": f.is_active}

    def describe(self, f):
        return f"{f.farm.name} / {f.name}"

    def clean(self, data, m, instance, errors):
        farm = Farm.objects.filter(organisation=m.organisation, pk=clean.uuid_value(data.get("farm")), is_active=True).first()
        if farm is None:
            errors["farm"] = "Choose one of your farms."
        lat = clean.decimal(data, "latitude", errors, min_value=Decimal("-90"), max_value=Decimal("90"), places=6)
        lng = clean.decimal(data, "longitude", errors, min_value=Decimal("-180"), max_value=Decimal("180"), places=6)
        return {
            "farm": farm,
            "name": clean.text(data, "name", errors, required=True, max_len=60, label="the field name"),
            "size_ha": clean.decimal(data, "size_ha", errors, required=True, positive=True, max_value=Decimal("100000")),
            "irrigated": clean.boolean(data, "irrigated"),
            "soil_type": clean.text(data, "soil_type", errors, max_len=60),
            "latitude": lat, "longitude": lng,
            "notes": clean.text(data, "notes", errors, max_len=2000),
        }

    def after_validate(self, values, m, instance, errors):
        # A field can't shrink below what is already planned on it in an open season.
        if instance is None:
            return
        busiest = (CropPlan.objects.filter(field=instance).exclude(status=CropPlan.ABANDONED)
                   .exclude(season__status=Season.CLOSED).values("season")
                   .annotate(t=Sum("planted_ha")).order_by("-t").first())
        if busiest and values["size_ha"] is not None and values["size_ha"] < busiest["t"]:
            errors["size_ha"] = f"{busiest['t']:.2f} ha is already planned on this field this season."


# ---------------------------------------------------------------- seasons
class SeasonResource(Resource):
    model = Season
    label = "season"
    edit_permission = "edit_seasons"
    archivable = False

    def queryset(self, m, request):
        return (Season.objects.filter(organisation=m.organisation)
                .annotate(planted_ha=Sum("crop_plans__planted_ha", filter=~Q(crop_plans__status=CropPlan.ABANDONED)),
                          n_plans=Count("crop_plans")))

    def to_dict(self, s):
        return {"id": str(s.id), "name": s.name, "start_date": s.start_date.isoformat(),
                "end_date": s.end_date.isoformat(), "status": s.status, "status_label": s.get_status_display(),
                "planted_ha": dec(getattr(s, "planted_ha", None) or Decimal("0")), "plans": getattr(s, "n_plans", None)}

    def clean(self, data, m, instance, errors):
        start = clean.date(data, "start_date", errors, required=True)
        end = clean.date(data, "end_date", errors, required=True)
        if start and end and end <= start:
            errors["end_date"] = "The end must be after the start."
        return {
            "name": clean.text(data, "name", errors, required=True, max_len=60, label="the season name"),
            "start_date": start, "end_date": end,
            "status": clean.choice(data, "status", errors, [s for s, _ in Season.STATUS_CHOICES], default=Season.PLANNING),
        }


# ---------------------------------------------------------------- crop plans
class CropPlanResource(Resource):
    model = CropPlan
    label = "crop plan"
    edit_permission = "edit_seasons"
    archivable = False

    def queryset(self, m, request):
        qs = CropPlan.objects.filter(organisation=m.organisation).select_related("field__farm", "season")
        season = clean.uuid_value(request.GET.get("season"))
        return qs.filter(season_id=season) if season else qs

    def to_dict(self, p):
        return {"id": str(p.id), "season": str(p.season_id), "season_name": p.season.name,
                "field": str(p.field_id), "field_name": p.field.name, "farm_name": p.field.farm.name,
                "field_size_ha": dec(p.field.size_ha), "crop": p.crop, "variety": p.variety,
                "planted_ha": dec(p.planted_ha),
                "planting_date": p.planting_date.isoformat() if p.planting_date else None,
                "expected_harvest": p.expected_harvest.isoformat() if p.expected_harvest else None,
                "target_yield_t_per_ha": dec(p.target_yield_t_per_ha),
                "target_tonnes": dec((p.target_yield_t_per_ha * p.planted_ha).quantize(Decimal("0.01")))
                if p.target_yield_t_per_ha is not None else None,
                "budget_amount": dec(p.budget_amount), "budget_currency": p.budget_currency,
                "budget_per_ha": dec((p.budget_amount / p.planted_ha).quantize(Decimal("0.01")))
                if p.budget_amount is not None else None,
                "status": p.status, "status_label": p.get_status_display(), "notes": p.notes}

    def describe(self, p):
        return f"{p.crop} on {p.field.farm.name} / {p.field.name}, {p.season.name}"

    def clean(self, data, m, instance, errors):
        org = m.organisation
        season = Season.objects.filter(organisation=org, pk=clean.uuid_value(data.get("season"))).first()
        if season is None:
            errors["season"] = "Choose a season."
        elif season.status == Season.CLOSED:
            errors["season"] = "That season is closed. Re-open it to add or change plans."
        field = Field.objects.filter(organisation=org, pk=clean.uuid_value(data.get("field")), is_active=True).first()
        if field is None:
            errors["field"] = "Choose one of your fields."
        planting = clean.date(data, "planting_date", errors)
        harvest = clean.date(data, "expected_harvest", errors)
        if planting and harvest and harvest <= planting:
            errors["expected_harvest"] = "Harvest must come after planting."
        return {
            "season": season, "field": field,
            "crop": clean.choice(data, "crop", errors, CROPS),
            "variety": clean.text(data, "variety", errors, max_len=60),
            "planted_ha": clean.decimal(data, "planted_ha", errors, required=True, positive=True, max_value=Decimal("100000")),
            "planting_date": planting, "expected_harvest": harvest,
            "target_yield_t_per_ha": clean.decimal(data, "target_yield_t_per_ha", errors, min_value=Decimal("0"),
                                                   max_value=Decimal("1000")),
            "budget_amount": clean.decimal(data, "budget_amount", errors, min_value=Decimal("0")),
            "budget_currency": clean.choice(data, "budget_currency", errors, CURRENCY_CODES, default="USD"),
            "status": clean.choice(data, "status", errors, [s for s, _ in CropPlan.STATUS_CHOICES], default=CropPlan.PLANNED),
            "notes": clean.text(data, "notes", errors, max_len=2000),
        }

    def after_validate(self, values, m, instance, errors):
        # The same field can carry several crops in a season, but not more hectares than it has.
        if values["status"] == CropPlan.ABANDONED:
            return
        others = (CropPlan.objects.filter(field=values["field"], season=values["season"])
                  .exclude(status=CropPlan.ABANDONED))
        if instance is not None:
            others = others.exclude(pk=instance.pk)
        used = others.aggregate(t=Sum("planted_ha"))["t"] or Decimal("0")
        free = values["field"].size_ha - used
        if values["planted_ha"] > free:
            errors["planted_ha"] = (f"Only {free:.2f} ha of {values['field'].name} is free this season "
                                    f"({values['field'].size_ha:.2f} ha, {used:.2f} ha already planned).")


farms_res, fields_res, seasons_res, plans_res = FarmResource(), FieldResource(), SeasonResource(), CropPlanResource()


# ---------------------------------------------------------------- audit
@require_http_methods(["GET"])
@org_view
def audit_log(request, m):
    if not allowed(m, "view_audit"):
        return error("Only the owner, manager or accountant can see the change history.", status=403)
    qs = AuditEvent.objects.filter(organisation=m.organisation).select_related("user")[:200]
    return JsonResponse({"results": [{"at": e.at.isoformat(), "who": e.user.full_name if e.user else "System",
                                      "action": e.action, "type": e.object_type, "summary": e.summary,
                                      "changes": e.changes} for e in qs]})


@require_http_methods(["GET"])
@login_required_json
def lists(request):
    """Choices the web app needs for its forms."""
    return JsonResponse({
        "provinces": PROVINCES, "crops": CROPS, "currencies": CURRENCY_CODES,
        "roles": [{"value": v, "label": l} for v, l in Membership.ROLE_CHOICES],
        "season_statuses": [{"value": v, "label": l} for v, l in Season.STATUS_CHOICES],
        "plan_statuses": [{"value": v, "label": l} for v, l in CropPlan.STATUS_CHOICES],
    })
