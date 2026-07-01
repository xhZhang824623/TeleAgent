"use client";

import { Button } from "../components/ui";
import { permissionSummary, formatTime } from "./lib";
import type { PermissionCard as PermissionCardType } from "./lib";

// 工具名 → 自然语言动作短语（卡片正文用蓝色高亮）。未知工具回退到「使用 <名>」。
const TOOL_VERBS: Record<string, string> = {
  Bash: "运行命令",
  Shell: "运行命令",
  Write: "写入文件",
  Edit: "编辑文件",
  MultiEdit: "编辑文件",
  Read: "读取文件",
  WebFetch: "访问网页",
  WebSearch: "联网搜索",
};

function ShieldIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  );
}

// 紧凑行：用于顶部「权限审批」折叠面板里的历史记录列表。待审项保留快捷批准/拒绝按钮。
export function PermissionRow({
  card,
  onAnswer,
}: {
  card: PermissionCardType;
  onAnswer?: (decision: "allow" | "deny", remember: boolean) => void;
}) {
  const action = TOOL_VERBS[card.tool_name] || `使用 ${card.tool_name}`;
  const target = permissionSummary(card.tool_input);
  const pending = card.status === "pending";
  const allowed = card.status === "allowed";
  const badge = pending
    ? { dot: "bg-accent", text: "text-accent", chip: "bg-accent-soft text-accent", label: "待批准" }
    : allowed
      ? { dot: "bg-success-dot", text: "text-success-fg", chip: "bg-success-bg text-success-fg", label: "已批准" }
      : { dot: "bg-failed-dot", text: "text-failed-fg", chip: "bg-failed-bg text-failed-fg", label: "已拒绝" };

  return (
    <div
      className={`flex min-w-0 items-center gap-2.5 rounded-field border px-2.5 py-2 transition ${
        pending ? "border-accent-border bg-white" : "border-line bg-white hover:bg-surface"
      }`}
    >
      <span className={`inline-flex shrink-0 items-center gap-1 rounded-pill px-2 py-0.5 text-[10px] font-semibold ${badge.chip}`}>
        <span className={`h-1.5 w-1.5 rounded-pill ${badge.dot}`} aria-hidden />
        {badge.label}
      </span>
      <span className="shrink-0 text-xs font-medium text-ink">{action}</span>
      {target && (
        <code className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted" title={target}>
          {target}
        </code>
      )}
      {card.created_at && (
        <span className="ml-auto shrink-0 text-[11px] text-faint">{formatTime(card.created_at)}</span>
      )}
      {pending && onAnswer && (
        <span className="flex shrink-0 items-center gap-1.5">
          <Button size="sm" variant="accent" onClick={() => onAnswer("allow", false)}>批准</Button>
          <Button size="sm" variant="secondary" onClick={() => onAnswer("deny", false)}>拒绝</Button>
        </span>
      )}
    </div>
  );
}

// 审批卡片：完整卡片，用于「需要批准」的全局弹框。待审显示按钮；已批准/已拒绝则保留为记录。
export function PermissionCard({
  card,
  onAnswer,
}: {
  card: PermissionCardType;
  onAnswer: (decision: "allow" | "deny", remember: boolean) => void;
}) {
  const action = TOOL_VERBS[card.tool_name] || `使用 ${card.tool_name}`;
  const target = permissionSummary(card.tool_input);
  const pending = card.status === "pending";
  const allowed = card.status === "allowed";

  return (
    <div className="flex gap-3">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-pill bg-accent text-white">
        <ShieldIcon />
      </div>
      <div className="min-w-0 max-w-xl flex-1 rounded-card border border-line bg-white p-4 shadow-soft">
        <div className="flex items-baseline gap-2 text-sm font-semibold text-ink">
          <span>有一个待批准的请求</span>
          {!pending && (
            <span className={`text-xs font-medium ${allowed ? "text-success-fg" : "text-failed-fg"}`}>
              · {allowed ? "已批准" : "已拒绝"}
            </span>
          )}
          {card.created_at && (
            <span className="ml-auto shrink-0 text-[11px] font-normal text-faint">{formatTime(card.created_at)}</span>
          )}
        </div>
        <div className="mt-1 text-sm leading-6 text-ink-soft">
          Agent 请求权限来 <span className="font-medium text-accent">{action}</span>
        </div>
        {target && (
          <pre className="mt-2 max-h-32 overflow-auto rounded-field bg-surface px-3 py-2 font-mono text-xs leading-5 text-ink-soft whitespace-pre-wrap break-all">
            {target}
          </pre>
        )}

        {pending ? (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button size="sm" variant="accent" onClick={() => onAnswer("allow", false)}>
              批准
            </Button>
            <Button size="sm" variant="secondary" onClick={() => onAnswer("allow", true)}>
              一直允许（本会话）
            </Button>
            <Button size="sm" variant="secondary" onClick={() => onAnswer("deny", false)}>
              拒绝
            </Button>
          </div>
        ) : (
          <div className="mt-2 text-xs text-muted">
            {allowed ? "✓ 你已批准此操作" : "✕ 你已拒绝此操作"}
          </div>
        )}
      </div>
    </div>
  );
}
