from django.urls import path

from . import views

app_name = "telephony"

urlpatterns = [
    path("calls/", views.CallListView.as_view(), name="calls"),
    path("calls/<uuid:pk>/", views.CallDetailView.as_view(), name="call"),
    path("calls/<uuid:pk>/hangup/", views.CallHangupView.as_view(), name="hangup"),
    path("call-events/", views.call_events, name="call-events"),
    path("call-profile/", views.call_profile, name="call-profile"),
    path("call-recording/", views.call_recording, name="call-recording"),
    path("inbound-context/", views.inbound_context, name="inbound-context"),
]
