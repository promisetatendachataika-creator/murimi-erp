"""
People who use Murimi Farm ERP.

The User table has the SAME fields and rules as Murimi Intelligent Hub, so
when the systems are joined, people are matched by their WhatsApp number.
What someone may do on a farm is NOT stored here: it lives in
erp_core.Membership (their role in each organisation).
"""
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from murimi_common.identity import normalize_phone, normalize_username


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, phone, password=None, **extra):
        user = self.model(phone=normalize_phone(phone), **extra)
        if extra.get("email"):
            user.email = self.normalize_email(extra["email"])
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("full_name", "Murimi admin")
        return self.create_user(phone, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    phone = models.CharField("WhatsApp number", max_length=20, unique=True)
    username = models.CharField(max_length=30, unique=True, null=True, blank=True)
    full_name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    # Kept for compatibility with the Hub (farmer / landowner / investor / driver). Not used by the ERP.
    role = models.CharField(max_length=20, blank=True)
    whatsapp_consent = models.BooleanField(default=False)
    phone_verified_at = models.DateTimeField("WhatsApp number confirmed", null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="Can log in to the Murimi staff admin.")
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.full_name} ({self.phone})"

    def clean(self):
        super().clean()
        try:
            self.phone = normalize_phone(self.phone)
        except ValidationError as e:
            raise ValidationError({"phone": e.messages})
        if self.username:
            try:
                self.username = normalize_username(self.username)
            except ValidationError as e:
                raise ValidationError({"username": e.messages})
        else:
            self.username = None

    def save(self, *args, **kwargs):
        try:
            self.phone = normalize_phone(self.phone)
        except ValidationError:
            pass
        self.username = (self.username or "").strip().lower() or None
        super().save(*args, **kwargs)
