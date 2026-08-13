import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
IS_TESTING = "test" in sys.argv
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG or IS_TESTING:
        SECRET_KEY = "development-only-secret-key"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required when DJANGO_DEBUG=0.")
ALLOWED_HOSTS = [x.strip() for x in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if x.strip()]
CSRF_TRUSTED_ORIGINS = [x.strip() for x in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if x.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "inventory.apps.InventoryConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ],
    },
}]
WSGI_APPLICATION = "config.wsgi.application"

if os.getenv("DB_ENGINE") == "sqlite":
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "fox_inventory"),
            "USER": os.getenv("POSTGRES_USER", "fox_inventory"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", "fox_inventory"),
            "HOST": os.getenv("DB_HOST", "db"),
            "PORT": os.getenv("DB_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "ru-ru"
TIME_ZONE = os.getenv("TIME_ZONE", "Europe/Moscow")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG or IS_TESTING
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
FIELD_ENCRYPTION_KEY = os.getenv("FIELD_ENCRYPTION_KEY", "")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_MB * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# Authentication audit and throttling.
LOGIN_MAX_FAILURES = max(1, int(os.getenv("LOGIN_MAX_FAILURES", "5")))
LOGIN_IP_MAX_FAILURES = max(LOGIN_MAX_FAILURES, int(os.getenv("LOGIN_IP_MAX_FAILURES", "20")))
LOGIN_FAILURE_WINDOW_MINUTES = max(1, int(os.getenv("LOGIN_FAILURE_WINDOW_MINUTES", "15")))
LOGIN_LOCKOUT_MINUTES = max(1, int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15")))
LOGIN_LOG_DISPLAY_LIMIT = max(20, int(os.getenv("LOGIN_LOG_DISPLAY_LIMIT", "100")))
LOGIN_BLOCKED_LOG_INTERVAL_SECONDS = max(1, int(os.getenv("LOGIN_BLOCKED_LOG_INTERVAL_SECONDS", "60")))
LOGIN_TRUST_PROXY_HEADERS = os.getenv(
    "LOGIN_TRUST_PROXY_HEADERS", os.getenv("TRUST_X_FORWARDED_PROTO", "0")
) == "1"
LOGIN_PROXY_IP_HEADERS = tuple(
    item.strip()
    for item in os.getenv(
        "LOGIN_PROXY_IP_HEADERS",
        "HTTP_CF_CONNECTING_IP,HTTP_X_FORWARDED_FOR,HTTP_X_REAL_IP",
    ).split(",")
    if item.strip()
)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "SAMEORIGIN"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False

ACT_DEFAULT_CITY = os.getenv("ACT_DEFAULT_CITY", "")
ACT_ISSUE_REPRESENTATIVE_POSITION = os.getenv("ACT_ISSUE_REPRESENTATIVE_POSITION", "")
ACT_ISSUE_REPRESENTATIVE_NAME = os.getenv("ACT_ISSUE_REPRESENTATIVE_NAME", "")
ACT_RETURN_REPRESENTATIVE_POSITION = os.getenv("ACT_RETURN_REPRESENTATIVE_POSITION", "")
ACT_RETURN_REPRESENTATIVE_NAME = os.getenv("ACT_RETURN_REPRESENTATIVE_NAME", "")

SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "0") == "1"
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"
CSRF_COOKIE_SECURE = os.getenv("CSRF_COOKIE_SECURE", "0") == "1"
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv("SECURE_HSTS_INCLUDE_SUBDOMAINS", "0") == "1"
SECURE_HSTS_PRELOAD = os.getenv("SECURE_HSTS_PRELOAD", "0") == "1"
if os.getenv("TRUST_X_FORWARDED_PROTO", "0") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
