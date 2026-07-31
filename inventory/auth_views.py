from math import ceil

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.db.models import Count
from django.shortcuts import redirect, render

from .forms import AccountPasswordChangeForm
from .models import LoginAttempt
from .security import (
    get_client_ip, get_login_lockout, normalize_login_username,
    record_blocked_login_attempt, record_login_attempt,
)


class SecureLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            "login_max_failures": settings.LOGIN_MAX_FAILURES,
            "login_lockout_minutes": settings.LOGIN_LOCKOUT_MINUTES,
        })
        return context

    def _locked_response(self, form, lockout):
        minutes = max(1, ceil(lockout.retry_after_seconds / 60))
        response = self.render_to_response(self.get_context_data(
            form=form,
            login_lockout=lockout,
            login_lockout_message=(
                f"Слишком много неудачных попыток. Повторите вход примерно через {minutes} мин."
            ),
        ))
        response.status_code = 429
        response["Retry-After"] = str(lockout.retry_after_seconds)
        return response

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        username = request.POST.get("username", "")
        password_supplied = bool(request.POST.get("password", ""))
        client_ip = get_client_ip(request)

        lockout = get_login_lockout(username, client_ip)
        if lockout.blocked:
            reason = (
                "Активна блокировка для логина и IP-адреса"
                if lockout.scope == "username_ip"
                else "Активна блокировка IP-адреса"
            )
            record_blocked_login_attempt(request, username, reason)
            locked_form = self.get_form_class()(request=request, initial={"username": username})
            return self._locked_response(locked_form, lockout)

        if form.is_valid():
            user = form.get_user()
            record_login_attempt(
                request,
                username,
                LoginAttempt.Result.SUCCESS,
                user=user,
                reason="Успешная аутентификация",
            )
            return self.form_valid(form)

        if username.strip() and password_supplied:
            record_login_attempt(
                request,
                username,
                LoginAttempt.Result.FAILED,
                reason="Неверное имя пользователя или пароль",
            )
            lockout = get_login_lockout(username, client_ip)
            if lockout.blocked:
                return self._locked_response(form, lockout)
        return self.form_invalid(form)


@login_required
def account_view(request):
    password_form = AccountPasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST":
        if password_form.is_valid():
            user = password_form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "Пароль изменён. Текущий сеанс сохранён.")
            return redirect("account")
        messages.error(request, "Пароль не изменён. Проверьте указанные значения.")

    attempts = LoginAttempt.objects.select_related("user")
    if not request.user.is_superuser:
        attempts = attempts.filter(
            username_normalized=normalize_login_username(request.user.get_username())
        )
    attempts = attempts[: settings.LOGIN_LOG_DISPLAY_LIMIT]

    stats_query = LoginAttempt.objects.all()
    if not request.user.is_superuser:
        stats_query = stats_query.filter(
            username_normalized=normalize_login_username(request.user.get_username())
        )
    stats = {
        row["result"]: row["total"]
        for row in stats_query.values("result").annotate(total=Count("pk"))
    }

    context = {
        "password_form": password_form,
        "attempts": attempts,
        "success_count": stats.get(LoginAttempt.Result.SUCCESS, 0),
        "failed_count": stats.get(LoginAttempt.Result.FAILED, 0),
        "blocked_count": stats.get(LoginAttempt.Result.BLOCKED, 0),
        "login_max_failures": settings.LOGIN_MAX_FAILURES,
        "login_ip_max_failures": settings.LOGIN_IP_MAX_FAILURES,
        "login_failure_window_minutes": settings.LOGIN_FAILURE_WINDOW_MINUTES,
        "login_lockout_minutes": settings.LOGIN_LOCKOUT_MINUTES,
    }
    return render(request, "registration/account.html", context)
