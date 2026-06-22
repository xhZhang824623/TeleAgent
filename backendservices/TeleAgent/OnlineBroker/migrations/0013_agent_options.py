from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("OnlineBroker", "0012_conversation_force"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="options",
            field=models.JSONField(
                blank=True, default=dict,
                help_text="会话级 Agent 参数（permission_mode/model/effort 等）",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="options",
            field=models.JSONField(
                blank=True, default=dict,
                help_text="任务级 Agent 参数（建任务时从会话继承）",
            ),
        ),
    ]
