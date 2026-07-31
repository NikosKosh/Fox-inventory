from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from inventory.auth_views import SecureLoginView
from inventory.views import health, protected_media

urlpatterns = [
    path("health/", health, name="health"),
    path("media/<path:path>", protected_media, name="protected_media"),
    path("admin/", admin.site.urls),
    path("login/", SecureLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("inventory.urls")),
]
