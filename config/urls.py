from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from apps.core import voice_views

urlpatterns = [
    path("", voice_views.workspace, name="voice"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("api/voice/token/", voice_views.token, name="voice-token"),
    path("admin/", admin.site.urls),
    path("api/", include("apps.core.urls")),
]
