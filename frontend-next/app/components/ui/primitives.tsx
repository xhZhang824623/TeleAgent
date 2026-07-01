import React from "react";

/* ---- Eyebrow — 宽字距大写微标题，TeleAgent 最具辨识度的标签 ---- */
export interface EyebrowProps extends React.HTMLAttributes<HTMLDivElement> {
  tone?: "default" | "faint";
}
export function Eyebrow({ tone = "default", children, style, ...rest }: EyebrowProps) {
  return (
    <div
      style={{
        fontSize: "var(--text-2xs)",
        textTransform: "uppercase",
        letterSpacing: "var(--tracking-eyebrow-wide)",
        color: tone === "faint" ? "var(--text-faint)" : "var(--text-muted)",
        fontWeight: "var(--weight-medium)",
        ...style,
      }}
      {...rest}
    >
      {children}
    </div>
  );
}

/* ---- Card — rounded-2xl 白卡，hairline 描边 + 极淡阴影 ---- */
export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  pad?: string;
}
export function Card({ pad = "var(--space-5)", children, style, ...rest }: CardProps) {
  return (
    <section
      style={{
        borderRadius: "var(--radius-lg)",
        border: "1px solid var(--ta-line)",
        background: "var(--ta-panel)",
        boxShadow: "var(--shadow-sm)",
        padding: pad,
        ...style,
      }}
      {...rest}
    >
      {children}
    </section>
  );
}

/* ---- Badge — 大写宽字距小药丸，tone 驱动；任务状态优先用 StatusBadge ---- */
type BadgeTone = "neutral" | "accent" | "outline" | "queued" | "running" | "success" | "failed";
const BADGE_TONES: Record<BadgeTone, { bg: string; fg: string; dot: string; border?: boolean }> = {
  neutral: { bg: "var(--ta-line-faint)", fg: "var(--ta-muted)", dot: "var(--ta-neutral-dot)" },
  accent: { bg: "var(--ta-accent-soft)", fg: "var(--ta-accent)", dot: "var(--ta-accent)" },
  outline: { bg: "var(--ta-panel)", fg: "var(--ta-muted)", dot: "var(--ta-faint)", border: true },
  queued: { bg: "var(--ta-queued-bg)", fg: "var(--ta-queued-fg)", dot: "var(--ta-queued-dot)" },
  running: { bg: "var(--ta-running-bg)", fg: "var(--ta-running-fg)", dot: "var(--ta-running-dot)" },
  success: { bg: "var(--ta-success-bg)", fg: "var(--ta-success-fg)", dot: "var(--ta-success-dot)" },
  failed: { bg: "var(--ta-failed-bg)", fg: "var(--ta-failed-fg)", dot: "var(--ta-failed-dot)" },
};
export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  dot?: boolean;
}
export function Badge({ tone = "neutral", dot = false, children, style, ...rest }: BadgeProps) {
  const t = BADGE_TONES[tone] || BADGE_TONES.neutral;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "0.4rem",
        padding: "0.25rem 0.75rem",
        borderRadius: "var(--radius-pill)",
        background: t.bg,
        color: t.fg,
        border: t.border ? "1px solid var(--ta-line)" : "1px solid transparent",
        fontSize: "var(--text-2xs)",
        fontWeight: "var(--weight-medium)",
        textTransform: "uppercase",
        letterSpacing: "var(--tracking-chip)",
        lineHeight: 1.4,
        ...style,
      }}
      {...rest}
    >
      {dot && <span style={{ width: 6, height: 6, borderRadius: "9999px", background: t.dot }} />}
      {children}
    </span>
  );
}

/* ---- Chip — 柔和混合大小写的计数/标签药丸（非大写），比 Badge 更安静 ---- */
type ChipTone = "muted" | "plain" | "accent";
const CHIP_TONES: Record<ChipTone, { bg: string; fg: string; border?: boolean }> = {
  muted: { bg: "var(--ta-line-faint)", fg: "var(--ta-muted)" },
  plain: { bg: "var(--ta-panel)", fg: "var(--ta-faint)", border: true },
  accent: { bg: "var(--ta-accent-soft)", fg: "var(--ta-accent)" },
};
export interface ChipProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: ChipTone;
}
export function Chip({ tone = "muted", children, style, ...rest }: ChipProps) {
  const t = CHIP_TONES[tone] || CHIP_TONES.muted;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "0.375rem 0.75rem",
        borderRadius: "var(--radius-pill)",
        background: t.bg,
        color: t.fg,
        border: t.border ? "1px solid var(--ta-line)" : "1px solid transparent",
        fontSize: "var(--text-2xs)",
        fontWeight: "var(--weight-medium)",
        lineHeight: 1.3,
        maxWidth: "100%",
        ...style,
      }}
      {...rest}
    >
      {children}
    </span>
  );
}

/* ---- Avatar — 会话身份用的 rounded-2xl 字母块 + 可选状态点 ---- */
type AvatarStatus = "queued" | "running" | "success" | "failed" | "idle";
const DOT_COLORS: Record<AvatarStatus, string> = {
  queued: "var(--ta-queued-dot)",
  running: "var(--ta-running-dot)",
  success: "var(--ta-success-dot)",
  failed: "var(--ta-failed-dot)",
  idle: "var(--ta-neutral-dot)",
};
export interface AvatarProps extends React.HTMLAttributes<HTMLSpanElement> {
  label?: string;
  status?: string;
  size?: number;
}
export function Avatar({ label = "", status, size = 36, style, ...rest }: AvatarProps) {
  const text = String(label).slice(0, 2).toUpperCase();
  const dot = status ? DOT_COLORS[status as AvatarStatus] || DOT_COLORS.idle : undefined;
  return (
    <span style={{ position: "relative", display: "inline-flex", flex: "none", ...style }} {...rest}>
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: size,
          height: size,
          borderRadius: "var(--radius-lg)",
          background: "var(--ta-line-faint)",
          color: "var(--ta-muted)",
          fontSize: "var(--text-2xs)",
          fontWeight: "var(--weight-semibold)",
          textTransform: "uppercase",
          letterSpacing: "0.14em",
        }}
      >
        {text}
      </span>
      {dot && (
        <span
          style={{
            position: "absolute",
            top: -2,
            right: -2,
            width: 10,
            height: 10,
            borderRadius: "9999px",
            background: dot,
            boxShadow: "0 0 0 2px #fff",
          }}
        />
      )}
    </span>
  );
}
