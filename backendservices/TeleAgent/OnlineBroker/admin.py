from django.contrib import admin
from django.contrib.auth.hashers import make_password
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django import forms
from .models import AgentClient, BrokerClientCredential, Conversation, Task, Message


class BrokerClientCredentialCreateForm(forms.ModelForm):
    """创建时填写 Secret Key（明文），保存为哈希。"""
    secret_key = forms.CharField(
        required=True,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        label="Secret Key",
        help_text="创建后请将 ID 与 Secret Key 告知 PC 使用者；Secret 仅此时可见。",
    )

    class Meta:
        model = BrokerClientCredential
        fields = ["name", "user"]

    def save(self, commit=True):
        obj = super().save(commit=False)
        sk = self.cleaned_data.get("secret_key")
        if sk:
            obj.secret_hash = make_password(sk)
        if commit:
            obj.save()
        return obj


class BrokerClientCredentialChangeForm(forms.ModelForm):
    class Meta:
        model = BrokerClientCredential
        fields = ["name", "user"]


@admin.register(BrokerClientCredential)
class BrokerClientCredentialAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "user", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["name", "user__email", "user__username"]
    readonly_fields = ["id", "created_at"]

    fieldsets = (
        (None, {"fields": ("id", "name", "user")}),
        ("凭证", {"fields": ("secret_key",), "description": "将 ID（上方）与 Secret Key 告知 PC 使用者，用于 LocalBroker 登录。"}),
        ("时间", {"fields": ("created_at",)}),
    )

    def get_fieldsets(self, request, obj=None):
        if obj:
            return (
                (None, {"fields": ("id", "name", "user")}),
                ("凭证说明", {"fields": (), "description": "Secret Key 仅创建时设置，不可查看或修改。如需更换请新建凭证并告知 PC 新 ID+Secret。"}),
                ("时间", {"fields": ("created_at",)}),
            )
        return (
            (None, {"fields": ("name", "user")}),
            ("凭证", {"fields": ("secret_key",), "description": "创建保存后，在列表页查看生成的 ID，将 ID 与本次填写的 Secret Key 告知 PC 使用者，用于 LocalBroker 登录。"}),
        )

    def get_form(self, request, obj=None, change=False, **kwargs):
        if obj is None:
            return BrokerClientCredentialCreateForm
        return BrokerClientCredentialChangeForm


@admin.register(AgentClient)
class AgentClientAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "owner", "hostname", "last_seen", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["name", "hostname", "owner__email", "owner__username"]
    raw_id_fields = ["owner"]
    readonly_fields = ["id", "created_at", "last_seen"]
    fieldsets = (
        (None, {"fields": ("id", "name", "hostname", "owner")}),
        ("时间", {"fields": ("last_seen", "created_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("owner")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ["id", "title", "owner", "cwd", "assigned_client", "session_id", "created_at", "updated_at"]
    list_filter = ["created_at"]
    search_fields = ["title", "cwd", "owner__email"]
    raw_id_fields = ["owner", "assigned_client"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ["id", "conversation", "prompt_short", "status", "started_at", "finished_at", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["prompt", "cwd"]

    def prompt_short(self, obj):
        return (obj.prompt[:50] + "…") if len(obj.prompt) > 50 else obj.prompt
    prompt_short.short_description = "Prompt"


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["id", "conversation", "prompt_short", "task", "created_at"]
    search_fields = ["prompt"]

    def prompt_short(self, obj):
        return (obj.prompt[:50] + "…") if len(obj.prompt) > 50 else obj.prompt
    prompt_short.short_description = "Prompt"


# 用户管理：覆盖默认 User 以在管理平台中更清晰
if admin.site.is_registered(User):
    admin.site.unregister(User)


@admin.register(User)
class CustomUserAdmin(BaseUserAdmin):
    list_display = ["username", "email", "is_staff", "is_active", "date_joined"]
    list_filter = ["is_staff", "is_active", "is_superuser"]
    search_fields = ["username", "email", "first_name", "last_name"]
