from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from inventory.views import health, protected_media

urlpatterns = [
    path("health/", health, name="health"),
    path("media/<path:path>", protected_media, name="protected_media"),
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("inventory.urls")),
]
