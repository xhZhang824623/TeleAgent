# Broker 架构与整体理解

## 1. 数据流与角色

```
Web 用户 ←→ Django 后台 ←→ PC Broker（本机） ←→ Cursor Agent
```

- **Django**：会话、消息、任务持久化；REST API 供 Web 与 Broker 使用。
- **Web 页面**：用户在此创建会话、发消息、选择「用哪台 PC 的 Agent」；结果通过轮询或后续 SSE 展示。
- **PC Broker**：运行在每台 PC 上，一头接 Django（拉任务、回写状态/事件），一头接本机 Agent（执行任务）。**不存会话数据**，只做数据中转。
- **Agent**：本机 Cursor agent CLI，由 Broker 按任务拉起并上报结果。

---

## 2. Web 用户与 PC Agent：多对多

- **多对多**：多个 Web 用户可选用多台 PC 上的 Agent；同一台 PC Agent 可被多个会话/用户选用（按会话维度选择「用哪台」）。
- **创建会话时**：在 Web 端选择「使用哪台 PC 的 Agent」（从已注册的 Agent 客户端列表选），该会话后续任务只会下发给所选 PC 的 Broker。
- **后续扩展**：可引入「用户 ↔ Agent 访问控制」——例如仅允许某用户访问「我的 PC」或「他的 PC」等指定 Agent，在后台通过用户/权限与 `assigned_client` 绑定即可。

---

## 3. 本地 Qt 程序两种模式

| 模式 | 含义 | 数据与交互 |
|------|------|------------|
| **本地 (Qt)** | 纯本地对话 | 会话仅存本机内存；用户在 Qt 里与 Agent 对话，不经过 Django。 |
| **接入云端** | 本机仅作 Broker | 不存会话；Qt 只做「Django ↔ Agent」中转，显示状态与「打开 Broker 网页」入口；**用户到 Web 端选择本机并对话**。 |

- **接入云端时**：Qt 窗口仅为 Broker 状态窗（等待任务 / 执行中 / 完成 + 打开网页）。

---

## 4. Qt 作为云端能力的原型与迁移

- **本地 Qt 对话**可作为「与 Agent 强相关交互」的**原型与验证环境**：流式输出、tool 展示、多轮、session 恢复等先在 Qt 上实现并验证。
- **验证通过后**：同一套交互语义与协议迁移到云端（Django + Worker + API），Web 前端再实现对等能力，最终使 **Web 页面具备与本地 Qt 一致的体验**。

---

## 5. 组件对应

| 组件 | 说明 |
|------|------|
| **frontend-next（Next.js /broker）** | Web Broker 页：会话列表、新建会话时选择 Agent 客户端、发消息、轮询任务状态；支持手机与电脑适配。 |
| **Django OnlineBroker** | 会话/消息/任务/AgentClient 模型；REST API（含 clients、queued tasks、PATCH/POST 任务状态与事件）。 |
| **LocalBroker Qt** | 二选一：本地模式 = 完整 Qt 对话（内存）；云端模式 = CloudBrokerWindow（仅状态 + 打开网页），后台用 broker_worker 轮询并执行任务。 |

---

## 6. 后续可做：用户 ↔ Agent 访问控制

- 当前：Web 新建会话时可选「任意」或「某台已注册 Agent」；任务按 `assigned_client` 下发给对应 Broker。
- 后续可做：为「用户」或「角色」配置「可用的 Agent 列表」，仅允许用户选择其有权限的 PC Agent（如「我的 PC」「他的 PC」），在 API 与前端过滤可选客户端列表即可。
