"""Sign up, log in, log out and "who am I" for the Farm ERP."""
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from murimi_common.http import body, client_ip, error, login_required_json, reset_counter, too_many
from murimi_common.identity import looks_like_username, normalize_phone, normalize_username

from .models import User


def user_dict(user):
    from erp_core.models import Membership  # here to avoid a circular import
    memberships = (Membership.objects.filter(user=user, is_active=True, organisation__is_active=True)
                   .select_related("organisation").order_by("organisation__name"))
    return {
        "id": user.pk,
        "full_name": user.full_name,
        "phone": user.phone,
        "username": user.username or "",
        "email": user.email,
        "is_staff": user.is_staff,
        "organisations": [{"id": str(m.organisation_id), "name": m.organisation.name, "role": m.role,
                           "role_label": m.get_role_display()} for m in memberships],
    }


def clean_account(data, errors, user=None, password_required=True):
    out = {}
    name = str(data.get("full_name", "")).strip()
    if len(name) < 2:
        errors["full_name"] = "Please enter your full name."
    out["full_name"] = name[:120]
    try:
        phone = normalize_phone(str(data.get("phone", "")))
        taken = User.objects.filter(phone=phone)
        if user:
            taken = taken.exclude(pk=user.pk)
        if taken.exists():
            errors["phone"] = "There's already an account with this number. Log in instead."
        out["phone"] = phone
    except ValidationError as e:
        errors["phone"] = e.messages[0]
    raw_username = str(data.get("username", "") or "").strip()
    if raw_username:
        try:
            username = normalize_username(raw_username)
            taken = User.objects.filter(username=username)
            if user:
                taken = taken.exclude(pk=user.pk)
            if taken.exists():
                errors["username"] = "That username is taken."
            out["username"] = username
        except ValidationError as e:
            errors["username"] = e.messages[0]
    else:
        out["username"] = None
    email = str(data.get("email", "") or "").strip()
    if email:
        try:
            validate_email(email)
        except ValidationError:
            errors["email"] = "That email doesn't look right."
    out["email"] = email.lower()
    password = data.get("password") or ""
    if password or password_required:
        candidate = user or User(full_name=out["full_name"], email=out["email"], phone=out.get("phone", ""))
        try:
            validate_password(password, user=candidate)
        except ValidationError as e:
            errors["password"] = " ".join(e.messages)
    out["password"] = password
    return out


@require_GET
@ensure_csrf_cookie
def health(request):
    return JsonResponse({"service": "murimi-erp", "ok": True})


@require_POST
def register(request):
    if too_many(f"register:{client_ip(request)}", limit=10, window_seconds=3600):
        return error("Too many sign-ups from this connection. Please try again in an hour.", status=429)
    errors = {}
    account = clean_account(body(request), errors)
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    user = User.objects.create_user(phone=account["phone"], password=account["password"],
                                    full_name=account["full_name"], username=account["username"],
                                    email=account["email"])
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return JsonResponse({"user": user_dict(user)}, status=201)


@require_POST
def login_view(request):
    data = body(request)
    identifier = str(data.get("login") or "").strip()
    password = data.get("password") or ""
    if not identifier:
        return error("Enter your WhatsApp number or username.")
    if looks_like_username(identifier):
        who = identifier.lower().lstrip("@")
    else:
        try:
            who = normalize_phone(identifier)
        except ValidationError:
            return error("Enter your WhatsApp number with the country code, or your username.")
    key = f"login:{client_ip(request)}:{who}"
    if too_many(key, limit=5, window_seconds=15 * 60):
        return error("Too many attempts. Please wait 15 minutes and try again.", status=429)
    user = authenticate(request, username=identifier, password=password)
    if user is None:
        return error("That number or username and password don't match.")
    reset_counter(key)
    login(request, user)
    return JsonResponse({"user": user_dict(user)})


@require_POST
def logout_view(request):
    logout(request)
    return JsonResponse({"ok": True})


@require_http_methods(["GET", "PATCH"])
@login_required_json
def me(request):
    user = request.user
    if request.method == "GET":
        return JsonResponse({"user": user_dict(user)})
    errors = {}
    data = body(request)
    data.setdefault("phone", user.phone)
    account = clean_account(data, errors, user=user, password_required=False)
    if errors:
        return error("Please fix the highlighted fields.", errors=errors)
    user.full_name, user.phone, user.username, user.email = (account["full_name"], account["phone"],
                                                            account["username"], account["email"])
    if account["password"]:
        user.set_password(account["password"])
    user.save()
    if account["password"]:
        update_session_auth_hash(request, user)
    return JsonResponse({"user": user_dict(user)})
