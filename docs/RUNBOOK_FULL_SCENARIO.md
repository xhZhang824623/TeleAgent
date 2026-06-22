# 完整模拟应用场景 · 运行说明

按下面顺序操作，可完整跑通：**Web 建会话/发消息 → 本机执行 Agent → 结果回写 Django → Web 看到结果**。

---

## 1. 启动后端（Docker）

在项目根目录：

```bash
cd /home/nealzhang/dev/teleAgent
./start-dev.sh
```

或：

```bash
cp -f env.dev .env
docker compose up -d --build
```

确认服务正常：

```bash
./status-check.sh
curl -sk https://localhost:9443/health
# 应返回 {"status":"ok"}
```

---

## 2. 在 Web 上创建会话并发消息

1. 浏览器打开：**https://localhost:9443/**（自签名证书点「继续访问」）
2. 点击 **Broker** 进入 `/broker`
3. 点击 **「新建会话」**，填写：
   - **工作目录 (cwd)**：本机一个真实目录，例如 `/home/nealzhang/dev/teleAgent`（Agent 会在这个目录下执行）
   - **标题**：随意
   - **使用哪台 PC 的 Agent**：下拉选择「任意」或某台已注册的客户端（需先有 PC 以 Qt 选「接入云端」注册）
4. 在右侧输入框输入一条 **prompt**（例如「列出当前目录下的文件」），点击 **发送**

此时任务会在 Django 里处于 **queued** 状态；若选了某台 PC，仅该台 Broker 会拉取并执行。

---

## 3. 本机运行 Qt 云端模式（执行 Agent 并回写 Django）

在**本机**运行 LocalBroker Qt，选择「接入云端」并配置同一 API 地址（如 https://localhost:9443）与本机客户端名称：

```bash
cd /home/nealzhang/dev/teleAgent/LocalBroker
pip install PyQt5
python broker_qt.py
```

- 选 **接入云端**，填写 API 地址与客户端名称后，会弹出状态窗并开始轮询 Django 的 queued 任务
- 本机用 **Cursor agent**（需已安装且 `agent` 在 PATH）执行任务，状态与结果回写 Django
- Web 上可看到任务从 **queued → running → success/failed**；可点击「打开 Broker 网页」在浏览器中操作

保持该 Qt 窗口运行，本机即作为该客户端执行任务。

---

## 4. 可选：用 LocalBroker 桌面端（Qt）的本地模式

若不需要接入云端，可运行 Qt 并选「本地 (Qt)」做纯本机对话；运行方式同上，选模式：

```bash
cd /home/nealzhang/dev/teleAgent/LocalBroker
pip install PyQt5
python broker_qt.py
```

- **本地 (Qt)**：纯本地对话，会话存内存，在 Qt 里选目录、发 prompt，Agent 本机执行；用于原型验证或离线使用。
- **接入云端**：本机仅作 Broker（数据中转）。配置云端 API 地址与本机客户端名称后，会弹出状态窗（等待任务 / 执行中），并提供「打开 Broker 网页」；**对话在 Web 端进行**，选择本机后任务由此 PC 执行。

---

## 5. 可选：Admin 与 API

- **Django Admin**：https://localhost:9443/admin/  
  首次使用需创建超级用户：
  ```bash
  docker compose exec TeleAgent python manage.py createsuperuser
  ```
- **Broker API**：例如
  ```bash
  curl -sk https://localhost:9443/api/broker/conversations/
  ```

---

## 流程小结

| 步骤 | 动作 | 说明 |
|------|------|------|
| 1 | 运行 `./start-dev.sh` | 起 Nginx + Django + Postgres |
| 2 | 浏览器打开 Broker 页，新建会话、发消息 | 任务在 Django 中为 queued |
| 3 | 本机运行 `python broker_qt.py` 并选「接入云端」 | 拉取 queued 任务，本机跑 Agent，结果回写 Django |
| 4 | Web 上查看会话/任务 | 看到 running → success/failed 与结果 |

这样即完成「Web 建任务 → 本机执行 → 结果回写」的完整模拟。
