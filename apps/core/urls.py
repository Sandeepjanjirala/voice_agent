from django.urls import path

from . import assistant, views

app_name = "core"

urlpatterns = [
    path("assistant/chat/", assistant.chat, name="assistant-chat"),
    path("health/", views.health, name="health"),
]
