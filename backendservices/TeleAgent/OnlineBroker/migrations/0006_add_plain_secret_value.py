# Generated manually to support credential reveal in the frontend.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("OnlineBroker", "0005_add_agent_type_and_supported_agents"),
    ]

    operations = [
        migrations.AddField(
            model_name="brokerclientcredential",
            name="secret_value",
            field=models.CharField(blank=True, help_text="用于前端二次查看的明文 Secret Key", max_length=255),
        ),
    ]
