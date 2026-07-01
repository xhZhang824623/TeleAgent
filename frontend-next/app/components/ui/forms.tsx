import React from "react";

/* ---- Input — token 字段，可选 label，mono 用于路径/密钥 ---- */
export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: React.ReactNode;
  mono?: boolean;
}
export function Input({ label, mono = false, className = "", style, ...rest }: InputProps) {
  const field = (
    <input className={`ta-field${mono ? " ta-field-mono" : ""} ${className}`.trim()} style={style} {...rest} />
  );
  if (!label) return field;
  return (
    <label style={{ display: "block" }}>
      <span style={{ display: "block", fontSize: "var(--text-sm)", fontWeight: "var(--weight-medium)", color: "var(--ta-ink-soft)", marginBottom: "0.25rem" }}>
        {label}
      </span>
      {field}
    </label>
  );
}

/* ---- Textarea — token 字段，纵向可拉伸 ---- */
export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: React.ReactNode;
}
export function Textarea({ label, rows = 3, className = "", style, ...rest }: TextareaProps) {
  const field = (
    <textarea
      rows={rows}
      className={`ta-field ${className}`.trim()}
      style={{ resize: "vertical", ...style }}
      {...rest}
    />
  );
  if (!label) return field;
  return (
    <label style={{ display: "block" }}>
      <span style={{ display: "block", fontSize: "var(--text-sm)", fontWeight: "var(--weight-medium)", color: "var(--ta-ink-soft)", marginBottom: "0.25rem" }}>
        {label}
      </span>
      {field}
    </label>
  );
}

/* ---- Select — token 字段 + 统一下拉箭头（.select-clean） ---- */
export interface SelectProps extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, "size"> {
  label?: React.ReactNode;
  size?: "sm" | "md";
}
export function Select({ label, size = "md", className = "", style, children, ...rest }: SelectProps) {
  const field = (
    <select
      className={`ta-field select-clean${size === "sm" ? " ta-field-sm" : ""} ${className}`.trim()}
      style={style}
      {...rest}
    >
      {children}
    </select>
  );
  if (!label) return field;
  return (
    <label style={{ display: "block" }}>
      <span style={{ display: "block", fontSize: "var(--text-sm)", fontWeight: "var(--weight-medium)", color: "var(--ta-ink-soft)", marginBottom: "0.25rem" }}>
        {label}
      </span>
      {field}
    </label>
  );
}
