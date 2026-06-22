from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("OnlineBroker", "0008_conversation_warm_session_presence"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="title_custom",
            field=models.BooleanField(
                default=False,
                help_text="标题是否由用户显式设定（自动摘要不覆盖）",
            ),
        ),
    ]
