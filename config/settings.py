"""
Settings for Murimi Farm ERP.

Everything that differs between your laptop and a live server comes from
environment variables or a .env file next to manage.py (see .env.example).
The defaults are safe for running on your own computer only.
"""
import mimetypes
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("application/vnd.android.package-archive", ".apk")


def _load_dotenv(path):
    """Read KEY=VALUE lines from .env (if present). Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return str(env(name, str(default))).lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [x.strip() for x in env(name, default).split(",") if x.strip()]


DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("Set DJANGO_SECRET_KEY before running with DJANGO_DEBUG=False.")
    SECRET_KEY = "dev-only-insecure-key-change-me"

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Shared by every Murimi module (copied from the Hub's rules).
    "murimi_common",
    # People and log-in (same fields and rules as Murimi Intelligent Hub).
    "accounts",
    # The Farm ERP itself. Every app is prefixed erp_ so it can be copied into the Hub later.
    "erp_core",      # organisations, members and roles, farms, fields, seasons, crop plans, audit log
    "erp_finance",   # currencies, exchange rates, money helpers
    "erp_inventory", # stock in batches, stores, fuel and machines
    "erp_labour",    # workers, piece work, advances, payroll (PAYE, AIDS levy, NSSA)
    "erp_trade",     # customers, suppliers, sales invoices, purchases, VAT, harvests, reports
    # Serves the web app.
    "web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

if env("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB"),
            "USER": env("POSTGRES_USER", ""),
            "PASSWORD": env("POSTGRES_PASSWORD", ""),
            "HOST": env("POSTGRES_HOST", "localhost"),
            "PORT": env("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "accounts.backends.PhoneOrUsernameBackend",
    "django.contrib.auth.backends.ModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
     "OPTIONS": {"user_attributes": ("phone", "username", "full_name", "email")}},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# Exchange rates fetched online (see erp_finance/fetch.py).
MURIMI_RATES_AUTO = env_bool("MURIMI_RATES_AUTO", True)
MURIMI_RATES_URL = env("MURIMI_RATES_URL", "")
# Fiscal device for sales invoices (see erp_trade/fiscal.py). "manual" = type in your fiscal receipt numbers.
MURIMI_FISCAL_DEVICE = env("MURIMI_FISCAL_DEVICE", "manual")

# The Android app (see android/ and PHONE_APP.md). The fingerprint proves the APK is ours, so
# Android opens the site full-screen with no address bar.
ANDROID_PACKAGE = env("ANDROID_PACKAGE", "zw.murimi.erp")
ANDROID_CERT_SHA256 = env_list("ANDROID_CERT_SHA256", "")

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_CONTENT_TYPE_NOSNIFF = True

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Harare"
USE_I18N = True
USE_TZ = True

# Bump this whenever app.js / app.css change, so browsers and installed phone apps fetch the new files.
MURIMI_ASSET_VERSION = "8"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if len(sys.argv) > 1 and sys.argv[1] == "test":
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # faster tests only
    MURIMI_RATES_AUTO = False  # tests never touch the internet
