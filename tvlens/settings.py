"""
Django settings for TVLens project.

MovieLens-inspired TV show recommendation platform.
"""

import importlib.util
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default):
    return os.getenv(name, default).lower() in ("true", "1", "yes")


# Off unless asked for. The old default was on, so a server that forgot to set
# DEBUG would show tracebacks and settings to anyone. Development sets
# DEBUG=True in .env; a deploy that sets nothing is safe by default.
DEBUG = env_bool("DEBUG", "False")

# A missing key in development falls back to a placeholder; in production it
# refuses to start, because a known key lets anyone forge sessions.
SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is off.")
    SECRET_KEY = "insecure-development-only-key"

ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# The origins a form may be posted from, e.g. "https://app.tvlens.org".
# Django 4+ checks them on every HTTPS POST.
CSRF_TRUSTED_ORIGINS = [o for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o]

# HTTPS everywhere once DEBUG is off: redirect plain HTTP, and send the session
# and CSRF cookies only over HTTPS so they can't be read off the wire.
if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", "True")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # HSTS tells browsers to refuse plain HTTP for this host. It is sticky:
    # a browser that has seen it keeps it for the full duration, even if the
    # site later needs HTTP. So it starts at one hour, covers only this host
    # (not every subdomain of tvlens.org), and goes up once the deploy is
    # proven. Raise SECURE_HSTS_SECONDS in the environment, not here.
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "3600"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False

# Behind a host's proxy, the app sees plain HTTP even when the visitor used
# HTTPS, and SECURE_SSL_REDIRECT would loop forever. Hosts that terminate TLS
# say so in this header; trust it only when the host is known to set it.
if env_bool("TRUST_PROXY_SSL_HEADER", "False"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Local
    "shows",
]

# django-extensions is a development convenience (shell_plus, graph_models) and
# nothing in the app imports it. Added only when it is actually installed, so
# production can ship without it rather than carrying a dev tool it never calls.
# See requirements-dev.txt.
if importlib.util.find_spec("django_extensions"):
    INSTALLED_APPS.insert(-1, "django_extensions")

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Right after SecurityMiddleware, as whitenoise asks: static files are
    # served before sessions or auth do any work for them.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "tvlens.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "tvlens.wsgi.application"

# Database

DATABASES = {
    "default": {
        "ENGINE": os.getenv("DB_ENGINE", "django.db.backends.sqlite3"),
        "NAME": os.getenv("DB_NAME", str(BASE_DIR / "db.sqlite3")),
        "USER": os.getenv("DB_USER", ""),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", ""),
        "PORT": os.getenv("DB_PORT", ""),
        # WAL so a background job writing continuously stops starving everyone
        # else. Under the default "delete" journal a commit needs an exclusive
        # lock on the whole file, so an open read blocks the ingest and the
        # ingest blocks reads; measured on this catalog, both fail outright.
        # Under WAL a reader and a writer coexist and only writer-versus-writer
        # contends. "timeout" makes that loser wait its turn for 20s instead of
        # raising "database is locked" after Python's 5s default.
        # Left "synchronous" at FULL: NORMAL would trade the last few
        # transactions on power loss, and nobody has made that call. See #23.
        "OPTIONS": {"timeout": 20, "init_command": "PRAGMA journal_mode=WAL;"},
    }
}

# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# In production, collectstatic writes hashed, compressed copies (style.3f2a.css)
# so browsers can cache them forever and still see a change the moment it ships.
# Development keeps the plain storage, which needs no collectstatic run.
if not DEBUG:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

# Media files

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Default primary key field type

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# TMDB API

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p"

# Pagination

SHOWS_PER_PAGE = 24
REVIEWS_PER_PAGE = 10

# Email (console backend in dev — emails print to terminal)

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "noreply@tvlens.org")

# Beta invites: comma-separated codes, each good for one sign-up. Unset means
# no codes, so sign-up is closed and browsing stays open. Set them on Fly with
# `fly secrets set BETA_INVITE_CODES=...`.
BETA_INVITE_CODES = {c.strip() for c in os.getenv("BETA_INVITE_CODES", "").split(",") if c.strip()}

# Authentication

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# Logging: errors go to stdout, which is where Fly collects logs (`fly logs`).
# Django's own default sends them to mail_admins only when DEBUG is off, and
# there is no mail server and no ADMINS, so a crash in production would leave
# no trace at all.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
