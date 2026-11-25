from django.apps import AppConfig
from django.db.models.signals import post_delete


class CallingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.calling"

    def ready(self) -> None:
        from . import recordings
        from .models import CallRecording

        # However a recording row goes (its call deleted by retention, by hand,
        # with its organisation), its stored file goes with it.
        def discard(sender, instance, **kwargs):
            recordings.discard_file(instance)

        post_delete.connect(
            discard,
            sender=CallRecording,
            # A nested function: held strongly, or it is garbage-collected.
            weak=False,
            dispatch_uid="call-recording-file",
        )
