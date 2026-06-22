from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("OnlineBroker", "0011_alter_agentclient_options_alter_conversation_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="force",
            field=models.BooleanField(
                default=False,
                help_text="是否允许 Agent 自动执行（跳过确认）；常驻进程启动时生效",
            ),
        ),
    ]
