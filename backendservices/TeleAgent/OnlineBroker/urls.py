from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from . import views

app_name = "OnlineBroker"

# URL 层对 Broker API 做 CSRF 豁免（前端 / LocalBroker 无 session 表单）
urlpatterns = [
    path("credentials/", csrf_exempt(views.BrokerClientCredentialListCreateView.as_view()), name="broker-credential-list-create"),
    path("clients/", csrf_exempt(views.AgentClientListView.as_view()), name="agent-client-list"),
    path("clients/<uuid:client_id>/", csrf_exempt(views.AgentClientDetailView.as_view()), name="agent-client-detail"),
    path("tasks/queued/", csrf_exempt(views.QueuedTasksView.as_view()), name="queued-tasks"),
    path("conversations/", csrf_exempt(views.ConversationListCreateView.as_view()), name="conversation-list-create"),
    path("conversations/active/", csrf_exempt(views.ActiveConversationsView.as_view()), name="active-conversations"),
    path("conversations/<uuid:conv_id>/", csrf_exempt(views.ConversationDetailView.as_view()), name="conversation-detail"),
    path("conversations/<uuid:conv_id>/open/", csrf_exempt(views.ConversationOpenView.as_view()), name="conversation-open"),
    path("conversations/<uuid:conv_id>/close/", csrf_exempt(views.ConversationCloseView.as_view()), name="conversation-close"),
    path("conversations/<uuid:conv_id>/control/", csrf_exempt(views.ConversationControlView.as_view()), name="conversation-control"),
    path("controls/pending/", csrf_exempt(views.PendingControlsView.as_view()), name="controls-pending"),
    path("controls/<uuid:control_id>/", csrf_exempt(views.ControlDetailView.as_view()), name="control-detail"),
    path("conversations/<uuid:conv_id>/messages/", csrf_exempt(views.SendMessageView.as_view()), name="send-message"),
    path("conversations/<uuid:conv_id>/tasks/", csrf_exempt(views.TaskListByConversationView.as_view()), name="task-list-by-conv"),
    path("tasks/<uuid:task_id>/", csrf_exempt(views.TaskDetailView.as_view()), name="task-detail"),
    path("tasks/<uuid:task_id>/events/", csrf_exempt(views.TaskEventsAppendView.as_view()), name="task-events-append"),
    path("tasks/<uuid:task_id>/stream/", csrf_exempt(views.task_stream_view), name="task-stream"),
]
