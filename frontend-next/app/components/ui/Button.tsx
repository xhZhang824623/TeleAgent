"use client";

import React from "react";

type Variant = "primary" | "accent" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

const SIZES: Record<Size, React.CSSProperties> = {
  sm: { padding: "0.375rem 0.875rem", fontSize: "var(--text-xs)" },
  md: { padding: "0.625rem 1.25rem", fontSize: "var(--text-sm)" },
  lg: { padding: "0.75rem 1.25rem", fontSize: "var(--text-sm)" },
};

const VARIANTS: Record<Variant, React.CSSProperties> = {
  primary: { background: "var(--ta-ink)", color: "#fff", border: "1px solid transparent" },
  accent: { background: "var(--ta-accent)", color: "#fff", border: "1px solid transparent" },
  secondary: { background: "var(--ta-panel)", color: "var(--ta-ink-soft)", border: "1px solid var(--ta-line)" },
  ghost: { background: "transparent", color: "var(--ta-ink-soft)", border: "1px solid transparent" },
  danger: { background: "var(--ta-failed-bg)", color: "var(--ta-failed-fg)", border: "1px solid var(--ta-failed-border)" },
};

const HOVER_BG: Record<Variant, string> = {
  primary: "var(--ta-accent-hover)",
  accent: "var(--ta-accent-hover)",
  secondary: "var(--ta-line-faint)",
  ghost: "var(--ta-accent-soft)",
  danger: "var(--ta-failed-bg-strong)",
};

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  block?: boolean;
}

/**
 * Button — TeleAgent 的胶囊按钮。variant：primary(ink，hover→indigo，主 CTA)、
 * accent(indigo 实色)、secondary(白底描边)、ghost、danger(红字红底)。
 */
export function Button({
  variant = "primary",
  size = "md",
  block = false,
  disabled = false,
  type = "button",
  children,
  style,
  ...rest
}: ButtonProps) {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      type={type}
      disabled={disabled}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: block ? "flex" : "inline-flex",
        width: block ? "100%" : undefined,
        alignItems: "center",
        justifyContent: "center",
        gap: "0.5rem",
        borderRadius: "var(--radius-pill)",
        fontWeight: "var(--weight-medium)",
        lineHeight: 1.2,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        transition: "background-color var(--dur-fast) var(--ease-standard), border-color var(--dur-fast)",
        ...SIZES[size],
        ...VARIANTS[variant],
        ...(hover && !disabled ? { background: HOVER_BG[variant] } : null),
        ...style,
      }}
      {...rest}
    >
      {children}
    </button>
  );
}

export interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** 圆形（默认）或胶囊 */
  shape?: "circle" | "pill";
}

/** IconButton — 圆形/胶囊的图标按钮（菜单、关闭等），白底描边、hover 浅灰。 */
export function IconButton({ shape = "circle", style, children, ...rest }: IconButtonProps) {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      type="button"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: shape === "circle" ? 36 : undefined,
        height: 36,
        padding: shape === "pill" ? "0 0.75rem" : undefined,
        borderRadius: "var(--radius-pill)",
        border: "1px solid var(--ta-line)",
        background: hover ? "var(--ta-line-faint)" : "var(--ta-panel)",
        color: "var(--ta-ink-soft)",
        cursor: "pointer",
        transition: "background-color var(--dur-fast)",
        ...style,
      }}
      {...rest}
    >
      {children}
    </button>
  );
}
