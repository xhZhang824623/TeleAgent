# Generated manually for per-conversation agent selection and client capabilities.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("OnlineBroker", "0004_add_broker_client_credential"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentclient",
            name="supported_agents",
            field=models.JSONField(blank=True, default=list, help_text="本机支持的 Agent CLI 类型列表"),
        ),
        migrations.AddField(
            model_name="conversation",
            name="agent_type",
            field=models.CharField(blank=True, choices=[("codex", "Codex"), ("claude_code", "Claude Code"), ("cursor_agent", "Cursor Agent")], max_length=32),
        ),
        migrations.AddField(
            model_name="task",
            name="agent_type",
            field=models.CharField(blank=True, choices=[("codex", "Codex"), ("claude_code", "Claude Code"), ("cursor_agent", "Cursor Agent")], max_length=32),
        ),
    ]
