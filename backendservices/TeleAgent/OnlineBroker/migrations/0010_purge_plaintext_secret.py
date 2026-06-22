from django.db import migrations


def clear_secret_values(apps, schema_editor):
    """清除历史明文 secret（仅保留 hash）。"""
    Cred = apps.get_model("OnlineBroker", "BrokerClientCredential")
    Cred.objects.exclude(secret_value="").update(secret_value="")


class Migration(migrations.Migration):

    dependencies = [
        ("OnlineBroker", "0009_conversation_title_custom"),
    ]

    operations = [
        migrations.RunPython(clear_secret_values, migrations.RunPython.noop),
    ]
