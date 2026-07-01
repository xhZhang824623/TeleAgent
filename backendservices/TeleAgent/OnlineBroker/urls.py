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
    path("fs/browse/", csrf_exempt(views.FsBrowseCreateView.as_view()), name="fs-browse-create"),
    path("fs/browse/<uuid:req_id>/", csrf_exempt(views.FsBrowseDetailView.as_view()), name="fs-browse-detail"),
    path("fs/pending/", csrf_exempt(views.PendingFsRequestsView.as_view()), name="fs-pending"),
    path("fs/requests/<uuid:req_id>/", csrf_exempt(views.FsRequestAckView.as_view()), name="fs-request-ack"),
    path("permissions/", csrf_exempt(views.PermissionRequestCreateView.as_view()), name="permission-create"),
    path("permissions/<uuid:perm_id>/", csrf_exempt(views.PermissionRequestDetailView.as_view()), name="permission-detail"),
    path("conversations/<uuid:conv_id>/permissions/pending/", csrf_exempt(views.PendingPermissionsView.as_view()), name="permissions-pending"),
    path("conversations/<uuid:conv_id>/permissions/", csrf_exempt(views.ConversationPermissionsView.as_view()), name="conversation-permissions"),
    path("files/request/", csrf_exempt(views.FileTransferRequestView.as_view()), name="file-request"),
    path("files/pending/", csrf_exempt(views.PendingFileTransfersView.as_view()), name="files-pending"),
    path("files/<uuid:transfer_id>/upload/", csrf_exempt(views.FileTransferUploadView.as_view()), name="file-upload"),
    path("files/<uuid:transfer_id>/", csrf_exempt(views.FileTransferDetailView.as_view()), name="file-detail"),
    path("files/<uuid:transfer_id>/download/", csrf_exempt(views.file_download_view), name="file-download"),
    path("conversations/<uuid:conv_id>/files/", csrf_exempt(views.ConversationFilesView.as_view()), name="conversation-files"),
    path("conversations/<uuid:conv_id>/messages/", csrf_exempt(views.SendMessageView.as_view()), name="send-message"),
    path("conversations/<uuid:conv_id>/tasks/", csrf_exempt(views.TaskListByConversationView.as_view()), name="task-list-by-conv"),
    path("tasks/<uuid:task_id>/", csrf_exempt(views.TaskDetailView.as_view()), name="task-detail"),
    path("tasks/<uuid:task_id>/events/", csrf_exempt(views.TaskEventsAppendView.as_view()), name="task-events-append"),
    path("tasks/<uuid:task_id>/stream/", csrf_exempt(views.task_stream_view), name="task-stream"),
]
