"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// 渲染 Agent 输出的 markdown（表格/代码/列表/加粗…）。dark 用于深色气泡。
export function Markdown({ text, dark = false }: { text: string; dark?: boolean }) {
  return (
    <div className={dark ? "chat-md-dark" : "chat-md"}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text || ""}</ReactMarkdown>
    </div>
  );
}
