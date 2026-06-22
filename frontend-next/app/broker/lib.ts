// 纯常量 / 类型 / 无状态工具函数 —— 从 page.tsx 抽出以缩减主组件体积（无行为变化）。

export const API_BROKER = "/api/broker";
export const API_AUTH = "/api/auth";

export const AGENT_OPTIONS = [
  { value: "codex", label: "Codex" },
  { value: "claude_code", label: "Claude Code" },
  { value: "cursor_agent", label: "Cursor Agent" },
] as const;

export type AgentType = (typeof AGENT_OPTIONS)[number]["value"];
export type Client = { id: string; name: string; hostname?: string; supported_agents?: AgentType[] };
export type Conversation = {
  id: string;
  title?: string;
  cwd: string;
  assigned_client_id?: string;
  agent_type: AgentType;
  updated_at?: string;
  message_count?: number;
  last_result?: string;
  title_custom?: boolean;
  force?: boolean;
};
export type Task = { id: string; status: string; started_at?: string; result_text?: string; agent_type?: AgentType };
export type Message = { id: string; prompt: string; task?: Task };
export type LiveLine = { id: string; tone: "assistant" | "tool" | "system" | "result"; text: string };
export type LiveTask = { status: string; lines: LiveLine[]; resultText?: string };

export const TOKEN_KEY = "broker_token";
export const EMAIL_KEY = "broker_email";

// 声明式 Agent 参数 schema（前端渲染侧）。每个 agent 一组下拉控件；
// 选中的值存进 conversation.options(JSON)，由 LocalBroker 映射成 CLI flag。
// 加新参数 = 这里加一条 + LocalBroker 的 claude_option_args 加一条。
export type AgentOption = {
  key: string;
  label: string;
  hint?: string;
  default: string;
  choices: { value: string; label: string }[];
};

export const AGENT_OPTION_SCHEMA: Record<string, AgentOption[]> = {
  claude_code: [
    {
      key: "permission_mode",
      label: "权限模式",
      hint: "计划模式只产出方案不执行；全放开会自动改文件/跑命令",
      default: "default",
      choices: [
        { value: "default", label: "默认（逐步确认）" },
        { value: "plan", label: "计划模式（只规划，不执行）" },
        { value: "acceptEdits", label: "自动接受编辑" },
        { value: "bypassPermissions", label: "全放开（跳过所有确认）" },
      ],
    },
    {
      key: "model",
      label: "模型",
      default: "",
      choices: [
        { value: "", label: "默认" },
        { value: "opus", label: "Opus（最强）" },
        { value: "sonnet", label: "Sonnet（均衡）" },
        { value: "haiku", label: "Haiku（最快）" },
      ],
    },
    {
      key: "effort",
      label: "思考强度",
      default: "",
      choices: [
        { value: "", label: "默认" },
        { value: "low", label: "低" },
        { value: "medium", label: "中" },
        { value: "high", label: "高" },
        { value: "xhigh", label: "极高" },
        { value: "max", label: "最大" },
      ],
    },
  ],
  codex: [],
  cursor_agent: [],
};

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function agentLabel(value?: string): string {
  return AGENT_OPTIONS.find((item) => item.value === value)?.label || value || "Unknown";
}

export function extractAssistantText(event: Record<string, unknown>): string {
  const message = event.message as { content?: Array<{ text?: string }> } | undefined;
  const content = message?.content;
  if (!Array.isArray(content)) return "";
  return content
    .map((item) => item?.text || "")
    .filter(Boolean)
    .join("");
}

export function formatToolLine(toolCall: Record<string, unknown>, subtype?: string): string {
  const shellToolCall = toolCall.shellToolCall as { args?: { command?: string }; result?: { success?: { exitCode?: number }; failure?: { exitCode?: number } } } | undefined;
  if (shellToolCall) {
    if (subtype === "started") return `shell: ${shellToolCall.args?.command || ""}`;
    if (subtype === "completed") {
      const code = shellToolCall.result?.success?.exitCode ?? shellToolCall.result?.failure?.exitCode;
      return `shell completed${code !== undefined ? ` (exit ${code})` : ""}`;
    }
  }
  const readToolCall = toolCall.readToolCall as { args?: { path?: string } } | undefined;
  if (readToolCall) return `read: ${readToolCall.args?.path || ""}`;
  const writeToolCall = toolCall.writeToolCall as { args?: { path?: string } } | undefined;
  if (writeToolCall) return `write: ${writeToolCall.args?.path || ""}`;
  return "tool call";
}

export function findActiveTaskId(messages?: Message[] | null): string | null {
  return (
    messages
      ?.map((message) => message.task)
      .filter((task): task is Task => Boolean(task))
      .reverse()
      .find((task) => task.status === "queued" || task.status === "running")?.id || null
  );
}

export function statusLabel(status?: string): string {
  switch (status) {
    case "queued":
      return "排队中";
    case "running":
      return "执行中";
    case "success":
      return "已完成";
    case "failed":
      return "失败";
    case "cancelled":
      return "已取消";
    case "timeout":
      return "超时";
    default:
      return status || "未知";
  }
}

export function statusTone(status?: string): string {
  switch (status) {
    case "queued":
      return "bg-amber-100 text-amber-800";
    case "running":
      return "bg-sky-100 text-sky-800";
    case "success":
      return "bg-emerald-100 text-emerald-800";
    case "failed":
      return "bg-rose-100 text-rose-800";
    case "cancelled":
    case "timeout":
      return "bg-stone-200 text-stone-700";
    default:
      return "bg-stone-200 text-stone-700";
  }
}

export function shortPathLabel(path?: string): string {
  if (!path) return "未命名目录";
  const parts = path.split("/").filter(Boolean);
  return parts[parts.length - 1] || path;
}

export function lineToneLabel(tone: LiveLine["tone"]): string {
  switch (tone) {
    case "assistant":
      return "回复";
    case "tool":
      return "工具调用";
    case "result":
      return "结果";
    case "system":
      return "系统";
    default:
      return "事件";
  }
}

export function formatRelativeTime(value?: string): string {
  if (!value) return "刚创建";
  const ts = new Date(value).getTime();
  if (!Number.isFinite(ts)) return "最近更新";
  const diff = Date.now() - ts;
  if (diff < 60_000) return "刚刚更新";
  if (diff < 3_600_000) return `${Math.max(1, Math.floor(diff / 60_000))} 分钟前`;
  if (diff < 86_400_000) return `${Math.max(1, Math.floor(diff / 3_600_000))} 小时前`;
  return `${Math.max(1, Math.floor(diff / 86_400_000))} 天前`;
}

export function conversationStatusDot(status?: string): string {
  switch (status) {
    case "queued":
      return "bg-amber-500";
    case "running":
      return "bg-sky-500";
    case "success":
      return "bg-emerald-500";
    case "failed":
      return "bg-rose-500";
    default:
      return "bg-stone-300";
  }
}
