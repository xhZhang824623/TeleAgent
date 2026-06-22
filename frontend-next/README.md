# TeleAgent 前端（Next.js）

- **首页**：`/`
- **Broker**：`/broker`（会话列表、新建会话选 Agent、发消息、轮询任务状态）
- 适配**手机与电脑**：小屏下会话列表为抽屉，大屏为左侧固定栏

## 本地开发

```bash
npm install
npm run dev
```

访问 http://localhost:3000，API 需通过 Nginx 反向到 Django 或设置 `NEXT_PUBLIC_API` 代理。

## 生产构建（Docker）

由项目根目录 `docker compose build` 一并构建，镜像名 `teleagent-next:latest`。Nginx 将 `/` 代理到本容器 3000 端口。
