from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("OnlineBroker", "0007_task_lease_heartbeat"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="is_open",
            field=models.BooleanField(
                default=False,
                help_text="Web 端是否正打开此会话（用于预热常驻 Agent 进程）",
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="viewer_heartbeat_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="Web 端最近一次打开心跳时间",
            ),
        ),
    ]
