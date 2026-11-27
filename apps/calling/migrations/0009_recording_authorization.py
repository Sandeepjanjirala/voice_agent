from django.db import migrations, models
from django.db.models import F


def label_existing_consent(apps, schema_editor):
    apps.get_model("calling", "CallRecording").objects.filter(
        consented_at__isnull=False
    ).update(authorization="verbal", authorized_at=F("consented_at"))


class Migration(migrations.Migration):
    dependencies = [("calling", "0008_call_retry")]

    operations = [
        migrations.AddField(
            model_name="callrecording",
            name="authorization",
            field=models.CharField(
                blank=True,
                choices=[("verbal", "Verbal consent"), ("demo", "Approved demo test")],
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="callrecording",
            name="authorized_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(label_existing_consent, migrations.RunPython.noop),
    ]
