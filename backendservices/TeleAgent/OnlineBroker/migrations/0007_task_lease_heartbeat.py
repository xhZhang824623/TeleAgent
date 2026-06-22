# Generated manually for task lease heartbeat support.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("OnlineBroker", "0006_add_plain_secret_value"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="heartbeat_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
