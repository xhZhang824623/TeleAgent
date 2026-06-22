# Broker API（Web / PC Broker ↔ Django 对接）

Base URL: `/api/broker/`（相对当前站点，如 `https://localhost:9443/api/broker/`）

- **整体架构**（Web 用户与 PC Agent 多对多、Broker 仅做数据中转）：见 [BROKER_ARCHITECTURE.md](BROKER_ARCHITECTURE.md)。

---

## Agent 客户端（多对多：Web 用户 ↔ 多台 PC Agent）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `clients/` | 已注册的 Agent 客户端列表（Web 新建会话时选择「用哪台」） |
| POST | `clients/` | 注册本机为客户端。Body: `{ "name": "Neal的笔记本", "hostname?": "" }` → `{ "id", "name", ... }` |
| PATCH | `clients/<client_id>/` | 心跳，更新 `last_seen` |

- 创建会话时可选 `client_id`，则该会话任务仅下发给该 PC 的 Broker；不选则任务未分配，任意 Broker 可拉取。

---

## 任务拉取（PC Broker 用）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `tasks/queued/?client_id=<uuid>` | 拉取可执行任务。不传 `client_id` 则返回所有 queued；传则仅返回「未分配」或「分配给该 client」的任务。 |

---

## 会话（Conversation）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `conversations/` | 会话列表 |
| POST | `conversations/` | 创建会话 Body: `{ "cwd": "/path", "title": "可选", "client_id": "可选 UUID" }` |
| GET | `conversations/<conv_id>/` | 会话详情（含 messages、assigned_client_id） |
| DELETE | `conversations/<conv_id>/` | 删除会话 |

---

## 消息与任务（Message / Task）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `conversations/<conv_id>/messages/` | 发消息，创建任务。Body: `{ "prompt", "force?", "output_format?", "stream_partial?", "timeout_sec?" }` → `{ "message_id", "task_id", "status" }` |
| GET | `conversations/<conv_id>/tasks/` | 该会话下任务列表 |
| GET | `tasks/<task_id>/` | 任务详情 |
| **PATCH** | **`tasks/<task_id>/`** | **LocalBroker 上报任务状态/结果**（见下） |
| **POST** | **`tasks/<task_id>/events/`** | **LocalBroker 追加流式事件**（见下） |
| GET | `tasks/<task_id>/stream/` | SSE 流（服务端按已存储 events 推送） |

---

## LocalBroker 专用：任务更新与事件上报

### PATCH `tasks/<task_id>/`

LocalBroker 在本地跑完 agent 后，用此接口把任务状态与结果写回 Django。

**Body（均为可选）：**

```json
{
  "status": "running",
  "started_at": "2025-03-04T12:00:00Z",
  "finished_at": "2025-03-04T12:05:00Z",
  "result_text": "agent 输出的 result 文本",
  "exit_code": 0,
  "events": [ { "type": "system", "subtype": "init", "session_id": "..." }, ... ],
  "raw_lines": [ "line1", "line2" ]
}
```

- **开始执行时**：可只传 `status: "running"`、`started_at`（ISO8601）。
- **结束时**：传 `status`（`success`|`failed`|`cancelled`|`timeout`）、`finished_at`、`result_text`、`exit_code`，以及完整 `events`、`raw_lines`（可选）。
- 若 `events` 中含有 `type: "system", subtype: "init"` 且带 `session_id`，服务端会回写对应会话的 `session_id`。

**Response:** `200 OK` + 当前任务详情（同 GET task 结构）。

---

### POST `tasks/<task_id>/events/`

LocalBroker 在 agent 运行过程中可多次调用，追加流式事件（Web 端轮询或 SSE 可看到增量）。

**Body:**

```json
{
  "events": [ { "type": "...", "subtype": "...", ... }, ... ]
}
```

- `events` 为数组，每次请求可追加多条。
- 若某条为 `type: "system", subtype: "init"` 且含 `session_id`，会更新对应会话的 `session_id`。

**Response:** `200 OK` + `{ "appended": 数量 }`。

---

## LocalBroker 推荐流程

1. **拉会话**：`GET conversations/` 或 `GET conversations/<id>/`。
2. **发消息**：`POST conversations/<conv_id>/messages/` → 拿到 `task_id`。
3. **本地执行 agent**：用返回的 task 的 `prompt`、`cwd`、`resume_session_id` 等在本机起 agent 子进程。
4. **开始**：`PATCH tasks/<task_id>/` 传 `status: "running"`, `started_at`。
5. **流式**（可选）：每收到 agent 的 JSON 行就 `POST tasks/<task_id>/events/` 追加。
6. **结束**：`PATCH tasks/<task_id>/` 传 `status`, `finished_at`, `result_text`, `exit_code`, `events`, `raw_lines`。

这样 Django/Web 侧数据与 LocalBroker 本地执行保持一致。
