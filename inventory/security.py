"""Authentication security helpers for login audit and throttling."""

from dataclasses import dataclass
from datetime import timedelta
from ipaddress import ip_address

from django.conf import settings
from django.db.models import Count, Max
from django.utils import timezone

from .models import LoginAttempt


@dataclass(frozen=True)
class LoginLockout:
    blocked: bool = False
    until: object | None = None
    scope: str = ""
    username_failures: int = 0
    ip_failures: int = 0

    @property
    def retry_after_seconds(self):
        if not self.blocked or not self.until:
            return 0
        return max(1, int((self.until - timezone.now()).total_seconds()))


def normalize_login_username(value):
    return (value or "").strip().casefold()[:150]


def _valid_ip(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return str(ip_address(value))
    except ValueError:
        return None


def get_client_ip(request):
    """Return a validated client IP, optionally using configured proxy headers."""
    if getattr(settings, "LOGIN_TRUST_PROXY_HEADERS", False):
        for header in getattr(settings, "LOGIN_PROXY_IP_HEADERS", ()):
            raw_value = request.META.get(header, "")
            if not raw_value:
                continue
            candidates = raw_value.split(",") if header == "HTTP_X_FORWARDED_FOR" else [raw_value]
            for candidate in candidates:
                parsed = _valid_ip(candidate)
                if parsed:
                    return parsed
    return _valid_ip(request.META.get("REMOTE_ADDR"))


def record_login_attempt(request, username, result, user=None, reason=""):
    return LoginAttempt.objects.create(
        username=(username or "").strip()[:150],
        username_normalized=normalize_login_username(username),
        user=user,
        ip_address=get_client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:512],
        result=result,
        reason=(reason or "")[:255],
    )


def record_blocked_login_attempt(request, username, reason):
    """Record blocked traffic without allowing a blocked client to flood the audit table."""
    ip = get_client_ip(request)
    recent_since = timezone.now() - timedelta(
        seconds=settings.LOGIN_BLOCKED_LOG_INTERVAL_SECONDS
    )
    duplicate = LoginAttempt.objects.filter(
        username_normalized=normalize_login_username(username),
        ip_address=ip,
        result=LoginAttempt.Result.BLOCKED,
        created_at__gte=recent_since,
    ).exists()
    if duplicate:
        return None
    return record_login_attempt(
        request,
        username,
        LoginAttempt.Result.BLOCKED,
        reason=reason,
    )


def _username_ip_failures(username_normalized, ip, now):
    if not username_normalized:
        return 0, None
    window_start = now - timedelta(minutes=settings.LOGIN_FAILURE_WINDOW_MINUTES)
    success_filters = {
        "username_normalized": username_normalized,
        "ip_address": ip,
        "result": LoginAttempt.Result.SUCCESS,
        "created_at__gte": window_start,
    }
    last_success = LoginAttempt.objects.filter(**success_filters).aggregate(
        value=Max("created_at")
    )["value"]
    since = max(window_start, last_success) if last_success else window_start
    stats = LoginAttempt.objects.filter(
        username_normalized=username_normalized,
        ip_address=ip,
        result=LoginAttempt.Result.FAILED,
        created_at__gt=since,
    ).aggregate(count=Count("pk"), latest=Max("created_at"))
    return stats["count"], stats["latest"]


def _ip_failures(ip, now):
    if not ip:
        return 0, None
    window_start = now - timedelta(minutes=settings.LOGIN_FAILURE_WINDOW_MINUTES)
    stats = LoginAttempt.objects.filter(
        ip_address=ip,
        result=LoginAttempt.Result.FAILED,
        created_at__gte=window_start,
    ).aggregate(count=Count("pk"), latest=Max("created_at"))
    return stats["count"], stats["latest"]


def get_login_lockout(username, ip, now=None):
    now = now or timezone.now()
    username_normalized = normalize_login_username(username)
    username_count, username_latest = _username_ip_failures(username_normalized, ip, now)
    ip_count, ip_latest = _ip_failures(ip, now)

    lockout_delta = timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
    candidates = []
    if username_count >= settings.LOGIN_MAX_FAILURES and username_latest:
        candidates.append((username_latest + lockout_delta, "username_ip"))
    if ip_count >= settings.LOGIN_IP_MAX_FAILURES and ip_latest:
        candidates.append((ip_latest + lockout_delta, "ip"))

    active = [(until, scope) for until, scope in candidates if until > now]
    if not active:
        return LoginLockout(
            username_failures=username_count,
            ip_failures=ip_count,
        )

    until, scope = max(active, key=lambda item: item[0])
    return LoginLockout(
        blocked=True,
        until=until,
        scope=scope,
        username_failures=username_count,
        ip_failures=ip_count,
    )
