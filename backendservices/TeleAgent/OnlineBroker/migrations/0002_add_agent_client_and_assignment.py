# Generated manually for multi-PC Agent client selection

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("OnlineBroker", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentClient",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(help_text="显示名，如「Neal 的笔记本」", max_length=128)),
                ("hostname", models.CharField(blank=True, max_length=256)),
                ("last_seen", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-last_seen", "-created_at"],
            },
        ),
        migrations.AddField(
            model_name="conversation",
            name="assigned_client",
            field=models.ForeignKey(
                blank=True,
                help_text="指定由哪台 PC 的 Agent 执行",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="conversations",
                to="OnlineBroker.agentclient",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="assigned_client",
            field=models.ForeignKey(
                blank=True,
                help_text="仅该客户端可拉取并执行此任务",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tasks",
                to="OnlineBroker.agentclient",
            ),
        ),
    ]
