from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from apps.core import voice_views
from apps.telephony import views as telephony_views

urlpatterns = [
    path("", voice_views.workspace, name="voice"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("api/voice/token/", voice_views.token, name="voice-token"),
    path("calls/", telephony_views.calls_page, name="calls"),
    path("agent/", telephony_views.agent_page, name="agent"),
    path("api/telephony/", include("apps.telephony.urls")),
    path("admin/", admin.site.urls),
    path("api/", include("apps.core.urls")),
]
