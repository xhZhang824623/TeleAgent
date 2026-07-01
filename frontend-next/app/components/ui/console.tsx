import React from "react";

/* =========================================================================
   StatusBadge — 把任务状态映射成中文标签 + 配色，queued/running 带脉冲点。
   这是渲染任务状态的规范方式（任意标签用 core/Badge）。
   ========================================================================= */
type TaskStatus = "queued" | "running" | "success" | "failed" | "cancelled" | "timeout" | "idle";
const STATUS: Record<TaskStatus, { label: string; bg: string; fg: string; active?: boolean }> = {
  queued: { label: "排队中", bg: "var(--ta-queued-bg)", fg: "var(--ta-queued-fg)", active: true },
  running: { label: "执行中", bg: "var(--ta-running-bg)", fg: "var(--ta-running-fg)", active: true },
  success: { label: "已完成", bg: "var(--ta-success-bg)", fg: "var(--ta-success-fg)" },
  failed: { label: "失败", bg: "var(--ta-failed-bg)", fg: "var(--ta-failed-fg)" },
  cancelled: { label: "已取消", bg: "var(--ta-neutral-bg)", fg: "var(--ta-neutral-fg)" },
  timeout: { label: "超时", bg: "var(--ta-neutral-bg)", fg: "var(--ta-neutral-fg)" },
  idle: { label: "空闲", bg: "var(--ta-line-faint)", fg: "var(--ta-muted)" },
};

export interface StatusBadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  status?: string;
  label?: string;
}
export function StatusBadge({ status = "idle", label, style, ...rest }: StatusBadgeProps) {
  const s = STATUS[(status as TaskStatus)] || STATUS.idle;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "0.5rem",
        padding: "0.25rem 0.75rem",
        borderRadius: "var(--radius-pill)",
        background: s.bg,
        color: s.fg,
        fontSize: "var(--text-2xs)",
        fontWeight: "var(--weight-medium)",
        textTransform: "uppercase",
        letterSpacing: "var(--tracking-chip)",
        lineHeight: 1.4,
        ...style,
      }}
      {...rest}
    >
      {s.active && (
        <span className="ta-animate-pulse" style={{ width: 8, height: 8, borderRadius: "9999px", background: "currentColor", opacity: 0.7 }} />
      )}
      {label || s.label}
    </span>
  );
}

/* =========================================================================
   ChatBubble — 会话气泡。role="user" 暗色实底/右下尾角；role="agent" 白底
   描边/左下尾角。header 渲染微标题行，children 为正文。
   ========================================================================= */
export interface ChatBubbleProps extends React.HTMLAttributes<HTMLDivElement> {
  role?: "user" | "agent";
  header?: React.ReactNode;
}
export function ChatBubble({ role = "agent", header, children, style, ...rest }: ChatBubbleProps) {
  const isUser = role === "user";
  return (
    <div style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start" }}>
      <div
        className="ta-animate-enter"
        style={{
          maxWidth: isUser ? "78%" : "84%",
          width: isUser ? undefined : "100%",
          padding: "0.75rem 1rem",
          borderRadius: "var(--radius-md)",
          borderBottomRightRadius: isUser ? "0.375rem" : "var(--radius-md)",
          borderBottomLeftRadius: isUser ? "var(--radius-md)" : "0.375rem",
          background: isUser ? "var(--ta-ink)" : "var(--ta-panel)",
          color: isUser ? "#fff" : "var(--ta-ink-soft)",
          border: isUser ? "1px solid transparent" : "1px solid var(--ta-line)",
          boxShadow: "var(--shadow-sm)",
          ...style,
        }}
        {...rest}
      >
        {header && (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              gap: "0.5rem",
              justifyContent: isUser ? "space-between" : "flex-start",
              marginBottom: "0.5rem",
              fontSize: "var(--text-3xs)",
              textTransform: "uppercase",
              letterSpacing: "0.18em",
              color: isUser ? "rgba(255,255,255,0.58)" : "var(--ta-muted)",
            }}
          >
            {header}
          </div>
        )}
        <div style={{ fontSize: "var(--text-sm)", lineHeight: "var(--leading-normal)", whiteSpace: isUser ? "pre-wrap" : "normal", wordBreak: "break-word" }}>
          {children}
        </div>
      </div>
    </div>
  );
}

/* =========================================================================
   EventLine — 实时事件流中的一行。四种 tone：assistant/result 为 primary
   行（柔 indigo / 灰，sans）；tool/system 为 detail 行（mono）。
   ========================================================================= */
type EventTone = "assistant" | "result" | "tool" | "system";
const TONES: Record<EventTone, { label: string; bg: string; fg: string; mono: boolean }> = {
  assistant: { label: "回复", bg: "var(--ta-accent-soft)", fg: "var(--ta-accent-strong)", mono: false },
  result: { label: "结果", bg: "var(--ta-surface-muted)", fg: "var(--ta-ink-soft)", mono: false },
  tool: { label: "工具调用", bg: "var(--ta-accent-soft)", fg: "var(--ta-accent)", mono: true },
  system: { label: "系统", bg: "var(--ta-surface-muted)", fg: "var(--ta-muted)", mono: true },
};
export interface EventLineProps extends React.HTMLAttributes<HTMLDivElement> {
  tone?: EventTone;
  label?: React.ReactNode;
}
export function EventLine({ tone = "assistant", label, children, style, ...rest }: EventLineProps) {
  const t = TONES[tone] || TONES.assistant;
  return (
    <div
      style={{
        padding: t.mono ? "0.5rem 0.75rem" : "0.75rem 1rem",
        borderRadius: "var(--radius-md)",
        background: t.bg,
        color: t.fg,
        fontSize: "var(--text-sm)",
        lineHeight: "var(--leading-normal)",
        fontFamily: t.mono ? "var(--font-mono)" : "inherit",
        ...style,
      }}
      {...rest}
    >
      <div
        style={{
          marginBottom: "0.25rem",
          fontSize: "var(--text-3xs)",
          textTransform: "uppercase",
          letterSpacing: "0.18em",
          opacity: 0.7,
          fontFamily: "var(--font-sans)",
        }}
      >
        {label || t.label}
      </div>
      <div style={{ whiteSpace: t.mono ? "pre-wrap" : "normal", wordBreak: "break-word" }}>{children}</div>
    </div>
  );
}
