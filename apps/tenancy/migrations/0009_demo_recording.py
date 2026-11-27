from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenancy", "0008_universal_labels")]

    operations = [
        migrations.AlterField(
            model_name="organisation",
            name="recording",
            field=models.CharField(
                choices=[
                    ("off", "Never record calls"),
                    ("demo", "Demo test calls, without a recording question"),
                    ("test", "Record approved test calls only, after consent"),
                    ("consent", "Record any call, after the person agrees on the call"),
                ],
                default="off",
                max_length=8,
            ),
        ),
    ]
