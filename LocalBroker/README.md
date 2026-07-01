# TeleAgent LocalBroker — 本地端使用手册

LocalBroker 运行在你的 PC 上,作为「Broker」:一头连云端 Django 后端,一头在本机拉起
CLI Agent(Claude Code / Cursor 等)执行任务,并把结果/文件回传云端。Web 端在浏览器里
选中这台机器发起对话,实际的命令/改文件都发生在这台 PC 上。

有两种运行形态,**共用同一份配置**:

| 形态 | 入口 | 特点 |
|---|---|---|
| **GUI 版** | `broker_qt.py` | 有窗口,可交互登录、看状态、切审批开关。适合调试/首次配置。 |
| **服务版(headless)** | `broker_service.py` | 无窗口,`systemd --user` 托管,开机自启、崩溃自愈。**避免前台程序被误关。** |

> ⚠️ **两者互斥,不能同时运行**(否则会重复拉取/执行任务)。二者共用一把单实例锁:
> 若服务正在跑,再开 GUI 会弹窗提示并退出;反之亦然。切换前先停掉另一个。
> 日志也共用同一个文件,每次启动会打一行「启动模式」标明是 GUI 还是服务。

---

## 1. 前置条件

- **Python 3.9+**,并安装 **PyQt5**(唯一第三方依赖;HTTP 走标准库,无需 requests):
  ```bash
  pip install PyQt5          # 或 sudo apt install python3-pyqt5
  ```
- **Agent CLI 已装好并在 PATH 里**:如 `claude`(Claude Code)、`cursor-agent`。
  用 `which claude` 能打印路径即可。Broker 靠 PATH 发现它们。
- **云端后端可访问**,且在后端 Django Admin 里为这台 PC 建好一条
  **「Broker 客户端凭证」(BrokerClientCredential)**,拿到它的 **ID(UUID)** 和 **Secret Key**。

> ⚠️ **连接地址用 HTTP**(如 `http://<服务器IP>:9020`)。
> 若后端是自签名 HTTPS(9443),Broker 只对 `localhost` 跳过证书校验,连公网自签名会失败。
> 上正式证书后才建议用 HTTPS。

---

## 2. 配置文件(GUI 与服务共用)

所有配置存在一份 INI 文件里,GUI 和服务**都默认读它**:

```
~/.config/TeleAgent/Broker.conf
```

```ini
[cloud]
api_base=http://<服务器IP>:9020        # 云端地址
client_id=<客户端凭证UUID>              # Admin 里那条凭证的 ID
client_secret=<Secret Key>              # 对应 Secret(token 失效时用它自动重登)
client_name=我的笔记本                   # 这台机器在 Web 端显示的名字
token=<自动获取,无需手填>
email=<自动获取>
interactive_permissions=true            # 交互式工具审批开关
```

- **GUI 版**:启动时读它;登录对话框、审批开关会**动态写回**它。
- **服务版**:启动(`run`)时读它;用 `login` 子命令可从命令行写它。
- 可手改(比如换 `api_base`),但**推荐用登录流程**,因为它会顺带验证并拉取新 `token`。
- 改动对**正在运行的服务不热生效**,需 `systemctl --user restart teleagent-broker`。

Secret/token 是明文存储,注意文件权限(默认仅当前用户可读)。

---

## 3. GUI 版:启动与使用

```bash
cd LocalBroker
python3 broker_qt.py
```

流程:填云端地址 → 用「客户端 ID + Secret」登录 → 进入状态窗口。窗口里可:
- 「打开 Broker 网页」跳到 Web 端;
- 勾选**交互式工具审批**(Agent 的危险操作会在网页弹卡片让你确认)。

首次用 GUI 登录后,凭据就写进了上面的配置文件,之后服务版可直接复用。

---

## 4. 服务版(headless):命令行

三个子命令:

```bash
cd LocalBroker

# 登录并保存凭据(写入配置文件;会验证登录、拉取 token)
python3 broker_service.py login \
  --api-base http://<服务器IP>:9020 \
  --client-id <UUID> --secret <Secret Key> \
  [--name 我的笔记本]

# 查看当前已保存的凭据概要(不泄露明文)
python3 broker_service.py status

# 前台启动(调试用,Ctrl+C 停)
python3 broker_service.py run
```

看到 `Broker 服务已启动` + `[idle] 等待任务…` 即连通成功。

特性:
- **单实例锁**:防止 GUI 版和服务版同时跑导致重复执行任务。
- **token 自动续期**:token 过期时用 `client_id + secret` 自动重登,无需人工。
- 只有凭据被**吊销/错误**时才会停并提示重登。

---

## 5. 装成开机自启服务(systemd --user)

前台 `run` 验证通了之后,用现成的单元文件 `teleagent-broker.service` 托管(**无需任何脚本**):

```bash
cd LocalBroker
# 1) 按你的机器改单元里的两行(python 路径 + PATH),然后拷到 systemd 用户目录
cp teleagent-broker.service ~/.config/systemd/user/
# 2) 启用并启动
systemctl --user daemon-reload
systemctl --user enable --now teleagent-broker
# 3) 让它在你「未登录」时也运行
sudo loginctl enable-linger $USER
```

单元文件里有三处需按需确认(都有注释):
- **`ExecStart` 的 python 路径** —— 必须是**装了 PyQt5 的解释器**(本机是 miniconda;系统 python 则 `/usr/bin/python3`)。
- **`Environment=PATH=`** —— 要含 Agent CLI(claude 默认在 `~/.local/bin`);用 nvm 装的 codex 等再追加 node bin。
- **`Environment=BROKER_INSECURE_SSL=1`** —— 后端自签名 HTTPS 时保留,正式证书后删除。

> ⚠️ 为什么直接写 python 绝对路径、不用 `bash -lc`?因为 systemd 非交互环境下登录 shell 的 `python3`
> 可能解析成系统 python(没装 PyQt5),导致 `ModuleNotFoundError`。直接指定更可靠。

`Restart=on-failure` 会在崩溃/掉线时自动拉起。

常用运维命令:
```bash
systemctl --user status  teleagent-broker      # 状态
journalctl  --user -u    teleagent-broker -f   # 实时日志
systemctl --user restart teleagent-broker      # 重启(改配置后)
systemctl --user stop    teleagent-broker      # 停止
# 卸载:
systemctl --user disable --now teleagent-broker
rm ~/.config/systemd/user/teleagent-broker.service
systemctl --user daemon-reload
```

**日志有两处**(内容相同):
- **落盘文件**(推荐,可跨天回溯):`~/.local/state/teleagent/broker.log`,10MB 滚动、留 5 份。
  ```bash
  tail -f ~/.local/state/teleagent/broker.log
  ```
- **journald**(服务运行时):`journalctl --user -u teleagent-broker -f`

前台 `run` 时,日志同时打到终端和上面那个文件。

---

## 6. 可选环境变量(调优,一般不用改)

| 变量 | 默认 | 说明 |
|---|---|---|
| `BROKER_API_BASE` | `http://localhost:9020` | 配置文件没有 api_base 时的兜底地址 |
| `BROKER_INSECURE_SSL` | 关 | 设 `1` 则接受**自签名 HTTPS**证书(自建服务器用自签证书时需要;数据仍加密,只是不校验证书)。服务里在单元文件加 `Environment=BROKER_INSECURE_SSL=1` |
| `BROKER_LOG_LEVEL` | `INFO` | 日志级别(`DEBUG`/`INFO`/`WARNING`) |
| `BROKER_LOG_FILE` | `~/.local/state/teleagent/broker.log` | 落盘日志路径(设为空或 `-` 关闭落盘) |
| `BROKER_LOG_MAX_BYTES` | `10485760`(10MB) | 单个日志文件大小上限,超出滚动 |
| `BROKER_LOG_BACKUP_COUNT` | `5` | 保留的历史日志份数 |
| `BROKER_POLL_INTERVAL` | `2.0` | 最小轮询间隔(秒),有任务时用它 |
| `BROKER_POLL_MAX_INTERVAL` | `15.0` | 空闲时指数退避到的上限 |
| `BROKER_POLL_ERROR_MAX` | `30.0` | 出错时退避上限 |
| `BROKER_FILE_MAX_BYTES` | 后端约定 | 文件传输单文件大小上限 |
| `BROKER_PERMISSION_HOOK_MATCHER` | 内置正则 | 触发交互审批的工具名匹配 |

服务版要设这些,写进 systemd 单元的 `Environment=` 或 `EnvironmentFile=`。

---

## 7. 常见问题排查

| 现象 | 原因 / 处理 |
|---|---|
| `run` 一直报连接失败并重试 | 后端不可达,或 `api_base` 指错(如还是 `localhost`)。用 `status` 看地址,`login` 改。 |
| 连 `https://...:9443` 报 `CERTIFICATE_VERIFY_FAILED / self-signed` | 后端用的是自签名证书。单元文件里保留 `Environment=BROKER_INSECURE_SSL=1`(前台调试则 `BROKER_INSECURE_SSL=1 python3 broker_service.py run`)。或给后端上正式证书。 |
| 服务 `ModuleNotFoundError: No module named 'PyQt5'` | 单元里 `ExecStart` 的 python 不是装了 PyQt5 的那个。改成正确的解释器绝对路径(如 `%h/miniconda3/bin/python3`)。 |
| `认证失效/凭据被吊销` | 后端把该客户端凭证删了或 Secret 不对。重新 `login`。 |
| Web 端选不到这台机器 | 服务没连上、或 PATH 里没有 agent CLI。看日志 `journalctl --user -u teleagent-broker -f`。 |
| Agent 跑起来报找不到命令 | 服务的 PATH 不含 CLI 路径。重装服务(会重新烤 PATH),或在单元里补 `Environment=PATH=...`。 |
| 提示「已有另一个实例」 | GUI 版和服务版不能同时跑。关掉其一。 |

---

## 8. 一句话上手

```bash
# 1) 后端 Admin 建客户端凭证 → 拿 UUID + Secret
# 2) 登录并前台验证
python3 broker_service.py login --api-base http://<IP>:9020 --client-id <UUID> --secret <KEY>
python3 broker_service.py run          # 看到「等待任务…」= 通了,Ctrl+C
# 3) 托管为开机自启服务(先按机器改单元里的 python 路径 / PATH)
cp teleagent-broker.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now teleagent-broker
sudo loginctl enable-linger $USER
```
